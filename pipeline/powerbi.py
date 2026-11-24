"""Power BI version of the dashboard: export the gold layer to Parquet and generate a PBIP project.

    python run.py powerbi      # data/powerbi/*.parquet + powerbi/RetailIntelligence.pbip

Then open powerbi/RetailIntelligence.pbip in Power BI Desktop and click Home > Refresh.

The project is written as text (PBIP: TMDL semantic model + PBIR report), so it is versioned in
git like the rest of the code. The data folder is a model parameter (DataFolder) set to this
machine's path; run the command again after moving the repository.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import duckdb

from . import config

PROJECT = "RetailIntelligence"
PBI_DIR = config.ROOT / "powerbi"
DATA_DIR = config.DATA / "powerbi"
MODEL = PBI_DIR / f"{PROJECT}.SemanticModel" / "definition"
REPORT = PBI_DIR / f"{PROJECT}.Report" / "definition"
STATIC = PBI_DIR / f"{PROJECT}.Report" / "StaticResources"
BASE_THEME = "Fluent2-CY26SU10"

# ---------------------------------------------------------------------------------------------
# 1. Export: one Parquet file per model table, with explicit, Power BI-friendly types
# ---------------------------------------------------------------------------------------------
EXPORTS = {
    "FactSales": """
        select sale_line_id as SaleLineId, cast(sale_date as date) as Date, channel as Channel, sku as Sku,
               coalesce(order_id, sale_line_id) as OrderKey, cast(quantity as bigint) as Quantity,
               cast(unit_price_eur as double) as UnitPrice, cast(net_revenue_eur as double) as NetRevenue,
               cast(commission_eur as double) as Commission, cast(cogs_eur as double) as Cogs,
               cast(margin_eur as double) as Margin, is_return as IsReturn, match_method as MatchMethod
        from gold.fct_sales""",
    "DimProduct": """
        select sku as Sku, title as Title, category as Category, supplier_name as Supplier,
               cast(list_price_eur as double) as ListPrice, has_competitor_link as HasCompetitorLink
        from gold.dim_product""",
    "DimDate": """
        select date_day as Date, cast(year as bigint) as Year, cast(month as bigint) as MonthNumber,
               strftime(date_day, '%b') as MonthName, year_month as YearMonth, week_start as WeekStart
        from gold.dim_date""",
    "DimChannel": """
        select channel as Channel, channel_name as ChannelName, cast(commission_rate as double) as CommissionRate
        from gold.dim_channel""",
    "PricePosition": """
        select week_start as WeekStart, sku as Sku, cast(units as bigint) as Units,
               cast(net_revenue_eur as double) as NetRevenue, our_avg_price_eur as OurPrice,
               competitor_avg_price_eur as CompetitorPrice, price_gap_pct as PriceGap,
               case price_band when 'cheaper' then 'Cheaper (< -3 %)' when 'aligned' then 'Aligned (±3 %)'
                               when 'pricier' then 'Dearer (3-10 %)' when 'much pricier' then 'Much dearer (> 10 %)'
                               else 'No sales' end as PriceBand,
               case price_band when 'cheaper' then 1 when 'aligned' then 2 when 'pricier' then 3
                               when 'much pricier' then 4 else 5 end as PriceBandOrder
        from gold.mart_price_position_weekly""",
    "InvoiceAudit": """
        select a.supplier_code as SupplierCode, s.supplier_name as Supplier, a.invoice_number as InvoiceNumber,
               a.invoice_date as InvoiceDate, a.source_file as SourceFile, a.audit_status as Status,
               case when a.is_duplicate then 'Duplicate' when a.audit_status = 'OK' then 'OK' else 'Overcharge' end as StatusGroup,
               cast(a.total_printed as double) as TotalPrinted, cast(a.amount_at_risk_eur as double) as AmountAtRisk,
               cast(a.overbilled_lines as bigint) as OverbilledLines
        from gold.mart_invoice_audit a join gold.dim_supplier s using (supplier_code)""",
    "Quarantine": """
        select channel as Channel,
               case dq_issue when 'duplicate_line' then 'Duplicate line' when 'unknown_product' then 'Unknown product'
                             when 'price_outlier' then 'Absurd price' when 'missing_quantity' then 'Missing quantity'
                             when 'date_out_of_range' then 'Date out of range' when 'quantity_outlier' then 'Absurd quantity'
                             when 'ambiguous_product' then 'Ambiguous title' else dq_issue end as Reason,
               count(*) as Lines
        from silver.silver_sales_quarantine group by all""",
    "DataQuality": """
        select source as Source, rule as Rule, cast(rows_checked as bigint) as RowsChecked,
               cast(rows_failed as bigint) as RowsFailed, failed_share as Share, action as Action
        from gold.mart_data_quality""",
}


def export_parquet() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    try:
        for table, sql in EXPORTS.items():
            target = (DATA_DIR / f"{table}.parquet").as_posix()
            con.execute(f"copy ({sql}) to '{target}' (format parquet)").fetchall()
    finally:
        con.close()
    return len(EXPORTS)


# ---------------------------------------------------------------------------------------------
# 2. Semantic model (TMDL)
# ---------------------------------------------------------------------------------------------
TXT, INT, DBL, DATE, BOOL = ("string", "type text"), ("int64", "Int64.Type"), ("double", "type number"), \
    ("dateTime", "type date"), ("boolean", "type logical")
EUR, EUR2, PCT, GAP, N0 = "€#,0", "€#,0.00", "0.0%", "+0.0%;-0.0%;0.0%", "#,0"

# table -> [(column, type, properties)]
TABLES = {
    "FactSales": [
        ("SaleLineId", TXT, ["summarizeBy: none"]),
        ("Date", DATE, ["formatString: Short Date", "summarizeBy: none"]),
        ("Channel", TXT, ["isHidden", "summarizeBy: none"]),
        ("Sku", TXT, ["isHidden", "summarizeBy: none"]),
        ("OrderKey", TXT, ["isHidden", "summarizeBy: none"]),
        ("Quantity", INT, [f"formatString: {N0}", "summarizeBy: sum"]),
        ("UnitPrice", DBL, [f"formatString: {EUR2}", "summarizeBy: none"]),
        ("NetRevenue", DBL, [f"formatString: {EUR}", "summarizeBy: sum"]),
        ("Commission", DBL, [f"formatString: {EUR}", "summarizeBy: sum"]),
        ("Cogs", DBL, [f"formatString: {EUR}", "summarizeBy: sum"]),
        ("Margin", DBL, [f"formatString: {EUR}", "summarizeBy: sum"]),
        ("IsReturn", BOOL, ["summarizeBy: none"]),
        ("MatchMethod", TXT, ["summarizeBy: none"]),
    ],
    "DimProduct": [
        ("Sku", TXT, ["isKey", "summarizeBy: none"]),
        ("Title", TXT, ["summarizeBy: none"]),
        ("Category", TXT, ["summarizeBy: none"]),
        ("Supplier", TXT, ["summarizeBy: none"]),
        ("ListPrice", DBL, [f"formatString: {EUR2}", "summarizeBy: none"]),
        ("HasCompetitorLink", BOOL, ["summarizeBy: none"]),
    ],
    "DimDate": [
        ("Date", DATE, ["isKey", "formatString: Short Date", "summarizeBy: none"]),
        ("Year", INT, ["formatString: 0", "summarizeBy: none"]),
        ("MonthNumber", INT, ["isHidden", "summarizeBy: none"]),
        ("MonthName", TXT, ["sortByColumn: MonthNumber", "summarizeBy: none"]),
        ("YearMonth", TXT, ["summarizeBy: none"]),
        ("WeekStart", DATE, ["formatString: Short Date", "summarizeBy: none"]),
    ],
    "DimChannel": [
        ("Channel", TXT, ["isKey", "isHidden", "summarizeBy: none"]),
        ("ChannelName", TXT, ["summarizeBy: none"]),
        ("CommissionRate", DBL, [f"formatString: {PCT}", "summarizeBy: none"]),
    ],
    "PricePosition": [
        ("WeekStart", DATE, ["formatString: Short Date", "summarizeBy: none"]),
        ("Sku", TXT, ["isHidden", "summarizeBy: none"]),
        ("Units", INT, [f"formatString: {N0}", "summarizeBy: sum"]),
        ("NetRevenue", DBL, [f"formatString: {EUR}", "summarizeBy: sum"]),
        ("OurPrice", DBL, [f"formatString: {EUR2}", "summarizeBy: none"]),
        ("CompetitorPrice", DBL, [f"formatString: {EUR2}", "summarizeBy: none"]),
        ("PriceGap", DBL, [f"formatString: {GAP}", "summarizeBy: none"]),
        ("PriceBand", TXT, ["sortByColumn: PriceBandOrder", "summarizeBy: none"]),
        ("PriceBandOrder", INT, ["isHidden", "summarizeBy: none"]),
    ],
    "InvoiceAudit": [
        ("SupplierCode", TXT, ["isHidden", "summarizeBy: none"]),
        ("Supplier", TXT, ["summarizeBy: none"]),
        ("InvoiceNumber", TXT, ["summarizeBy: none"]),
        ("InvoiceDate", DATE, ["formatString: Short Date", "summarizeBy: none"]),
        ("SourceFile", TXT, ["summarizeBy: none"]),
        ("Status", TXT, ["summarizeBy: none"]),
        ("StatusGroup", TXT, ["summarizeBy: none"]),
        ("TotalPrinted", DBL, [f"formatString: {EUR2}", "summarizeBy: sum"]),
        ("AmountAtRisk", DBL, [f"formatString: {EUR2}", "summarizeBy: sum"]),
        ("OverbilledLines", INT, [f"formatString: {N0}", "summarizeBy: sum"]),
    ],
    "Quarantine": [
        ("Channel", TXT, ["isHidden", "summarizeBy: none"]),
        ("Reason", TXT, ["summarizeBy: none"]),
        ("Lines", INT, [f"formatString: {N0}", "summarizeBy: sum"]),
    ],
    "DataQuality": [
        ("Source", TXT, ["summarizeBy: none"]),
        ("Rule", TXT, ["summarizeBy: none"]),
        ("RowsChecked", INT, [f"formatString: {N0}", "summarizeBy: none"]),
        ("RowsFailed", INT, [f"formatString: {N0}", "summarizeBy: none"]),
        ("Share", DBL, ["formatString: 0.00%", "summarizeBy: none"]),
        ("Action", TXT, ["summarizeBy: none"]),
    ],
}

LAST4 = "VAR LastWeek = CALCULATE ( MAX ( PricePosition[WeekStart] ), ALL ( PricePosition ) )"
MEASURES = {  # table -> [(name, DAX lines, format, display folder)]
    "FactSales": [
        ("Net Revenue", ["SUM ( FactSales[NetRevenue] )"], EUR, "Sales"),
        ("Gross Margin", ["SUM ( FactSales[Margin] )"], EUR, "Sales"),
        ("Gross Margin %", ["DIVIDE ( [Gross Margin], [Net Revenue] )"], PCT, "Sales"),
        ("Units Sold", ["CALCULATE ( SUM ( FactSales[Quantity] ), FactSales[IsReturn] = FALSE () )"], N0, "Sales"),
        ("Units Returned", ["- CALCULATE ( SUM ( FactSales[Quantity] ), FactSales[IsReturn] = TRUE () )"], N0, "Sales"),
        ("Orders", ["CALCULATE ( DISTINCTCOUNT ( FactSales[OrderKey] ), FactSales[IsReturn] = FALSE () )"], N0, "Sales"),
        ("Avg Order Value", ["DIVIDE ( [Net Revenue], [Orders] )"], EUR2, "Sales"),
        ("Marketplace Commission", ["SUM ( FactSales[Commission] )"], EUR, "Sales"),
        ("Clean Lines", ["COUNTROWS ( FactSales )"], N0, "Data Quality"),
    ],
    "PricePosition": [
        # gap weighted by units sold: a title that sells a lot weighs more
        ("Price Gap", [
            "VAR GapRows = FILTER ( PricePosition, NOT ISBLANK ( PricePosition[PriceGap] ) )",
            "RETURN",
            "    DIVIDE ( SUMX ( GapRows, PricePosition[PriceGap] * PricePosition[Units] ), SUMX ( GapRows, PricePosition[Units] ) )",
        ], GAP, "Price"),
        ("Units per Product-Week", [
            # KEEPFILTERS: by price band (bar chart) each band must keep its own filter
            'CALCULATE ( AVERAGE ( PricePosition[Units] ), KEEPFILTERS ( PricePosition[PriceBand] <> "No sales" ) )'], "0.00", "Price"),
        ("Gap Last 4 Weeks", [LAST4, "RETURN", "    CALCULATE ( [Price Gap], PricePosition[WeekStart] >= LastWeek - 21 )"], GAP, "Price"),
        ("Units Last 4 Weeks", [LAST4, "RETURN",
            "    CALCULATE ( SUM ( PricePosition[Units] ), PricePosition[WeekStart] >= LastWeek - 21 )"], N0, "Price"),
        ("Titles Much Dearer", [
            "COUNTROWS ( FILTER ( VALUES ( PricePosition[Sku] ), [Gap Last 4 Weeks] > 0.10 ) )"], N0, "Price"),
    ],
    "InvoiceAudit": [
        ("Invoices Checked", ["COUNTROWS ( InvoiceAudit )"], N0, "Invoices"),
        ("Invoices Need Action", ['CALCULATE ( COUNTROWS ( InvoiceAudit ), InvoiceAudit[Status] <> "OK" )'], N0, "Invoices"),
        ("Duplicates To Block", ['CALCULATE ( SUM ( InvoiceAudit[AmountAtRisk] ), InvoiceAudit[StatusGroup] = "Duplicate" )'],
         EUR, "Invoices"),
        ("Overcharges", ['CALCULATE ( SUM ( InvoiceAudit[AmountAtRisk] ), InvoiceAudit[StatusGroup] = "Overcharge" )'],
         EUR, "Invoices"),
        ("Money At Risk", ["SUM ( InvoiceAudit[AmountAtRisk] )"], EUR, "Invoices"),
    ],
    "Quarantine": [
        ("Quarantined Lines", ["SUM ( Quarantine[Lines] )"], N0, "Data Quality"),
        ("Raw Lines", ["[Clean Lines] + [Quarantined Lines]"], N0, "Data Quality"),
        ("Clean Share", ["DIVIDE ( [Clean Lines], [Raw Lines] )"], PCT, "Data Quality"),
    ],
}

# colours with a meaning, shared by the DAX colour measures and the legends on the pages
CELL_GREEN, CELL_AMBER, CELL_RED, CELL_GREY = "#15803d", "#b45309", "#b91c1c", "#475569"   # cell backgrounds
UP, DOWN = "#4ade80", "#f87171"
CLAIM, REVIEW = "#fb923c", "#facc15"                                                      # invoice chart bars                                                           # trend / KPI text


def _trend(measure: str, kind: str = "pct") -> tuple[list[str], list[str]]:
    """Text '▲ 5.8 %  Oct vs Sep' (last month with sales vs the month before) and its colour."""
    head = [
        "VAR MaxSaleDate = CALCULATE ( MAX ( FactSales[Date] ), REMOVEFILTERS ( DimDate ) )",
        "VAR CurStart = EOMONTH ( MaxSaleDate, -1 ) + 1",
        "VAR PrevStart = EOMONTH ( MaxSaleDate, -2 ) + 1",
        f"VAR Cur = CALCULATE ( [{measure}], REMOVEFILTERS ( DimDate ), DimDate[Date] >= CurStart, DimDate[Date] <= EOMONTH ( MaxSaleDate, 0 ) )",
        f"VAR Prev = CALCULATE ( [{measure}], REMOVEFILTERS ( DimDate ), DimDate[Date] >= PrevStart, DimDate[Date] < CurStart )",
        "VAR Delta = " + ("DIVIDE ( Cur - Prev, Prev )" if kind == "pct" else "Cur - Prev"),
    ]
    amount = 'FORMAT ( ABS ( Delta ), "0.0%" )' if kind == "pct" else 'FORMAT ( ABS ( Delta ) * 100, "0.0" ) & " pp"'
    text = head + ["RETURN",
                   '    IF ( ISBLANK ( Cur ) || ISBLANK ( Prev ), BLANK (),',
                   f'        IF ( Delta >= 0, "▲ ", "▼ " ) & {amount} & "   " & FORMAT ( CurStart, "mmm" ) & " vs " & FORMAT ( PrevStart, "mmm" ) )']
    colour = head + ["RETURN", '    IF ( ISBLANK ( Delta ), BLANK (), IF ( Delta >= 0, "{UP}", "{DOWN}" ) )'.format(UP=UP, DOWN=DOWN)]
    return text, colour


for _m, _kind, _label in (("Net Revenue", "pct", "Revenue"), ("Gross Margin %", "pp", "Margin"),
                          ("Orders", "pct", "Orders"), ("Units Sold", "pct", "Units")):
    _text, _colour = _trend(_m, _kind)
    MEASURES["FactSales"] += [(f"{_label} Trend", _text, None, "Trends"),
                              (f"{_label} Trend Color", _colour, None, "Formatting")]

MEASURES["FactSales"].append(("Margin Color", [
    "VAR M = [Gross Margin %]",
    f'RETURN SWITCH ( TRUE (), ISBLANK ( M ), BLANK (), M >= 0.36, "{CELL_GREEN}", M >= 0.33, "{CELL_AMBER}", "{CELL_RED}" )'],
    None, "Formatting"))
MEASURES["PricePosition"] += [
    ("Sales Lost When Much Dearer", [
        'VAR MuchDearer = CALCULATE ( [Units per Product-Week], PricePosition[PriceBand] = "Much dearer (> 10 %)" )',
        'VAR Cheaper = CALCULATE ( [Units per Product-Week], PricePosition[PriceBand] = "Cheaper (< -3 %)" )',
        "RETURN DIVIDE ( MuchDearer, Cheaper ) - 1"], "+0%;-0%;0%", "Price"),
    ("Gap Color", [
        "VAR G = [Gap Last 4 Weeks]",
        f'RETURN SWITCH ( TRUE (), ISBLANK ( G ), BLANK (), G > 0.10, "{CELL_RED}", G > 0.03, "{CELL_AMBER}", G < -0.03, "{CELL_GREEN}", "{CELL_GREY}" )'],
     None, "Formatting"),
    ("Gap KPI Color", [f'IF ( [Gap Last 4 Weeks] > 0, "{DOWN}", "{UP}" )'], None, "Formatting"),
]
MEASURES["InvoiceAudit"].append(("Audit Color", [
    f'SWITCH ( SELECTEDVALUE ( InvoiceAudit[Status] ), "BLOCK - duplicate", "{CELL_RED}", '
    f'"CLAIM - billed above contract", "{CLAIM}", "REVIEW - total does not add up", "{REVIEW}", '
    f'"REVIEW - no contract price", "{REVIEW}", "{CELL_GREY}" )'], None, "Formatting"))
MEASURES["InvoiceAudit"].append(("Status Color", [
    f'SWITCH ( SELECTEDVALUE ( InvoiceAudit[StatusGroup] ), "Duplicate", "{CELL_RED}", "Overcharge", "{CELL_AMBER}", "OK", "{CELL_GREEN}" )'],
    None, "Formatting"))

RELATIONSHIPS = [
    ("FactSales.Date", "DimDate.Date"),
    ("FactSales.Sku", "DimProduct.Sku"),
    ("FactSales.Channel", "DimChannel.Channel"),
    ("PricePosition.WeekStart", "DimDate.Date"),
    ("PricePosition.Sku", "DimProduct.Sku"),
    ("InvoiceAudit.InvoiceDate", "DimDate.Date"),
    ("Quarantine.Channel", "DimChannel.Channel"),
]


def _q(name: str) -> str:
    """Quote a TMDL object name when needed."""
    return name if name.replace("_", "").isalnum() else f"'{name}'"


def _table_tmdl(table: str, columns) -> str:
    out = [f"table {table}"]
    if table == "DimDate":
        out.append("\tdataCategory: Time")
    out.append(f"\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, 'ri/' + table)}")
    out.append("")
    for name, dax, fmt, folder in MEASURES.get(table, []):
        if len(dax) == 1:
            out.append(f"\tmeasure {_q(name)} = {dax[0]}")
        else:
            out.append(f"\tmeasure {_q(name)} =")
            out += [f"\t\t\t{line}" for line in dax]
        if fmt:                                           # text measures (trends, colours) have no number format
            out.append(f"\t\tformatString: {fmt}")
        if folder == "Formatting":                        # colour helpers: used by visuals, hidden from users
            out.append("\t\tisHidden")
        out += [f"\t\tdisplayFolder: {folder}",
                f"\t\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, f'ri/{table}/m/{name}')}", ""]
    for name, (dtype, _), props in columns:
        out += [f"\tcolumn {_q(name)}", f"\t\tdataType: {dtype}"]
        out += [f"\t\t{p}" for p in props]
        out += [f"\t\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, f'ri/{table}/c/{name}')}",
                f"\t\tsourceColumn: {name}", ""]
    types = ", ".join(f'{{"{c}", {mtype}}}' for c, (_, mtype), _ in columns)
    m = ["let",
         f'    Source = Parquet.Document(File.Contents(DataFolder & "\\{table}.parquet")),',
         f"    Typed = Table.TransformColumnTypes(Source, {{{types}}})",
         "in",
         "    Typed"]
    out += [f"\tpartition {table} = m", "\t\tmode: import", "\t\tsource ="]
    out += [f"\t\t\t\t{line}" for line in m]
    out += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


def _dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _platform(kind: str, logical: str) -> dict:
    return {"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": kind, "displayName": PROJECT},
            "config": {"version": "2.0", "logicalId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"ri/{kind}"))}}


def write_model() -> None:
    root = MODEL.parent
    if MODEL.exists():
        shutil.rmtree(MODEL)
    (MODEL / "tables").mkdir(parents=True)
    _dump(root / ".platform", _platform("SemanticModel", "model"))
    _dump(root / "definition.pbism", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}})
    (MODEL / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1606\n\n", encoding="utf-8")
    for table, columns in TABLES.items():
        (MODEL / "tables" / f"{table}.tmdl").write_text(_table_tmdl(table, columns), encoding="utf-8")
    rels = []
    for frm, to in RELATIONSHIPS:
        rels += [f"relationship {uuid.uuid5(uuid.NAMESPACE_URL, f'ri/rel/{frm}')}", f"\tfromColumn: {frm}", f"\ttoColumn: {to}", ""]
    (MODEL / "relationships.tmdl").write_text("\n".join(rels), encoding="utf-8")
    folder = str(DATA_DIR).replace('"', '""')
    (MODEL / "expressions.tmdl").write_text(
        f'expression DataFolder = "{folder}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
        f"\tlineageTag: {uuid.uuid5(uuid.NAMESPACE_URL, 'ri/DataFolder')}\n"
        "\n\tannotation PBI_ResultType = Text\n", encoding="utf-8")
    (MODEL / "cultures").mkdir()
    (MODEL / "cultures" / "en-US.tmdl").write_text("cultureInfo en-US\n\n", encoding="utf-8")
    refs = "\n".join(f"ref table {t}" for t in TABLES)
    (MODEL / "model.tmdl").write_text(
        "model Model\n"
        "\tculture: en-US\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: en-US\n"
        "\tvalueFilterBehavior: independent\n"
        "\tdataAccessOptions\n"
        "\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n"
        "\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n\n"
        f"annotation PBI_QueryOrder = {json.dumps(['DataFolder', *TABLES])}\n\n"
        'annotation PBI_ProTooling = ["DevMode"]\n\n'
        f"{refs}\n\n"
        "ref cultureInfo en-US\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# 3. Report (PBIR): dark "command center" overview + the three detail pages
# ---------------------------------------------------------------------------------------------
VISUAL_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/1.2.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
CUSTOM_THEME = "RetailDark.json"

# dark palette (style inspired by real-time KPI wallboards)
PAGE_BG, TILE, TILE_2, SECTION, BORDER = "#0a1222", "#12203a", "#172a4a", "#16284a", "#1f3459"
WHITE, TEXT, MUTED = "#ffffff", "#e6edf7", "#93a4bf"
GREEN, AMBER, ORANGE, SKY, VIOLET, RED = "#22c55e", "#f59e0b", "#fb923c", "#38bdf8", "#a78bfa", "#f87171"
CHANNEL_COLORS = (("Web shop", SKY), ("Marketplace", AMBER), ("Madrid shop", GREEN))
STATUS = {"CLAIM - billed above contract": "#fb923c", "REVIEW - total does not add up": "#f59e0b",
          "BLOCK - duplicate": "#ef4444", "REVIEW - no contract price": "#f59e0b"}

THEME = {
    "name": "Retail Intelligence Dark",
    "dataColors": [SKY, AMBER, GREEN, "#f472b6", VIOLET, ORANGE, "#2dd4bf", RED],
    "background": TILE, "foreground": TEXT, "tableAccent": GREEN,
    "good": GREEN, "neutral": AMBER, "bad": RED, "maximum": GREEN, "center": AMBER, "minimum": RED,
    "textClasses": {
        "callout": {"fontSize": 30, "fontFace": "Segoe UI Semibold", "color": WHITE},
        "title": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": TEXT},
        "header": {"fontSize": 12, "fontFace": "Segoe UI Semibold", "color": TEXT},
        "label": {"fontSize": 10, "fontFace": "Segoe UI", "color": MUTED},
    },
    "visualStyles": {
        "*": {"*": {
            "background": [{"show": True, "color": {"solid": {"color": TILE}}, "transparency": 0}],
            "border": [{"show": True, "color": {"solid": {"color": BORDER}}, "radius": 12}],
            "dropShadow": [{"show": False}],
            "title": [{"show": True, "fontColor": {"solid": {"color": TEXT}}, "fontSize": 12}],
            "subTitle": [{"show": False}],
            "categoryAxis": [{"labelColor": {"solid": {"color": MUTED}}, "titleColor": {"solid": {"color": MUTED}},
                              "gridlineColor": {"solid": {"color": BORDER}}}],
            "valueAxis": [{"labelColor": {"solid": {"color": MUTED}}, "titleColor": {"solid": {"color": MUTED}},
                           "gridlineColor": {"solid": {"color": BORDER}}}],
            "legend": [{"labelColor": {"solid": {"color": TEXT}}}],
            "labels": [{"color": {"solid": {"color": TEXT}}}],
        }},
        "page": {"*": {"background": [{"color": {"solid": {"color": PAGE_BG}}, "transparency": 0}],
                       "outspace": [{"color": {"solid": {"color": PAGE_BG}}}]}},
        "tableEx": {"*": {
            "columnHeaders": [{"fontColor": {"solid": {"color": MUTED}}, "backColor": {"solid": {"color": TILE}},
                               "fontSize": 9, "bold": True}],
            "values": [{"fontColorPrimary": {"solid": {"color": TEXT}}, "backColorPrimary": {"solid": {"color": TILE}},
                        "fontColorSecondary": {"solid": {"color": TEXT}}, "backColorSecondary": {"solid": {"color": TILE_2}},
                        "fontSize": 10}],
            "grid": [{"gridHorizontal": True, "gridHorizontalColor": {"solid": {"color": BORDER}},
                      "gridVertical": False, "outlineColor": {"solid": {"color": BORDER}}, "rowPadding": 4}],
            "total": [{"fontColor": {"solid": {"color": TEXT}}, "backColor": {"solid": {"color": TILE}}}],
        }},
        "card": {"*": {"labels": [{"color": {"solid": {"color": WHITE}}, "fontSize": 28}],
                       "categoryLabels": [{"color": {"solid": {"color": MUTED}}, "fontSize": 11}]}},
        "slicer": {"*": {"items": [{"fontColor": {"solid": {"color": TEXT}}, "background": {"solid": {"color": TILE}}}],
                         "header": [{"fontColor": {"solid": {"color": MUTED}}}]}},
    },
}


def lit(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def color(value) -> dict:
    """A fixed colour ('#rrggbb') or a colour returned by a measure (field-value formatting)."""
    if isinstance(value, str):
        return {"solid": {"color": lit(f"'{value}'")}}
    return {"solid": {"color": {"expr": value}}}


def col(table: str, column: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}}


def mea(name: str, table: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def _ref(field: dict) -> str:
    inner = next(iter(field.values()))
    return f'{inner["Expression"]["SourceRef"]["Entity"]}.{inner["Property"]}'


def named(field: dict, display: str) -> dict:
    """The same field with a readable header / label in the visual."""
    return {**field, "__display": display}


def _projection(field: dict) -> dict:
    field = dict(field)
    display = field.pop("__display", None)
    proj = {"field": field, "queryRef": _ref(field), "nativeQueryRef": next(iter(field.values()))["Property"]}
    if display:
        proj["displayName"] = display
    return proj


def widths(**by_ref) -> list[dict]:
    """Fixed column widths for a table: widths(**{"DimProduct.Title": 330})."""
    return [{"properties": {"value": lit(f"{w}D")}, "selector": {"metadata": ref}} for ref, w in by_ref.items()]


def para(text: str, size: str, bold: bool = False, color_hex: str = TEXT, align: str = "left") -> dict:
    style = {"fontSize": size, "color": color_hex}
    if bold:
        style["fontWeight"] = "bold"
    return {"textRuns": [{"value": text, "textStyle": style}], "horizontalTextAlignment": align}


def cell_colors(*pairs) -> list[dict]:
    """Table conditional formatting: background of a column from a colour measure, white text."""
    out = []
    for field, colour_measure in pairs:
        sel = {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": _ref(field)}
        out.append({"properties": {"backColor": color(colour_measure), "fontColor": color(WHITE)}, "selector": sel})
    return out


def series_colors(table: str, column: str, mapping) -> list[dict]:
    return [{"properties": {"fill": color(c)}, "selector": {"data": [{"scopeId": {"Comparison": {
        "ComparisonKind": 0, "Left": col(table, column), "Right": {"Literal": {"Value": f"'{name}'"}}}}}]}}
        for name, c in mapping]


class Page:
    def __init__(self, name: str, display: str):
        self.name, self.display, self.visuals = name, display, []

    def _add(self, vname, x, y, w, h, visual):
        self.visuals.append({"$schema": VISUAL_SCHEMA, "name": vname,
                             "position": {"x": x, "y": y, "z": len(self.visuals) * 1000, "width": w, "height": h,
                                          "tabOrder": len(self.visuals) * 1000},
                             "visual": visual})

    @staticmethod
    def _container(title=None, bg=TILE, border=True, title_color=TEXT, title_size="12D"):
        c = {"background": [{"properties": {"show": lit("true" if bg else "false"),
                                            "color": color(bg or TILE), "transparency": lit("0D")}}],
             "border": [{"properties": {"show": lit("true" if border else "false"), "color": color(BORDER),
                                        "radius": lit("12D")}}],
             "subTitle": [{"properties": {"show": lit("false")}}],
             "visualHeader": [{"properties": {"show": lit("false")}}]}
        if title:
            c["title"] = [{"properties": {"show": lit("true"), "text": lit(f"'{title}'"), "fontColor": color(title_color),
                                          "fontSize": lit(title_size), "bold": lit("true"), "alignment": lit("'left'")}}]
        else:
            c["title"] = [{"properties": {"show": lit("false")}}]
        return c

    def visual(self, vname, vtype, x, y, w, h, roles, title=None, objects=None, sort=None, bg=TILE, border=True):
        v = {"visualType": vtype, "query": {"queryState": {
            role: {"projections": [_projection(f) for f in fields]} for role, fields in roles.items()}}}
        if sort:
            sort_field = {k: val for k, val in sort[0].items() if k != "__display"}
            v["query"]["sortDefinition"] = {"sort": [{"field": sort_field, "direction": sort[1]}], "isDefaultSort": True}
        if objects:
            v["objects"] = objects
            if "columnWidth" in objects:                 # Power BI's auto-size would override fixed widths
                objects.setdefault("columnHeaders", []).append(
                    {"properties": {"autoSizeColumnWidth": lit("false")}})
        v["visualContainerObjects"] = self._container(title, bg, border)
        self._add(vname, x, y, w, h, v)

    def kpi(self, vname, x, y, w, h, measure, title, value_color=WHITE, size="30D", bg=TILE, units="1D",
            title_color=TEXT, trend=None, trend_color=None, precision=None):
        """A wallboard tile: title top-left, big coloured value, optional trend line under it."""
        label = {"color": color(value_color), "fontSize": lit(size), "labelDisplayUnits": lit(units)}
        if precision is not None:                        # otherwise the measure's own format decides
            label["labelPrecision"] = lit(f"{precision}L")
        objects = {"labels": [{"properties": label}], "categoryLabels": [{"properties": {"show": lit("false")}}]}
        v = {"visualType": "card", "query": {"queryState": {"Values": {"projections": [_projection(measure)]}}},
             "objects": objects, "visualContainerObjects": self._container(title, bg, True, title_color)}
        self._add(vname, x, y, w, h, v)
        if trend:                                        # small text card sitting at the bottom of the tile
            tv = {"visualType": "card", "query": {"queryState": {"Values": {"projections": [_projection(trend)]}}},
                  "objects": {"labels": [{"properties": {"color": color(trend_color or MUTED), "fontSize": lit("11D")}}],
                              "categoryLabels": [{"properties": {"show": lit("false")}}]},
                  "visualContainerObjects": self._container(None, None, False)}
            self._add(vname + "Trend", x + 6, y + h - 58, w - 12, 52, tv)   # 52 px: Power BI pads cards inside

    def textbox(self, vname, x, y, w, h, paragraphs, bg=None):
        v = {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
             "visualContainerObjects": self._container(None, bg, bool(bg))}
        self._add(vname, x, y, w, h, v)

    def legend(self, vname, x, y, w, h, *lines, bg=TILE):
        """Colour key: each line is a list of (colour, label); a colour of None prints plain text."""
        paragraphs = []
        for items in lines:
            runs = []
            for colour, label in items:
                if colour:
                    runs.append({"value": "■ ", "textStyle": {"fontSize": "11pt", "color": colour}})
                runs.append({"value": label + "     ", "textStyle": {"fontSize": "9.5pt", "color": MUTED}})
            paragraphs.append({"textRuns": runs})
        self.textbox(vname, x, y, w, h, paragraphs, bg=bg)

    def header(self, title, subtitle):
        self.textbox("header", 40, 14, 1150, 76, [para(title, "20pt", True, WHITE), para(subtitle, "10.5pt", False, MUTED)])

    def section(self, vname, x, w, text):
        self.textbox(vname, x, 100, w, 66, [para(text, "20pt", True, WHITE, "center")], bg=SECTION)

    def write(self):
        folder = REPORT / "pages" / self.name
        _dump(folder / "page.json", {"$schema": PAGE_SCHEMA, "name": self.name, "displayName": self.display,
                                     "displayOption": "FitToPage", "height": 1080, "width": 1920,
                                     "objects": {"background": [{"properties": {"color": color(PAGE_BG),
                                                                                "transparency": lit("0D")}}]}})
        for v in self.visuals:
            _dump(folder / "visuals" / v["name"] / "visual.json", v)


DROPDOWN = {"data": [{"properties": {"mode": lit("'Dropdown'")}}]}

# what each colour means (same constants as the DAX colour measures)
LEGEND_MARGIN = ([(None, "Gross margin:"), (CELL_GREEN, "36 % or more"), (CELL_AMBER, "33–36 %"), (CELL_RED, "below 33 %")],)
LEGEND_TREND = [(None, "Trends (last month vs the one before):"), (UP, "▲ up"), (DOWN, "▼ down")]
LEGEND_GAP = ([(None, "Inkwell vs competitor:"), (CELL_RED, "more than 10 % dearer"), (CELL_AMBER, "3–10 % dearer")],
              [(CELL_GREY, "within ±3 %"), (CELL_GREEN, "more than 3 % cheaper")])
LEGEND_STATUS = ([(None, "Invoice status:"), (CELL_RED, "Duplicate — block, whole total at risk")],
                 [(CELL_AMBER, "Overcharge — claim the excess"), (CELL_GREEN, "OK")])
LABELS_ON = {"labels": [{"properties": {"show": lit("true"), "color": color(TEXT)}}]}
STRAIGHT = {"lineStyles": [{"properties": {"lineChartType": lit("'linear'"), "strokeWidth": lit("3D")}}]}


def _slicers(page, fields):
    for i, (vname, field) in enumerate(reversed(fields)):
        page.visual(vname, "slicer", 1570 - i * 330, 22, 310, 64, {"Values": [field]}, objects=DROPDOWN)


def _overview() -> Page:
    p = Page("ri0000000000overview", "Overview")
    p.header("Retail Price & Procurement Intelligence",
             "Inkwell Books (fictional) · Jan–Oct 2026 · bronze → silver → gold pipeline · trends: last month vs the one before")
    _slicers(p, [("slicerChannel", named(col("DimChannel", "ChannelName"), "Channel")),
                 ("slicerCategory", col("DimProduct", "Category"))])
    cols = {"sales": 40, "price": 660, "inv": 1280}
    p.section("secSales", cols["sales"], 600, "💶  Sales")
    p.section("secPrice", cols["price"], 600, "🏷️  Price vs competitor")
    p.section("secInvoices", cols["inv"], 600, "🧾  Supplier invoices")

    # sales: tall green hero tile + 2x2 tiles with month-on-month trends
    p.kpi("heroRevenue", 40, 184, 200, 342, mea("Net Revenue", "FactSales"), "Net revenue", "#052e16", "34D",
          bg=GREEN, units="0D", title_color="#052e16", precision=2,
          trend=mea("Revenue Trend", "FactSales"), trend_color="#052e16")
    tiles = [("kpiMargin", "Gross Margin %", "Gross margin", SKY, "Margin"),
             ("kpiOrders", "Orders", "Orders", AMBER, "Orders"),
             ("kpiUnits", "Units Sold", "Books sold", WHITE, "Units"),
             ("kpiAov", "Avg Order Value", "Avg order value", VIOLET, None)]
    for i, (vname, measure, title, c, trend) in enumerate(tiles):
        x, y = 256 + (i % 2) * 196, 184 + (i // 2) * 182
        p.kpi(vname, x, y, 184, 160, mea(measure, "FactSales"), title, c, "26D",
              trend=mea(f"{trend} Trend", "FactSales") if trend else None,
              trend_color=mea(f"{trend} Trend Color", "FactSales") if trend else None)

    # price vs competitor
    price = [("kpiGap4w", "Gap Last 4 Weeks", "Price gap · last 4 weeks", mea("Gap KPI Color", "PricePosition")),
             ("kpiGap", "Price Gap", "Price gap · Jan–Oct", WHITE),
             ("kpiDearer", "Titles Much Dearer", "Titles >10 % dearer", RED),
             ("kpiLost", "Sales Lost When Much Dearer", "Sales when >10 % dearer", ORANGE)]
    for i, (vname, measure, title, c) in enumerate(price):
        x, y = 660 + (i % 2) * 310, 184 + (i // 2) * 182
        p.kpi(vname, x, y, 290, 160, mea(measure, "PricePosition"), title, c, "30D")

    # supplier invoices
    inv = [("kpiDuplicates", "Duplicates To Block", "Duplicates to block", RED),
           ("kpiOvercharges", "Overcharges", "Overcharges to claim", ORANGE),
           ("kpiNeedAction", "Invoices Need Action", "Invoices need action", AMBER),
           ("kpiChecked", "Invoices Checked", "Invoices checked", WHITE)]
    for i, (vname, measure, title, c) in enumerate(inv):
        x, y = 1280 + (i % 2) * 310, 184 + (i // 2) * 182
        p.kpi(vname, x, y, 290, 160, mea(measure, "InvoiceAudit"), title, c, "30D")

    # tables with coloured cells
    p.visual("tblCategories", "tableEx", 40, 546, 600, 450,
             {"Values": [col("DimProduct", "Category"), named(mea("Net Revenue", "FactSales"), "Revenue"),
                         named(mea("Gross Margin %", "FactSales"), "Margin %"), named(mea("Units Sold", "FactSales"), "Books")]},
             title="Categories by revenue",
             objects={"values": cell_colors((mea("Gross Margin %", "FactSales"), mea("Margin Color", "FactSales"))),
                      "columnWidth": widths(**{"DimProduct.Category": 210, "FactSales.Net Revenue": 120,
                                               "FactSales.Gross Margin %": 110, "FactSales.Units Sold": 90})},
             sort=(mea("Net Revenue", "FactSales"), "Descending"))
    p.visual("tblDearTitles", "tableEx", 660, 546, 600, 450,
             {"Values": [col("DimProduct", "Title"), named(mea("Gap Last 4 Weeks", "PricePosition"), "Gap"),
                         named(mea("Units Last 4 Weeks", "PricePosition"), "Books")]},
             title="Titles by price gap · last 4 weeks",
             objects={"values": cell_colors((mea("Gap Last 4 Weeks", "PricePosition"), mea("Gap Color", "PricePosition"))),
                      "columnWidth": widths(**{"DimProduct.Title": 360, "PricePosition.Gap Last 4 Weeks": 90,
                                               "PricePosition.Units Last 4 Weeks": 70})},
             sort=(mea("Gap Last 4 Weeks", "PricePosition"), "Descending"))
    p.visual("tblInvoices", "tableEx", 1280, 546, 600, 450,
             {"Values": [named(col("InvoiceAudit", "InvoiceNumber"), "Invoice"), col("InvoiceAudit", "Supplier"),
                         named(col("InvoiceAudit", "StatusGroup"), "Status"), named(col("InvoiceAudit", "AmountAtRisk"), "At risk")]},
             title="Invoices by money at risk",
             objects={"values": cell_colors((col("InvoiceAudit", "StatusGroup"), mea("Status Color", "InvoiceAudit"))),
                      "columnWidth": widths(**{"InvoiceAudit.InvoiceNumber": 120, "InvoiceAudit.Supplier": 220,
                                               "InvoiceAudit.StatusGroup": 100, "InvoiceAudit.AmountAtRisk": 100})},
             sort=(col("InvoiceAudit", "AmountAtRisk"), "Descending"))
    p.legend("legSales", 40, 1006, 600, 62, *LEGEND_MARGIN, LEGEND_TREND)
    p.legend("legPrice", 660, 1006, 600, 62, *LEGEND_GAP)
    p.legend("legInvoices", 1280, 1006, 600, 62, *LEGEND_STATUS)
    return p


def _row_of_kpis(page, items, y=110):
    w = (1840 - 20 * (len(items) - 1)) // len(items)
    for i, (measure, title, c) in enumerate(items):
        page.kpi(f"kpi{i + 1}", 40 + i * (w + 20), y, w, 150, measure, title, c, "30D")


def _prices() -> Page:
    p = Page("ri000000000000prices", "Price vs competitor")
    p.header("🏷️  Price vs competitor", "Same book on the competitor's site, scraped twice a week, GBP → EUR with that day's rate")
    _slicers(p, [("slicerCategory", col("DimProduct", "Category"))])
    _row_of_kpis(p, [(mea("Price Gap", "PricePosition"), "Price gap · Jan–Oct", WHITE),
                     (mea("Gap Last 4 Weeks", "PricePosition"), "Price gap · last 4 weeks", mea("Gap KPI Color", "PricePosition")),
                     (mea("Units per Product-Week", "PricePosition"), "Units per product-week", SKY),
                     (mea("Titles Much Dearer", "PricePosition"), "Titles >10 % dearer", RED)])
    p.visual("gapByWeek", "lineChart", 40, 280, 1100, 380,
             {"Category": [col("PricePosition", "WeekStart")], "Y": [mea("Price Gap", "PricePosition")]},
             title="How much dearer Inkwell is, by week (weighted by units)",
             objects={"dataPoint": [{"properties": {"fill": color(SKY)}}], **STRAIGHT},
             sort=(col("PricePosition", "WeekStart"), "Ascending"))
    p.visual("unitsByBand", "clusteredBarChart", 1160, 280, 720, 380,
             {"Category": [col("PricePosition", "PriceBand")], "Y": [mea("Units per Product-Week", "PricePosition")]},
             title="Units per product and week, by price position",
             objects={**LABELS_ON, "dataPoint": [{"properties": {"fill": color(SKY)}}]},
             sort=(col("PricePosition", "PriceBand"), "Ascending"))
    p.visual("dearTitles", "tableEx", 40, 680, 1840, 330,
             {"Values": [named(col("DimProduct", "Sku"), "SKU"), col("DimProduct", "Title"), col("DimProduct", "Category"),
                         named(mea("Gap Last 4 Weeks", "PricePosition"), "Gap (4 weeks)"),
                         named(mea("Units Last 4 Weeks", "PricePosition"), "Books (4 weeks)")]},
             title="Titles by price gap, last 4 weeks (repricing candidates at the top)",
             objects={"values": cell_colors((mea("Gap Last 4 Weeks", "PricePosition"), mea("Gap Color", "PricePosition"))),
                      "columnWidth": widths(**{"DimProduct.Sku": 130, "DimProduct.Title": 820, "DimProduct.Category": 300,
                                               "PricePosition.Gap Last 4 Weeks": 230,
                                               "PricePosition.Units Last 4 Weeks": 230})},
             sort=(mea("Gap Last 4 Weeks", "PricePosition"), "Descending"))
    p.legend("legPrice", 40, 1024, 1840, 44, [*LEGEND_GAP[0], *LEGEND_GAP[1]])
    return p


def _invoices() -> Page:
    p = Page("ri00000000000invoices", "Supplier invoices")
    p.header("🧾  Supplier invoices",
             "Every PDF read, its arithmetic recomputed and each line compared with the contract price on the invoice date")
    _slicers(p, [("slicerSupplier", col("InvoiceAudit", "Supplier"))])
    _row_of_kpis(p, [(mea("Invoices Checked", "InvoiceAudit"), "Invoices checked", WHITE),
                     (mea("Invoices Need Action", "InvoiceAudit"), "Need action", AMBER),
                     (mea("Duplicates To Block", "InvoiceAudit"), "Duplicates to block (whole total)", RED),
                     (mea("Overcharges", "InvoiceAudit"), "Overcharges to claim (only the excess)", ORANGE)])
    p.visual("overchargeByInvoice", "clusteredBarChart", 40, 280, 1840, 380,
             {"Category": [named(col("InvoiceAudit", "InvoiceNumber"), "Invoice")],
              "Y": [named(mea("Overcharges", "InvoiceAudit"), "Overcharge")]},
             title="Overcharges per invoice (duplicates are blocked in full, not shown)",
             objects={"dataPoint": [{"properties": {"fill": color(mea("Audit Color", "InvoiceAudit"))},
                                     "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}],
                      **LABELS_ON},
             sort=(mea("Overcharges", "InvoiceAudit"), "Descending"))
    p.legend("legChart", 1300, 292, 560, 40,
             [(CLAIM, "Claim: billed above contract"), (REVIEW, "Review: total does not add up")], bg=None)
    p.visual("invoiceTable", "tableEx", 40, 680, 1840, 330,
             {"Values": [col("InvoiceAudit", "Supplier"), named(col("InvoiceAudit", "InvoiceNumber"), "Invoice"),
                         named(col("InvoiceAudit", "InvoiceDate"), "Date"), named(col("InvoiceAudit", "StatusGroup"), "Status"),
                         named(col("InvoiceAudit", "Status"), "Audit result"),
                         named(col("InvoiceAudit", "OverbilledLines"), "Lines above contract"),
                         named(col("InvoiceAudit", "TotalPrinted"), "Invoice total"),
                         named(col("InvoiceAudit", "AmountAtRisk"), "At risk")]},
             title="All invoices",
             objects={"values": cell_colors((col("InvoiceAudit", "StatusGroup"), mea("Status Color", "InvoiceAudit"))),
                      "columnWidth": widths(**{"InvoiceAudit.Supplier": 330, "InvoiceAudit.InvoiceNumber": 170,
                                               "InvoiceAudit.InvoiceDate": 140, "InvoiceAudit.StatusGroup": 150,
                                               "InvoiceAudit.Status": 340, "InvoiceAudit.OverbilledLines": 190,
                                               "InvoiceAudit.TotalPrinted": 210, "InvoiceAudit.AmountAtRisk": 200})},
             sort=(col("InvoiceAudit", "AmountAtRisk"), "Descending"))
    p.legend("legInvoices", 40, 1024, 1840, 44, [*LEGEND_STATUS[0], *LEGEND_STATUS[1]])
    return p


def _quality() -> Page:
    p = Page("ri0000000000quality", "Data quality")
    p.header("🧪  Data quality", "The sources are messy on purpose. Every problem is caught, counted and fixed or set aside with its reason")
    _slicers(p, [("slicerChannel", named(col("DimChannel", "ChannelName"), "Channel"))])
    _row_of_kpis(p, [(mea("Raw Lines", "Quarantine"), "Raw sales lines", WHITE),
                     (mea("Clean Lines", "FactSales"), "Clean lines used", GREEN),
                     (mea("Quarantined Lines", "Quarantine"), "Lines in quarantine", AMBER),
                     (mea("Clean Share", "Quarantine"), "Share of clean lines", SKY)])
    p.visual("quarantineByReason", "clusteredBarChart", 40, 280, 900, 440,
             {"Category": [col("Quarantine", "Reason")], "Y": [mea("Quarantined Lines", "Quarantine")]},
             title="Sales lines set aside, by reason",
             objects={**LABELS_ON, "dataPoint": [{"properties": {"fill": color(AMBER)}}]},
             sort=(mea("Quarantined Lines", "Quarantine"), "Descending"))
    p.textbox("how", 40, 740, 900, 320, [
        para("How the pipeline works", "13pt", True, WHITE),
        para("Bronze — every file landed as delivered, with its lineage; re-sent, removed and unreadable files handled", "10.5pt", color_hex=MUTED),
        para("Silver — types, product matching (SKU, exact or fuzzy title), validation, then de-duplication", "10.5pt", color_hex=MUTED),
        para("Gold — star schema and marts; every raw line ends up clean or in quarantine with its reason", "10.5pt", color_hex=MUTED),
        para("143 dbt tests and 9 robustness tests run on every change", "10.5pt", color_hex=MUTED),
    ], bg=TILE)
    p.visual("checks", "tableEx", 960, 280, 920, 780,
             {"Values": [col("DataQuality", "Source"), named(col("DataQuality", "Rule"), "Check"),
                         named(col("DataQuality", "RowsFailed"), "Found"), col("DataQuality", "Share"),
                         named(col("DataQuality", "Action"), "What the pipeline did")]},
             title="All data-quality checks", sort=(col("DataQuality", "Source"), "Ascending"))
    return p


def write_report() -> None:
    if REPORT.exists():
        shutil.rmtree(REPORT)
    if STATIC.exists():
        shutil.rmtree(STATIC)
    root = REPORT.parent
    _dump(root / ".platform", _platform("Report", "report"))
    _dump(root / "definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{PROJECT}.SemanticModel"}}})
    _dump(REPORT / "version.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0"})
    import_version = {"visual": "2.14.0", "report": "3.4.0", "page": "2.3.1"}
    _dump(REPORT / "report.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": BASE_THEME, "reportVersionAtImport": import_version, "type": "SharedResources"},
            "customTheme": {"name": CUSTOM_THEME, "reportVersionAtImport": import_version, "type": "RegisteredResources"}},
        "objects": {"section": [{"properties": {"verticalAlignment": lit("'Top'")}}],
                    "outspacePane": [{"properties": {"expanded": lit("false")}}]},
        "resourcePackages": [
            {"name": "SharedResources", "type": "SharedResources", "items": [
                {"name": BASE_THEME, "path": f"BaseThemes/{BASE_THEME}.json", "type": "BaseTheme"}]},
            {"name": "RegisteredResources", "type": "RegisteredResources", "items": [
                {"name": CUSTOM_THEME, "path": CUSTOM_THEME, "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                     "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True}})
    base_dir = STATIC / "SharedResources" / "BaseThemes"
    base_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(PBI_DIR / "theme" / f"{BASE_THEME}.json", base_dir / f"{BASE_THEME}.json")   # generated by Power BI
    _dump(STATIC / "RegisteredResources" / CUSTOM_THEME, THEME)

    pages = [_overview(), _prices(), _invoices(), _quality()]
    for p in pages:
        p.write()
    _dump(REPORT / "pages" / "pages.json", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name})


DAX_FUNCTIONS = {  # time-intelligence and common functions that tend to be picked as variable names
    "lastdate", "firstdate", "lastnonblank", "firstnonblank", "date", "dates", "year", "month", "day", "today",
    "now", "max", "min", "sum", "values", "filter", "all", "calendar", "eomonth", "edate", "weekday", "rank",
    "value", "values", "total", "datesbetween", "datesytd", "previousmonth", "nextmonth", "selectedvalue"}


def check_model() -> None:
    """Catch, before Power BI does, the mistakes that stop the project from opening."""
    problems = []
    import re
    for table, measures in MEASURES.items():                # VAR names must not be DAX functions
        for name, dax, *_ in measures:
            for var in re.findall(r"\bVAR\s+(\w+)", " ".join(dax)):
                if var.lower() in DAX_FUNCTIONS:
                    problems.append(f"measure '{name}' ({table}): VAR {var} is a DAX function name")
    columns = {(t, c.lower()) for t, cols in TABLES.items() for c, _, _ in cols}
    all_columns = {c for _, c in columns}
    seen = set()
    for table, measures in MEASURES.items():
        for name, *_ in measures:
            key = name.lower()
            if key in all_columns:                        # Power BI: "a column with the same name already exists"
                problems.append(f"measure '{name}' ({table}) has the same name as a column")
            if key in seen:
                problems.append(f"measure '{name}' is defined twice")
            seen.add(key)
    measures = {(t, m.lower()) for t, ms in MEASURES.items() for m, *_ in ms}
    for vis in REPORT.glob("pages/*/visuals/*/visual.json"):   # every field a visual uses must exist
        for kind, entity, prop in _fields(json.loads(vis.read_text(encoding="utf-8"))):
            ok = (entity, prop.lower()) in (measures if kind == "Measure" else columns)
            if not ok:
                problems.append(f"{vis.parent.parent.parent.name}/{vis.parent.name}: {kind} {entity}[{prop}] does not exist")
    if problems:
        raise SystemExit("Power BI model check failed:\n  " + "\n  ".join(problems))


def _fields(node):
    """Yield (Column|Measure, table, name) for every field reference in a visual."""
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            ref = node.get(kind)
            if isinstance(ref, dict) and "Property" in ref:
                yield kind, ref["Expression"]["SourceRef"]["Entity"], ref["Property"]
        for v in node.values():
            yield from _fields(v)
    elif isinstance(node, list):
        for v in node:
            yield from _fields(v)


def build() -> str:
    n = export_parquet()
    _dump(PBI_DIR / f"{PROJECT}.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
        "settings": {"enableAutoRecovery": True}})
    write_model()
    write_report()
    check_model()
    return f"{n} tables exported to {DATA_DIR}; project written to powerbi/{PROJECT}.pbip"
