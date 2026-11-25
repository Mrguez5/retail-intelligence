"""Create (or update) and publish the Databricks AI/BI dashboard on the gold layer.

    .venv-databricks/Scripts/python databricks/dashboard.py            # create/update + publish
    .venv-databricks/Scripts/python databricks/dashboard.py --print    # only print the definition

Four pages, like the HTML dashboard: Overview, Price vs competitor, Supplier invoices and
Data quality. Every widget reads a gold (or silver quarantine) table built by dbt, so the
dashboard shows the data of the last job run. Connection from the environment
(DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_HTTP_PATH for the warehouse).
"""
from __future__ import annotations

import argparse
import json
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.dashboards import Dashboard

CATALOG = os.environ.get("RI_DATABRICKS_CATALOG", "workspace")
NAME = "Retail Price & Procurement Intelligence"
G, S = f"{CATALOG}.gold", f"{CATALOG}.silver"

# ------------------------------------------------------------------ datasets (one SQL query each)
DATASETS = {
    "kpi_sales": f"""
        select sum(net_revenue_eur) as net_revenue,
               sum(margin_eur) / sum(net_revenue_eur) as margin_pct,
               sum(case when not is_return then quantity end) as units_sold,
               count(distinct case when not is_return then coalesce(order_id, sale_line_id) end) as orders
        from {G}.fct_sales""",
    "kpi_invoices": f"""
        select count(*) as invoices_checked,
               count_if(audit_status <> 'OK') as need_action,
               sum(amount_at_risk_eur) as money_at_risk,
               sum(case when is_duplicate then amount_at_risk_eur end) as duplicates_to_block,
               sum(case when not is_duplicate then amount_at_risk_eur end) as overcharges
        from {G}.mart_invoice_audit""",
    "revenue_month_channel": f"""
        select cast(date_trunc('month', f.sale_date) as date) as month, c.channel_name as channel,
               sum(f.net_revenue_eur) as net_revenue
        from {G}.fct_sales f join {G}.dim_channel c using (channel)
        group by all""",
    "margin_month": f"""
        select month_start as month, margin_pct from {G}.mart_kpis_monthly""",
    "price_gap_week": f"""
        select week_start as week, sum(price_gap_pct * units) / nullif(sum(units), 0) as price_gap
        from {G}.mart_price_position_weekly
        where price_gap_pct is not null
        group by week_start""",
    "units_by_band": f"""
        select case price_band when 'cheaper' then '1 Cheaper (< -3 %)' when 'aligned' then '2 Aligned (±3 %)'
                               when 'pricier' then '3 Dearer (3-10 %)' else '4 Much dearer (> 10 %)' end as price_position,
               avg(units) as units_per_product_week
        from {G}.mart_price_position_weekly
        where price_band <> 'no sales'
        group by all""",
    "dearer_titles": f"""
        select title, category,
               round(100 * sum(price_gap_pct * units) / nullif(sum(units), 0), 1) as gap_last_4_weeks_pct,
               sum(units) as books_sold
        from {G}.mart_price_position_weekly
        where week_start > (select max(week_start) from {G}.mart_price_position_weekly) - interval 28 days
          and price_gap_pct is not null
        group by all
        having sum(units) > 0
        order by gap_last_4_weeks_pct desc
        limit 15""",
    "invoices_at_risk": f"""
        select invoice_number, supplier_code, invoice_date, audit_status, round(amount_at_risk_eur, 2) as amount_at_risk_eur
        from {G}.mart_invoice_audit
        where audit_status <> 'OK'
        order by amount_at_risk_eur desc""",
    "overcharges": f"""
        select invoice_number, audit_status, amount_at_risk_eur
        from {G}.mart_invoice_audit
        where audit_status <> 'OK' and not is_duplicate""",
    "kpi_quality": f"""
        select (select count(*) from {S}.int_sales_checked) as raw_lines,
               (select count(*) from {G}.fct_sales) as clean_lines,
               (select count(*) from {S}.silver_sales_quarantine) as quarantined_lines""",
    "quarantine_reason": f"""
        select replace(dq_issue, '_', ' ') as reason, count(*) as lines
        from {S}.silver_sales_quarantine group by all""",
    "quality_checks": f"""
        select source, rule, rows_checked, rows_failed, round(100 * failed_share, 2) as failed_pct, action
        from {G}.mart_data_quality order by source, rule""",
}

# ------------------------------------------------------------------ widget helpers
_n = 0


def _name(prefix: str) -> str:
    global _n
    _n += 1
    return f"{prefix}{_n:02d}"


