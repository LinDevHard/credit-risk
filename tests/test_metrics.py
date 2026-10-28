"""Независимая проверка метрик: считаем всё заново из CSV на чистом Python
и сравниваем с вьюхами reporting.* в Postgres. Только стандартная библиотека.

Запуск: python3 -m unittest -v tests/test_metrics.py
(перед этим ./scripts/run.sh — контейнер credit-risk-pg должен быть запущен)
"""
import csv
import json
import subprocess
import unittest
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATES = ("2026-09-01", "2026-10-01")
CONTAINER = "credit-risk-pg"


def psql_json(sql: str):
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-d", "risk", "-At",
         "-c", f"SELECT coalesce(json_agg(t), '[]') FROM ({sql}) t"],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out, parse_float=Decimal)


def load_reference():
    """Эталонный расчёт по определениям из задания."""
    fx = {(r["date"], r["currency"]): Decimal(r["rate_kzt"])
          for r in csv.DictReader((ROOT / "data/fx.csv").read_text(encoding="utf-8").splitlines())}
    rows = list(csv.DictReader((ROOT / "data/portfolio.csv").read_text(encoding="utf-8").splitlines()))
    seen, loans = set(), defaultdict(list)
    for r in rows:
        key = tuple(r.values())
        if key in seen:                     # полный дубль строки
            continue
        seen.add(key)
        if r["written_off"] == "1":         # списанные не входят в портфель
            continue
        d = r["report_date"]
        rate = Decimal(1) if r["currency"] == "KZT" else fx[(d, r["currency"])]  # курс на дату среза
        amt = Decimal(r["principal_outstanding"]) * rate
        dpd, restr = int(r["days_past_due"]), r["restructured"] == "1"
        npl = dpd > 90 or restr
        prov = Decimal("0.50") if npl else Decimal("0.10") if dpd > 30 else Decimal("0.01")
        loans[d].append(dict(r, amt=amt, dpd=dpd, npl=npl, prov=amt * prov))

    ref = {}
    for d in DATES:
        L = loans[d]
        total = sum(x["amt"] for x in L)
        groups = defaultdict(Decimal)
        for x in L:
            groups[x["group_id"]] += x["amt"]
        top10 = sum(sorted(groups.values(), reverse=True)[:10])
        ref[d] = dict(
            loans=len(L),
            portfolio_kzt=total,
            par30=sum(x["amt"] for x in L if x["dpd"] > 30) / total,
            par90=sum(x["amt"] for x in L if x["dpd"] > 90) / total,
            npl=sum(x["amt"] for x in L if x["npl"]) / total,
            provisions_kzt=sum(x["prov"] for x in L),
            top10_concentration=top10 / total,
            by=dict(
                sector=_by(L, "sector"),
                program=_by(L, "program"),
            ),
        )
    return ref, rows


def _by(L, col):
    acc = defaultdict(Decimal)
    for x in L:
        acc[x[col]] += x["amt"]
    return dict(acc)


class MetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref, cls.raw_rows = load_reference()
        cls.db = {r["report_date"]: r for r in psql_json("SELECT * FROM reporting.v_metrics")}

    def assertClose(self, a, b, tol=Decimal("1e-9"), msg=None):
        a, b = Decimal(str(a)), Decimal(str(b))
        self.assertLessEqual(abs(a - b), tol * max(Decimal(1), abs(b)), msg)

    def test_metrics_match_reference(self):
        for d in DATES:
            for m in ("loans", "portfolio_kzt", "par30", "par90", "npl",
                      "provisions_kzt", "top10_concentration"):
                with self.subTest(date=d, metric=m):
                    self.assertClose(self.db[d][m], self.ref[d][m])

    def test_breakdowns_match_and_sum_to_total(self):
        rows = psql_json("SELECT * FROM reporting.v_breakdown")
        for r in rows:
            with self.subTest(date=r["report_date"], dim=r["dim"], v=r["dim_value"]):
                self.assertClose(r["portfolio_kzt"],
                                 self.ref[r["report_date"]]["by"][r["dim"]][r["dim_value"]])
        for d in DATES:
            for dim in ("sector", "program"):
                s = sum(r["portfolio_kzt"] for r in rows if r["report_date"] == d and r["dim"] == dim)
                self.assertClose(s, self.ref[d]["portfolio_kzt"])

    def test_change_view_consistent(self):
        for r in psql_json("SELECT * FROM reporting.v_metrics_change"):
            m = r["metric"]
            with self.subTest(metric=m):
                self.assertClose(r["value_2026_09_01"], self.ref["2026-09-01"][m])
                self.assertClose(r["value_2026_10_01"], self.ref["2026-10-01"][m])
                self.assertClose(r["delta_abs"], self.ref["2026-10-01"][m] - self.ref["2026-09-01"][m])

    def test_invariants(self):
        for d in DATES:
            m = self.ref[d]
            self.assertLessEqual(m["par90"], m["par30"])   # PAR90 ⊂ PAR30
            self.assertLessEqual(m["par90"], m["npl"])     # NPL ⊇ просрочка 90+
            self.assertTrue(0 < m["top10_concentration"] < 1)

    def test_data_quality(self):
        dq = {r["report_date"]: r for r in psql_json("SELECT * FROM reporting.v_data_quality")}
        for d in DATES:
            self.assertEqual(dq[d]["missing_fx_rows"], 0, "нет курса на дату среза")
            self.assertEqual(dq[d]["mart_rows"], self.ref[d]["loans"])
        # витрина уникальна по (дата, кредит)
        dup = psql_json("SELECT report_date, loan_id FROM reporting.v_loan_snapshot "
                        "GROUP BY 1,2 HAVING count(*) > 1")
        self.assertEqual(dup, [])

    def test_fx_rate_is_on_report_date(self):
        # 31.08 курс 473.47, 01.09 — 478.35: ловушка «курс предыдущего дня»
        r = psql_json("SELECT DISTINCT report_date, rate_kzt FROM reporting.v_loan_snapshot "
                      "WHERE currency = 'USD' ORDER BY 1")
        self.assertEqual([(x["report_date"], x["rate_kzt"]) for x in r],
                         [("2026-09-01", Decimal("478.35")), ("2026-10-01", Decimal("482.10"))])

    def test_semantic_model_columns_exist(self):
        import re
        y = (ROOT / "model/credit_portfolio.yml").read_text(encoding="utf-8")
        src = re.search(r"^source: reporting\.(\w+)", y, re.M).group(1)
        cols = {r["column_name"] for r in psql_json(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = 'reporting' AND table_name = '{src}'")}
        self.assertEqual([c for c in re.findall(r"column: (\w+)", y) if c not in cols], [])
        # меры-доли из YAML дают те же цифры, что v_metrics
        r = {x["report_date"]: x for x in psql_json(
            "SELECT report_date, sum(par30_kzt)/sum(principal_kzt) par30, "
            "sum(npl_kzt)/sum(principal_kzt) npl FROM reporting.v_loan_snapshot GROUP BY 1")}
        for d in DATES:
            self.assertClose(r[d]["par30"], self.ref[d]["par30"])
            self.assertClose(r[d]["npl"], self.ref[d]["npl"])

    def test_idempotent_rerun(self):
        before = psql_json("SELECT * FROM reporting.v_metrics ORDER BY report_date")
        for f in ("01_raw.sql", "02_reporting.sql"):
            subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-d", "risk",
                            "-q", "-v", "ON_ERROR_STOP=1", "-v", "datadir=/work/data",
                            "-f", f"/work/sql/{f}"], check=True, capture_output=True)
        after = psql_json("SELECT * FROM reporting.v_metrics ORDER BY report_date")
        self.assertEqual(before, after)
        self.assertEqual(psql_json("SELECT count(*) AS n FROM raw.portfolio")[0]["n"],
                         len(self.raw_rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
