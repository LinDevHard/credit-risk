-- Загрузка исходных CSV в схему raw. Идемпотентно: таблицы создаются один раз,
-- при каждом запуске очищаются и загружаются заново.
-- Запуск: psql -v ON_ERROR_STOP=1 -v datadir=/work/data -f sql/01_raw.sql

CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.portfolio (
    report_date           date        NOT NULL,
    loan_id               text        NOT NULL,
    borrower_id           text,
    group_id              text,
    sector                text,
    program               text,
    currency              char(3)     NOT NULL,
    issue_date            date,
    maturity_date         date,
    principal_outstanding numeric(20,2) NOT NULL,  -- в валюте кредита
    days_past_due         integer     NOT NULL,
    restructured          smallint    NOT NULL,
    written_off           smallint    NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.fx (
    date      date    NOT NULL,
    currency  char(3) NOT NULL,
    rate_kzt  numeric(12,4) NOT NULL
);

TRUNCATE raw.portfolio, raw.fx;

\set portfolio_file :datadir '/portfolio.csv'
\set fx_file :datadir '/fx.csv'
COPY raw.portfolio FROM :'portfolio_file' WITH (FORMAT csv, HEADER true);
COPY raw.fx        FROM :'fx_file'        WITH (FORMAT csv, HEADER true);
