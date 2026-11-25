"""Check the Databricks bronze layer against the local DuckDB one: rows per table and per file.

    .venv-databricks/Scripts/python databricks/compare_bronze.py

Needs DATABRICKS_HOST, DATABRICKS_TOKEN and DATABRICKS_HTTP_PATH (the SQL warehouse), and a
local warehouse built with `python run.py all`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
from databricks.sdk import WorkspaceClient

ROOT = Path(__file__).resolve().parent.parent
CATALOG = os.environ.get("RI_DATABRICKS_CATALOG", "workspace")
TABLES = ["erp_products", "erp_price_changes", "contract_prices", "fx_rates", "competitor_snapshots",
          "sales_web", "sales_marketplace", "sales_store", "invoice_headers", "invoice_lines"]


def remote(w: WorkspaceClient, sql: str) -> list[list]:
    warehouse_id = os.environ["DATABRICKS_HTTP_PATH"].rstrip("/").rsplit("/", 1)[-1]
    r = w.statement_execution.execute_statement(statement=sql, warehouse_id=warehouse_id, wait_timeout="50s")
    if r.status.error:
        raise RuntimeError(r.status.error.message)
    return r.result.data_array or []


def main() -> int:
    w = WorkspaceClient()
    con = duckdb.connect(str(ROOT / "data" / "warehouse" / "retail.duckdb"), read_only=True)
    union = " union all ".join(f"select '{t}', _source_file, count(*) from {{s}}.{t} group by 2" for t in TABLES)
    local = {(t, f): n for t, f, n in con.execute(union.format(s="bronze")).fetchall()}
    dbx = {(t, f): int(n) for t, f, n in remote(w, union.format(s=f"{CATALOG}.bronze"))}

    ok = True
    print(f"{'table':<22}{'files L/D':>12}{'rows local':>13}{'rows Databricks':>17}")
    for t in TABLES:
        lf = {f: n for (tt, f), n in local.items() if tt == t}
        df = {f: n for (tt, f), n in dbx.items() if tt == t}
        same = lf == df
        ok &= same
        print(f"{t:<22}{len(lf):>5} / {len(df):<5}{sum(lf.values()):>13,}{sum(df.values()):>17,}  {'OK' if same else 'DIFFERENT'}")
        for f in sorted(set(lf) | set(df)):
            if lf.get(f) != df.get(f):
                print(f"    {f}: local {lf.get(f)} vs Databricks {df.get(f)}")
    print("\nIDENTICAL: same files and same row count per file in every table" if ok else "\nDIFFERENCES FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
