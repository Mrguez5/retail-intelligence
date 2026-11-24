"""Build the dashboard: one self-contained HTML page with the gold-layer data embedded.

    python run.py dashboard        # writes docs/index.html

The page has no external dependencies (charts are hand-built SVG), so it opens straight
from disk and can be published as-is (e.g. GitHub Pages from the /docs folder).
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import duckdb

from . import config

TEMPLATE = config.ROOT / "dashboard" / "template.html"
OUT = config.ROOT / "docs" / "index.html"


def _rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        rec = {}
        for k, v in zip(cols, row):
            if isinstance(v, Decimal):
                v = float(v)
            elif isinstance(v, (dt.date, dt.datetime)):
                v = v.isoformat()[:10]
            elif hasattr(v, "__int__") and type(v).__name__ in ("int128", "HugeInt"):
                v = int(v)
            rec[k] = v
        out.append(rec)
    return out


def collect(con) -> dict:
    q = lambda sql: _rows(con, sql)
    data = {
        "generated": dt.date.today().isoformat(),
        "months": q("""
            select year_month, net_revenue_eur, margin_eur, margin_pct, units_sold, units_returned, orders,
                   weighted_price_gap_pct, purchases_eur, invoice_amount_at_risk_eur
            from gold.mart_kpis_monthly where is_complete_month order by month_start"""),
        "channel_months": q("""
            select d.year_month, f.channel, round(sum(f.net_revenue_eur), 2) as net_revenue_eur,
                   round(sum(f.margin_eur), 2) as margin_eur, sum(f.quantity) as units
            from gold.fct_sales f join gold.dim_date d on d.date_day = f.sale_date
            where d.year_month in (select year_month from gold.mart_kpis_monthly where is_complete_month)
            group by 1, 2 order by 1, 2"""),
        "weekly_gap": q("""
            select week_start, round(sum(price_gap_pct * units) / sum(units), 4) as gap, sum(units) as units
            from gold.mart_price_position_weekly
            where units > 0 and price_gap_pct is not null
            group by 1 order by 1"""),
        "bands": q("""
            select price_band, count(*) as product_weeks, round(avg(units), 2) as avg_units,
                   round(avg(price_gap_pct), 3) as avg_gap
            from gold.mart_price_position_weekly where price_band <> 'no sales'
            group by 1"""),
        "pricier_now": q("""
            with last4 as (
                select * from gold.mart_price_position_weekly
                where week_start >= (select max(week_start) - interval 21 day from gold.mart_price_position_weekly)
            )
            select sku, any_value(title) as title, round(avg(our_avg_price_eur), 2) as our_price,
                   round(avg(competitor_avg_price_eur), 2) as competitor_price,
                   round(avg(price_gap_pct), 3) as gap, sum(units) as units
            from last4 where price_gap_pct is not null
            group by 1 having avg(price_gap_pct) > 0.10
            order by gap desc limit 12"""),
        "invoices": q("""
            select supplier_code, invoice_number, invoice_date, audit_status, total_printed,
                   overbilled_lines, amount_at_risk_eur
            from gold.mart_invoice_audit where audit_status <> 'OK'
            order by amount_at_risk_eur desc"""),
        "invoice_totals": q("""
            select count(*) as invoices, count(*) filter (where audit_status <> 'OK') as need_action,
                   round(sum(amount_at_risk_eur), 2) as at_risk,
                   round(sum(total_printed) filter (where not is_duplicate), 2) as purchases
            from gold.mart_invoice_audit""")[0],
        "quality": q("""
            select source, rule, rows_checked, rows_failed, failed_share, action
            from gold.mart_data_quality order by source, rows_failed desc"""),
        "quarantine": q("""
            select dq_issue, count(*) as lines from silver.silver_sales_quarantine group by 1 order by 2 desc"""),
        "reconciliation": q("""
            select (select count(*) from bronze.sales_web) + (select count(*) from bronze.sales_marketplace)
                       + (select count(*) from bronze.sales_store)                  as raw_lines,
                   (select count(*) from silver.silver_sales)                       as clean_lines,
                   (select count(*) from silver.silver_sales_quarantine)            as quarantined_lines,
                   (select count(*) from bronze._ingestion_log where status = 'LOADED') as files_loaded""")[0],
    }
    return data


def build() -> str:
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    try:
        data = collect(con)
    finally:
        con.close()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    return str(OUT.relative_to(config.ROOT))
