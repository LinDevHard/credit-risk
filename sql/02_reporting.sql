-- Витрина и метрики в схеме reporting. Только CREATE OR REPLACE VIEW — повторный запуск безопасен.

CREATE SCHEMA IF NOT EXISTS reporting;

-- 1. Очищенная витрина: один ряд на (дата среза, кредит), сумма в тенге по курсу НА ДАТУ СРЕЗА,
--    списанные кредиты исключены, риск-флаги и провизии посчитаны на уровне кредита.
CREATE OR REPLACE VIEW reporting.v_loan_snapshot AS
WITH dedup AS (
    -- в raw 30 полных дублей на каждую дату — берём DISTINCT
    SELECT DISTINCT * FROM raw.portfolio
),
fx AS (
    SELECT date, currency, rate_kzt FROM raw.fx
    UNION ALL
    SELECT DISTINCT report_date, 'KZT', 1 FROM raw.portfolio
)
SELECT
    p.report_date,
    p.loan_id,
    p.borrower_id,
    p.group_id,
    trim(p.sector)  AS sector,
    trim(p.program) AS program,
    p.currency,
    p.issue_date,
    p.maturity_date,
    p.principal_outstanding                    AS principal_ccy,
    fx.rate_kzt,
    p.principal_outstanding * fx.rate_kzt      AS principal_kzt,
    p.days_past_due,
    p.restructured = 1                          AS is_restructured,
    CASE
        WHEN p.days_past_due = 0  THEN '0'
        WHEN p.days_past_due <= 30 THEN '1-30'
        WHEN p.days_past_due <= 90 THEN '31-90'
        ELSE '90+'
    END                                         AS dpd_bucket,
    p.days_past_due > 30                        AS is_par30,
    p.days_past_due > 90                        AS is_par90,
    (p.days_past_due > 90 OR p.restructured = 1) AS is_npl,
    CASE
        WHEN p.days_past_due > 90 OR p.restructured = 1 THEN 0.50   -- NPL имеет приоритет
        WHEN p.days_past_due > 30                       THEN 0.10
        ELSE 0.01
    END                                         AS provision_rate,
    p.principal_outstanding * fx.rate_kzt *
    CASE
        WHEN p.days_past_due > 90 OR p.restructured = 1 THEN 0.50
        WHEN p.days_past_due > 30                       THEN 0.10
        ELSE 0.01
    END                                         AS provision_kzt,
    -- суммовые колонки под меры семантической модели (доля = sum(x_kzt) / sum(principal_kzt))
    CASE WHEN p.days_past_due > 30 THEN p.principal_outstanding * fx.rate_kzt ELSE 0 END AS par30_kzt,
    CASE WHEN p.days_past_due > 90 THEN p.principal_outstanding * fx.rate_kzt ELSE 0 END AS par90_kzt,
    CASE WHEN p.days_past_due > 90 OR p.restructured = 1
         THEN p.principal_outstanding * fx.rate_kzt ELSE 0 END                         AS npl_kzt
FROM dedup p
LEFT JOIN fx ON fx.date = p.report_date AND fx.currency = p.currency   -- LEFT: пропуск курса ловим в v_data_quality
WHERE p.written_off = 0;

-- 2. Концентрация: доля топ-10 групп связанных заёмщиков.
CREATE OR REPLACE VIEW reporting.v_group_exposure AS
SELECT
    report_date,
    group_id,
    sum(principal_kzt) AS exposure_kzt,
    sum(principal_kzt) / sum(sum(principal_kzt)) OVER (PARTITION BY report_date) AS share,
    row_number() OVER (PARTITION BY report_date ORDER BY sum(principal_kzt) DESC, group_id) AS rnk
FROM reporting.v_loan_snapshot
GROUP BY report_date, group_id;