def _query(dataset: str, fields: list[str]) -> list[dict]:
    return [{"name": "main_query", "query": {"datasetName": dataset, "disaggregated": True,
                                              "fields": [{"name": f, "expression": f"`{f}`"} for f in fields]}}]


def _frame(title: str, description: str = "") -> dict:
    frame = {"showTitle": True, "title": title}
    if description:
        frame.update(showDescription=True, description=description)
    return frame


def counter(dataset, field, title, fmt=None):
    value = {"fieldName": field, "displayName": title}
    if fmt:
        value["format"] = fmt
    return {"name": _name("c"), "queries": _query(dataset, [field]),
            "spec": {"version": 2, "widgetType": "counter", "encodings": {"value": value}, "frame": _frame(title)}}


def chart(kind, dataset, x, y, title, description="", color=None, x_type="temporal", y_fmt=None, x_title=None, y_title=None,
          color_title=None):
    fields = [x, y] + ([color] if color else [])
    enc = {"x": {"fieldName": x, "scale": {"type": x_type}, "displayName": x_title or x},
           "y": {"fieldName": y, "scale": {"type": "quantitative"}, "displayName": y_title or y}}
    if y_fmt:
        enc["y"]["format"] = y_fmt
    if color:
        enc["color"] = {"fieldName": color, "scale": {"type": "categorical"}, "displayName": color_title or color}
    return {"name": _name("g"), "queries": _query(dataset, fields),
            "spec": {"version": 3, "widgetType": kind, "encodings": enc, "frame": _frame(title, description)}}


COLUMN_TITLES = {"gap_last_4_weeks_pct": "Gap, last 4 weeks (%)", "failed_pct": "Failed (%)",
                 "amount_at_risk_eur": "At risk (EUR)", "books_sold": "Books sold", "supplier_code": "Supplier",
                 "invoice_number": "Invoice", "invoice_date": "Date", "audit_status": "Status",
                 "rows_checked": "Rows checked", "rows_failed": "Rows failed"}


def table(dataset, columns, title, description=""):
    cols = [{"fieldName": c, "displayName": COLUMN_TITLES.get(c, c.replace("_", " ").capitalize())} for c in columns]
    return {"name": _name("t"), "queries": _query(dataset, columns),
            "spec": {"version": 2, "widgetType": "table", "encodings": {"columns": cols}, "frame": _frame(title, description)}}


def text(*lines):
    return {"name": _name("x"), "multilineTextboxSpec": {"lines": [line + "\n" for line in lines]}}


def place(widget, x, y, w, h):
    return {"widget": widget, "position": {"x": x, "y": y, "width": w, "height": h}}


EUR = {"type": "number-currency", "currencyCode": "EUR", "abbreviation": "compact", "decimalPlaces": {"type": "max", "places": 2}}
PCT = {"type": "number-percent", "decimalPlaces": {"type": "max", "places": 1}}
NUM = {"type": "number-plain", "abbreviation": "none", "decimalPlaces": {"type": "max", "places": 0}}


