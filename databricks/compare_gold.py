"""Check that DuckDB and Databricks produce the same silver and gold results.

    .venv-databricks/Scripts/python databricks/compare_gold.py

Compares the row count of every silver/gold table and the key business figures.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import duckdb
from databricks.sdk import WorkspaceClient

ROOT = Path(__file__).resolve().parent.parent
CATALOG = os.environ.get("RI_DATABRICKS_CATALOG", "workspace")

FIGURES = {   # name -> SQL with {s} = schema prefix of the engine ('' for DuckDB, 'workspace.' for Databricks)
    "net revenue EUR": "select round(sum(net_revenue_eur), 2) from {s}gold.fct_sales",
    "gross margin EUR": "select round(sum(margin_eur), 2) from {s}gold.fct_sales",
    "units sold": "select sum(quantity) from {s}gold.fct_sales where not is_return",
    "sales lines quarantined": "select count(*) from {s}silver.silver_sales_quarantine",
    "fuzzy title matches": "select count(*) from {s}silver.int_sales_matched where match_method = 'title_fuzzy'",
    "money at risk in invoices EUR": "select round(sum(amount_at_risk_eur), 2) from {s}gold.mart_invoice_audit",
    "invoices needing action": "select count(*) from {s}gold.mart_invoice_audit where audit_status <> 'OK'",
    "competitor price days (not stale)": "select count(*) from {s}gold.fct_competitor_price_daily where not is_stale",
    "avg competitor price EUR": "select round(avg(price_eur), 4) from {s}gold.fct_competitor_price_daily",
    "data-quality rows failed": "select sum(rows_failed) from {s}gold.mart_data_quality",
}


def remote(w: WorkspaceClient, sql: str) -> list[list]:
    warehouse_id = os.environ["DATABRICKS_HTTP_PATH"].rstrip("/").rsplit("/", 1)[-1]
    r = w.statement_execution.execute_statement(statement=sql, warehouse_id=warehouse_id, wait_timeout="50s")
    if r.status.error:
        raise RuntimeError(f"{r.status.error.message}\n{sql}")
    return r.result.data_array or []


def norm(v):
    if v is None:
        return None
    try:
        return Decimal(str(v)).normalize()
    except Exception:
        return str(v)


def main() -> int:
    w = WorkspaceClient()
    con = duckdb.connect(str(ROOT / "data" / "warehouse" / "retail.duckdb"), read_only=True)
    tables = [r for r in con.execute("select table_schema, table_name from information_schema.tables "
                                     "where table_schema in ('silver', 'gold') order by 1, 2").fetchall()]
    ok = True
    print(f"{'table':<44}{'DuckDB':>10}{'Databricks':>12}")
    for schema, table in tables:
        n_local = con.execute(f"select count(*) from {schema}.{table}").fetchone()[0]
        n_dbx = int(remote(w, f"select count(*) from {CATALOG}.{schema}.{table}")[0][0])
        same = n_local == n_dbx
        ok &= same
        print(f"{schema + '.' + table:<44}{n_local:>10,}{n_dbx:>12,}  {'OK' if same else 'DIFFERENT'}")
    print(f"\n{'business figure':<44}{'DuckDB':>16}{'Databricks':>16}")
    for name, sql in FIGURES.items():
        a = norm(con.execute(sql.format(s="")).fetchone()[0])
        b = norm(remote(w, sql.format(s=f"{CATALOG}."))[0][0])
        same = a == b
        ok &= same
        print(f"{name:<44}{str(a):>16}{str(b):>16}  {'OK' if same else 'DIFFERENT'}")
    print("\nIDENTICAL results on both engines" if ok else "\nDIFFERENCES FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
