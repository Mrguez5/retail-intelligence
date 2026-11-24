"""Command line entry point.

    python run.py generate     # (re)create the fictional source files in data/raw
    python run.py ingest       # load new/changed raw files into the bronze layer (incremental)
    python run.py transform    # dbt build: silver + gold models and all data tests
    python run.py all          # generate + ingest + transform
    python run.py scrape       # optional: real scrape of books.toscrape.com into data/raw/competitor
    python run.py report       # print the main gold tables (or: report invoices | kpis | prices | quality)
    python run.py report --sql "select ..."   # any query against the warehouse
    python run.py ui           # browse every table in DuckDB's web UI (http://localhost:4213)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time

from pipeline import config

log = logging.getLogger("run")


def cmd_generate() -> None:
    from generators import generate_all
    t = time.perf_counter()
    report = generate_all()
    log.info("generated sources in %.1fs\n%s", time.perf_counter() - t, json.dumps(report, indent=2, default=str))


def cmd_ingest() -> None:
    from pipeline import bronze
    bronze.ingest_all()


def cmd_transform() -> None:
    dbt_dir = config.ROOT / "dbt"
    exe = os.path.join(os.path.dirname(sys.executable), "dbt")
    env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir)}
    # run from the repo root: the profile and vars use paths relative to it
    res = subprocess.run([exe, "build", "--project-dir", str(dbt_dir)], env=env, cwd=config.ROOT)
    if res.returncode != 0:
        sys.exit(res.returncode)


REPORTS = {
    "kpis": ("Monthly KPIs", "select year_month, net_revenue_eur, margin_pct, units_sold, "
             "weighted_price_gap_pct, invoice_amount_at_risk_eur from gold.mart_kpis_monthly"),
    "invoices": ("Invoices that need action", "select supplier_code, invoice_number, invoice_date, audit_status, "
                 "amount_at_risk_eur from gold.mart_invoice_audit where audit_status <> 'OK' "
                 "order by amount_at_risk_eur desc"),
    "prices": ("Price position vs competitor", "select price_band, count(*) as product_weeks, "
               "round(avg(units), 2) as avg_units_per_week, round(avg(price_gap_pct), 3) as avg_gap "
               "from gold.mart_price_position_weekly where price_band <> 'no sales' "
               "group by 1 order by avg_gap"),
    "quality": ("Data quality checks", "select source, rule, rows_checked, rows_failed, action "
                "from gold.mart_data_quality"),
}


def cmd_report(name: str | None, sql: str | None) -> None:
    import duckdb
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    if sql:
        print(con.sql(sql))
        return
    for key, (title, query) in REPORTS.items():
        if name in (None, key):
            print(f"\n== {title}")
            con.sql(query).show(max_width=160, max_rows=40)


def cmd_ui() -> None:
    """Browse the warehouse in DuckDB's web UI. The database is locked while it runs."""
    import duckdb
    con = duckdb.connect(str(config.DB_PATH))
    con.sql("CALL start_ui()")
    print("\nDuckDB UI running at http://localhost:4213")
    print("While it runs, ingest/transform cannot write to the database.")
    try:
        input("Press Enter (or Ctrl+C) here to stop it.\n")
    except (KeyboardInterrupt, EOFError):
        pass
    con.close()
    print("UI stopped, database released.")


def cmd_scrape() -> None:
    from pipeline import scraper
    scraper.run_snapshot()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["generate", "ingest", "transform", "all", "scrape", "report", "ui"])
    ap.add_argument("report", nargs="?", choices=list(REPORTS), help="report: show only this one")
    ap.add_argument("--sql", help="report: run your own query against the warehouse")
    args = ap.parse_args()
    if args.command == "report":
        cmd_report(args.report, args.sql)
        return
    if args.command == "ui":
        cmd_ui()
        return
    if args.command in ("generate", "all"):
        cmd_generate()
    if args.command in ("ingest", "all"):
        cmd_ingest()
    if args.command in ("transform", "all"):
        cmd_transform()
    if args.command == "scrape":
        cmd_scrape()


if __name__ == "__main__":
    main()
