#!/usr/bin/env bash
# Поднимает Postgres 16 в Docker (если ещё нет), грузит raw и создаёт вьюхи reporting,
# затем выгружает данные для дашборда. Можно запускать сколько угодно раз.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=credit-risk-pg

if ! docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker run -d --name "$NAME" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=risk \
    -p 5433:5432 -v "$PWD":/work postgres:16 >/dev/null
fi
docker start "$NAME" >/dev/null
until docker exec "$NAME" pg_isready -U postgres -d risk >/dev/null 2>&1; do sleep 1; done

PSQL=(docker exec -i "$NAME" psql -U postgres -d risk -v ON_ERROR_STOP=1 -q)
"${PSQL[@]}" -v datadir=/work/data -f /work/sql/01_raw.sql
"${PSQL[@]}" -f /work/sql/02_reporting.sql

# Выгрузка вьюх в JSON для статичного дашборда
for v in v_metrics v_metrics_change v_breakdown v_data_quality; do
  "${PSQL[@]}" -At -c "SELECT coalesce(json_agg(t), '[]') FROM reporting.$v t" > "dashboard/data/$v.json"
done
"${PSQL[@]}" -At -c "SELECT json_agg(t) FROM (SELECT * FROM reporting.v_group_exposure WHERE rnk <= 10 ORDER BY report_date, rnk) t" > dashboard/data/v_top_groups.json
"${PSQL[@]}" -At -c "SELECT json_agg(t) FROM (SELECT report_date, dpd_bucket, sum(principal_kzt) AS portfolio_kzt FROM reporting.v_loan_snapshot GROUP BY 1,2 ORDER BY 1,2) t" > dashboard/data/v_dpd_buckets.json

echo "OK. Метрики:"
"${PSQL[@]}" -c "SELECT * FROM reporting.v_metrics ORDER BY report_date"
