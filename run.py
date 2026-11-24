"""Command line entry point.

    python run.py generate     # (re)create the fictional source files in data/raw
    python run.py ingest       # load new/changed raw files into the bronze layer (incremental)
    python run.py transform    # dbt build: silver + gold models and all data tests
    python run.py all          # generate + ingest + transform
    python run.py scrape       # optional: real scrape of books.toscrape.com into data/raw/competitor
    python run.py report       # print the main gold tables (or: report invoices | kpis | prices | quality)
    python run.py report --sql "select ..."   # any query against the warehouse
    python run.py ui           # browse every table in DuckDB's web UI (http://localhost:4213)
    python run.py dashboard    # build docs/index.html: the interactive dashboard (self-contained)
    python run.py powerbi      # export gold to Parquet + generate the Power BI project (powerbi/*.pbip)
    python run.py docs         # regenerate docs/data_dictionary.md (tables, columns, types, keys)
    python run.py docs --pdf   # ... and also docs/data_dictionary.html + .pdf (A4 landscape)
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
    env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir),
           "RI_DB_PATH": str(config.DB_PATH), "RI_RAW_DIR": config.RAW.as_posix()}
    cmd = [exe, "build", "--project-dir", str(dbt_dir)]
    if os.environ.get("RI_SIMULATION") == "0":      # real (or deliberately broken) data: skip simulation-only tests
        cmd += ["--vars", "{simulation: false}"]
    if "RI_DATA_DIR" in os.environ:                 # isolated run: keep dbt's artifacts and logs out of the repo
        cmd += ["--target-path", str(config.DATA / "dbt_target"), "--log-path", str(config.DATA / "dbt_logs")]
    res = subprocess.run(cmd, env=env, cwd=config.ROOT)
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
    ap.add_argument("command", choices=["generate", "ingest", "transform", "all", "scrape", "report", "ui", "docs", "dashboard", "screenshots", "powerbi"])
    ap.add_argument("report", nargs="?", choices=list(REPORTS), help="report: show only this one")
    ap.add_argument("--sql", help="report: run your own query against the warehouse")
    ap.add_argument("--pdf", action="store_true", help="docs: also export a styled HTML page and a PDF")
    args = ap.parse_args()
    if args.command == "report":
        cmd_report(args.report, args.sql)
        return
    if args.command == "ui":
        cmd_ui()
        return
    if args.command == "dashboard":
        from pipeline import dashboard
        print("dashboard written to", dashboard.build())
        return
    if args.command == "powerbi":
        from pipeline import powerbi
        print(powerbi.build())
        print("Open powerbi/RetailIntelligence.pbip in Power BI Desktop and click Home > Refresh.")
        return
    if args.command == "screenshots":
        from pipeline import screenshots
        for dark in (False, True):
            for p in screenshots.capture(dark=dark):
                print(p.relative_to(config.ROOT))
        return
    if args.command == "docs":
        from pipeline import data_dictionary
        md = data_dictionary.generate()
        if args.pdf:
            from pipeline import doc_export
            html_path, pdf_path = doc_export.export(md)
            print(f"{html_path.relative_to(config.ROOT)}" + (f" and {pdf_path.relative_to(config.ROOT)}" if pdf_path else ""))
        return
    if args.command in ("generate", "all"):
        cmd_generate()
    if args.command in ("ingest", "all"):
        cmd_ingest()
    if args.command in ("transform", "all"):
        cmd_transform()
    if args.command == "all" and "RI_DATA_DIR" not in os.environ:   # the dashboard lives in docs/ of the repo
        from pipeline import dashboard
        log.info("dashboard written to %s", dashboard.build())
    if args.command == "scrape":
        cmd_scrape()


if __name__ == "__main__":
    main()