-- 3. Метрики на каждую дату среза (широкий формат).
CREATE OR REPLACE VIEW reporting.v_metrics AS
SELECT
    s.report_date,
    count(*)                                                    AS loans,
    sum(s.principal_kzt)                                        AS portfolio_kzt,
    sum(s.principal_kzt) FILTER (WHERE s.is_par30) / sum(s.principal_kzt) AS par30,
    sum(s.principal_kzt) FILTER (WHERE s.is_par90) / sum(s.principal_kzt) AS par90,
    sum(s.principal_kzt) FILTER (WHERE s.is_npl)   / sum(s.principal_kzt) AS npl,
    sum(s.provision_kzt)                                        AS provisions_kzt,
    sum(s.provision_kzt) / sum(s.principal_kzt)                 AS provision_ratio,
    (SELECT sum(g.share) FROM reporting.v_group_exposure g
      WHERE g.report_date = s.report_date AND g.rnk <= 10)     AS top10_concentration
FROM reporting.v_loan_snapshot s
GROUP BY s.report_date;

-- 4. Метрики в длинном формате с изменением к 01.09.2026 (для KPI-карточек дашборда).
CREATE OR REPLACE VIEW reporting.v_metrics_change AS
WITH long AS (
    SELECT report_date, m.*
    FROM reporting.v_metrics v
    CROSS JOIN LATERAL (VALUES
        (1, 'portfolio_kzt',       'Портфель, ₸',          v.portfolio_kzt,       'amount'),
        (2, 'par30',               'PAR30',                 v.par30,               'pct'),
        (3, 'par90',               'PAR90',                 v.par90,               'pct'),
        (4, 'npl',                 'NPL',                   v.npl,                 'pct'),
        (5, 'provisions_kzt',      'Провизии, ₸',          v.provisions_kzt,      'amount'),
        (6, 'top10_concentration', 'Концентрация топ-10',   v.top10_concentration, 'pct')
    ) AS m(ord, metric, label, value, kind)
)
SELECT
    cur.ord, cur.metric, cur.label, cur.kind,
    base.value                AS value_2026_09_01,
    cur.value                 AS value_2026_10_01,
    cur.value - base.value    AS delta_abs,         -- для долей это п.п. в долях единицы
    CASE WHEN cur.kind = 'amount' THEN cur.value / base.value - 1 END AS delta_rel
FROM long cur
JOIN long base ON base.metric = cur.metric AND base.report_date = DATE '2026-09-01'
WHERE cur.report_date = DATE '2026-10-01';

-- 5. Разрезы по отрасли и программе на обе даты.
CREATE OR REPLACE VIEW reporting.v_breakdown AS
SELECT
    report_date,
    dim,
    dim_value,
    sum(principal_kzt)                                       AS portfolio_kzt,
    sum(principal_kzt) / sum(sum(principal_kzt)) OVER (PARTITION BY report_date, dim) AS portfolio_share,
    sum(principal_kzt) FILTER (WHERE is_par30) / sum(principal_kzt) AS par30,
    sum(principal_kzt) FILTER (WHERE is_par90) / sum(principal_kzt) AS par90,
    sum(principal_kzt) FILTER (WHERE is_npl)   / sum(principal_kzt) AS npl,
    sum(provision_kzt)                                       AS provisions_kzt
FROM reporting.v_loan_snapshot
CROSS JOIN LATERAL (VALUES ('sector', sector), ('program', program)) d(dim, dim_value)
GROUP BY report_date, dim, dim_value;

-- 6. Контроль качества данных: что отфильтровано и почему.
CREATE OR REPLACE VIEW reporting.v_data_quality AS
SELECT
    p.report_date,
    count(*)                                              AS raw_rows,
    count(*) - count(DISTINCT p.*)                        AS exact_duplicates,
    count(DISTINCT p.loan_id) FILTER (WHERE p.written_off = 1) AS written_off_loans,
    (SELECT count(*) FROM reporting.v_loan_snapshot s
      WHERE s.report_date = p.report_date)                AS mart_rows,
    (SELECT count(*) FROM reporting.v_loan_snapshot s
      WHERE s.report_date = p.report_date AND s.rate_kzt IS NULL) AS missing_fx_rows
FROM raw.portfolio p
GROUP BY p.report_date;