def definition() -> dict:
    header = text("# Retail Price & Procurement Intelligence",
                  "Inkwell Books (fictional) · Jan–Oct 2026 · built by a bronze → silver → gold pipeline "
                  "(PySpark + dbt on Databricks)")
    overview = [
        place(header, 0, 0, 6, 2),
        place(counter("kpi_sales", "net_revenue", "Net revenue", EUR), 0, 2, 2, 3),
        place(counter("kpi_sales", "margin_pct", "Gross margin", PCT), 2, 2, 1, 3),
        place(counter("kpi_sales", "units_sold", "Books sold", NUM), 3, 2, 1, 3),
        place(counter("kpi_sales", "orders", "Orders", NUM), 4, 2, 1, 3),
        place(counter("kpi_invoices", "money_at_risk", "Supplier money at risk", EUR), 5, 2, 1, 3),
        place(chart("bar", "revenue_month_channel", "month", "net_revenue", "Net revenue by month",
                    "Split by sales channel. Returns deducted.", color="channel", color_title="Channel", y_fmt=EUR,
                    x_title="Month", y_title="Net revenue"), 0, 5, 3, 7),
        place(chart("line", "margin_month", "month", "margin_pct", "Gross margin by month",
                    "After marketplace commission and supplier contract cost.", y_fmt=PCT,
                    x_title="Month", y_title="Gross margin"), 3, 5, 3, 7),
    ]
    prices = [
        place(text("## Price vs competitor",
                   "Same book on the competitor's site, scraped twice a week, GBP → EUR with that day's rate."), 0, 0, 6, 2),
        place(chart("line", "price_gap_week", "week", "price_gap", "How much dearer Inkwell is, by week",
                    "Weighted by units sold. Below 0 = cheaper than the competitor.", y_fmt=PCT,
                    x_title="Week", y_title="Price gap"), 0, 2, 4, 7),
        place(chart("bar", "units_by_band", "price_position", "units_per_product_week",
                    "Units per product and week, by price position", "Much dearer titles sell fewer units.",
                    x_type="categorical", x_title="Price position", y_title="Units per product-week"), 4, 2, 2, 7),
        place(table("dearer_titles", ["title", "category", "gap_last_4_weeks_pct", "books_sold"],
                    "Repricing candidates: titles by price gap, last 4 weeks"), 0, 9, 6, 8),
    ]
    invoices = [
        place(text("## Supplier invoices",
                   "Every PDF read, its arithmetic recomputed and each line compared with the contract price on the invoice date."), 0, 0, 6, 2),
        place(counter("kpi_invoices", "invoices_checked", "Invoices checked", NUM), 0, 2, 1, 3),
        place(counter("kpi_invoices", "need_action", "Need action", NUM), 1, 2, 1, 3),
        place(counter("kpi_invoices", "duplicates_to_block", "Duplicates to block (whole total)", EUR), 2, 2, 2, 3),
        place(counter("kpi_invoices", "overcharges", "Overcharges to claim (only the excess)", EUR), 4, 2, 2, 3),
        place(chart("bar", "overcharges", "invoice_number", "amount_at_risk_eur", "Overcharges per invoice",
                    "Duplicates are blocked in full and not shown here.", color="audit_status", color_title="Status", x_type="categorical",
                    y_fmt=EUR, x_title="Invoice", y_title="At risk"), 0, 5, 6, 7),
        place(table("invoices_at_risk", ["invoice_number", "supplier_code", "invoice_date", "audit_status", "amount_at_risk_eur"],
                    "Invoices that need action", "Sorted by money at risk."), 0, 12, 6, 8),
    ]
    quality = [
        place(text("## Data quality",
                   "The sources are messy on purpose. Every problem is caught, counted and fixed or set aside with its reason."), 0, 0, 6, 2),
        place(counter("kpi_quality", "raw_lines", "Raw sales lines", NUM), 0, 2, 2, 3),
        place(counter("kpi_quality", "clean_lines", "Clean lines used", NUM), 2, 2, 2, 3),
        place(counter("kpi_quality", "quarantined_lines", "Lines in quarantine", NUM), 4, 2, 2, 3),
        place(chart("bar", "quarantine_reason", "reason", "lines", "Sales lines set aside, by reason",
                    x_type="categorical", x_title="Reason", y_title="Lines"), 0, 5, 2, 8),
        place(table("quality_checks", ["source", "rule", "rows_checked", "rows_failed", "failed_pct", "action"],
                    "All data-quality checks"), 2, 5, 4, 8),
    ]
    pages = [("overview", "Overview", overview), ("prices", "Price vs competitor", prices),
             ("invoices", "Supplier invoices", invoices), ("quality", "Data quality", quality)]
    return {
        "datasets": [{"name": k, "displayName": k.replace("_", " "),
                      "queryLines": [" ".join(line.strip() for line in sql.strip().splitlines())]}
                     for k, sql in DATASETS.items()],
        "pages": [{"name": n, "displayName": d, "layout": layout, "pageType": "PAGE_TYPE_CANVAS"} for n, d, layout in pages],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args()
    body = definition()
    if args.print:
        print(json.dumps(body, indent=1)[:4000])
        return
    w = WorkspaceClient()
    warehouse_id = os.environ["DATABRICKS_HTTP_PATH"].rstrip("/").rsplit("/", 1)[-1]
    parent = f"/Workspace/Users/{w.current_user.me().user_name}/retail-intelligence"
    w.workspace.mkdirs(parent)
    existing = [d for d in w.lakeview.list() if d.display_name == NAME]
    dash = Dashboard(display_name=NAME, warehouse_id=warehouse_id, serialized_dashboard=json.dumps(body))
    if existing:
        d = w.lakeview.update(dashboard_id=existing[0].dashboard_id, dashboard=dash)
        print(f"dashboard updated: {d.dashboard_id}")
    else:
        dash.parent_path = parent
        d = w.lakeview.create(dashboard=dash)
        print(f"dashboard created: {d.dashboard_id}")
    w.lakeview.publish(dashboard_id=d.dashboard_id, warehouse_id=warehouse_id, embed_credentials=True)
    print(f"published: {os.environ['DATABRICKS_HOST'].rstrip('/')}/dashboardsv3/{d.dashboard_id}/published")


if __name__ == "__main__":
    main()
