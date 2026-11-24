"""Supplier invoices as PDF, in two different layouts.

Northwind (NWD): English, "1,234.56 EUR", VAT 0 % (EU reverse charge).
Paperleaf (PLW): Spanish, "1.234,56 €", IVA 4 %, different column order.

Injected problems (the audit mart must find all of them):
- Paperleaf bills its July price increase already on the second June invoice.
- Northwind overbills ~3 % of lines by 8 %.
- Two invoices whose printed total does not equal subtotal + VAT.
- One invoice sent twice (same number, second file name ends in _resend).
"""
from __future__ import annotations

import datetime as dt
import math
import shutil

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pipeline import config
from .world import PLW_PRICE_INCREASE, SUPPLIERS, World

STYLES = getSampleStyleSheet()
BUYER = "Inkwell Books S.L. (fictional) · Calle Ficticia 12, 28000 Madrid · VAT ESB00000000"


def fmt_en(x: float) -> str:
    return f"{x:,.2f} EUR"


def fmt_es(x: float) -> str:
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def _contract_cost(w: World, sku: str, on: dt.date) -> float:
    c = w.contract[w.contract["sku"] == sku]
    for _, r in c.iterrows():
        if r["valid_from"] <= on and (r["valid_to"] is None or pd.isna(r["valid_to"]) or on <= r["valid_to"]):
            return float(r["unit_cost_eur"])
    raise ValueError(sku)


def _pdf_nwd(path, number, date, lines, subtotal, vat, total):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40)
    head = [
        Paragraph(f"<font size=18 color='#1F3864'><b>{SUPPLIERS['NWD']}</b></font>", STYLES["Normal"]),
        Paragraph("Unit 4, Fictional Business Park, Dublin · orders@northwind-books.example", STYLES["Normal"]),
        Spacer(1, 18),
        Paragraph("<font size=14><b>INVOICE</b></font>", STYLES["Normal"]),
        Paragraph(f"Invoice No: {number}", STYLES["Normal"]),
        Paragraph(f"Invoice Date: {date:%d %b %Y}", STYLES["Normal"]),
        Paragraph(f"Bill To: {BUYER}", STYLES["Normal"]),
        Spacer(1, 14),
    ]
    data = [["Item", "Description", "Qty", "Unit Price", "Amount"]]
    for sku, title, qty, unit in lines:
        data.append([sku, title[:48], str(qty), fmt_en(unit), fmt_en(round(qty * unit, 2))])
    t = Table(data, colWidths=[70, 250, 40, 75, 85])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
    ]))
    tail = [
        Spacer(1, 12),
        Paragraph(f"Subtotal: {fmt_en(subtotal)}", STYLES["Normal"]),
        Paragraph(f"VAT 0% (reverse charge): {fmt_en(vat)}", STYLES["Normal"]),
        Paragraph(f"<b>Total Due: {fmt_en(total)}</b>", STYLES["Normal"]),
        Spacer(1, 10),
        Paragraph("Payment terms: 30 days. Fictional company for a portfolio demo.", STYLES["Italic"]),
    ]
    doc.build(head + [t] + tail)


def _pdf_plw(path, number, date, lines, subtotal, vat, total):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40)
    head = [
        Paragraph(f"<font size=18 color='#7A2E2E'><b>{SUPPLIERS['PLW']}</b></font>", STYLES["Normal"]),
        Paragraph("Polígono Ficticio 7, Valencia · CIF B00000000 · pedidos@paperleaf.example", STYLES["Normal"]),
        Spacer(1, 18),
        Paragraph("<font size=14><b>FACTURA</b></font>", STYLES["Normal"]),
        Paragraph(f"Nº factura: {number}", STYLES["Normal"]),
        Paragraph(f"Fecha: {date:%d/%m/%Y}", STYLES["Normal"]),
        Paragraph(f"Cliente: {BUYER}", STYLES["Normal"]),
        Spacer(1, 14),
    ]
    data = [["Cant.", "Descripción", "Ref.", "Precio", "Importe"]]
    for sku, title, qty, unit in lines:
        data.append([str(qty), title[:48], sku, fmt_es(unit), fmt_es(round(qty * unit, 2))])
    t = Table(data, colWidths=[40, 255, 70, 70, 85])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#BBBBBB")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7A2E2E")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
    ]))
    tail = [
        Spacer(1, 12),
        Paragraph(f"Base imponible: {fmt_es(subtotal)}", STYLES["Normal"]),
        Paragraph(f"IVA 4%: {fmt_es(vat)}", STYLES["Normal"]),
        Paragraph(f"<b>TOTAL FACTURA: {fmt_es(total)}</b>", STYLES["Normal"]),
        Spacer(1, 10),
        Paragraph("Forma de pago: transferencia a 30 días. Empresa ficticia (demo de portfolio).", STYLES["Italic"]),
    ]
    doc.build(head + [t] + tail)


def write_invoices(w: World, rng: np.random.Generator) -> dict:
    if config.RAW_INVOICES.exists():
        shutil.rmtree(config.RAW_INVOICES)
    config.RAW_INVOICES.mkdir(parents=True)

    sales = w.sales_lines
    sup_of = w.products.set_index("sku")["supplier_code"]
    title_of = w.products.set_index("sku")["title"]
    stats = {"invoices": 0, "overbilled_lines": 0, "early_increase_invoice": None,
             "total_errors": [], "duplicate": None}
    invoices = []
    counters = {"NWD": 0, "PLW": 0}

    # replenishment twice a month: buy what sold in the previous half month (top 15 titles)
    for m in pd.period_range(config.START, config.END, freq="M"):
        for half_start in (m.start_time, m.start_time + pd.Timedelta(days=15)):
            inv_date = (half_start + pd.Timedelta(days=int(rng.integers(0, 3)))).date()
            window = sales[(sales["date"] >= half_start - pd.Timedelta(days=15)) & (sales["date"] < half_start)]
            if window.empty:                                   # first invoice of the year
                window = sales[sales["date"] < half_start + pd.Timedelta(days=15)]
            for sup in ("NWD", "PLW"):
                units = window[window["sku"].map(sup_of) == sup].groupby("sku")["qty"].sum()
                top = units.sort_values(ascending=False).head(15)
                counters[sup] += 1
                number = f"NWD-2026-{counters[sup]:04d}" if sup == "NWD" else f"F26/{counters[sup]:04d}"
                lines = []
                bill_on = inv_date
                if sup == "PLW" and inv_date.month == 6 and inv_date.day >= 15:
                    bill_on = PLW_PRICE_INCREASE                # bills July price in June
                    stats["early_increase_invoice"] = number
                for sku, q in top.items():
                    qty = int(max(5, math.ceil(q * 1.2 / 5) * 5))
                    unit = _contract_cost(w, sku, bill_on)
                    if sup == "NWD" and rng.random() < 0.03:
                        unit = round(unit * 1.08, 2)
                        stats["overbilled_lines"] += 1
                    lines.append((sku, title_of[sku], qty, unit))
                subtotal = round(sum(round(q * u, 2) for _, _, q, u in lines), 2)
                vat = 0.0 if sup == "NWD" else round(subtotal * 0.04, 2)
                invoices.append([sup, number, inv_date, lines, subtotal, vat, round(subtotal + vat, 2)])

    # two printed totals with a typo
    for k in rng.choice(len(invoices), 2, replace=False):
        inv = invoices[k]
        inv[6] = round(inv[6] + float(rng.choice([90.0, -45.0, 100.0])), 2)
        stats["total_errors"].append(inv[1])

    for sup, number, date, lines, sub, vat, total in invoices:
        safe = number.replace("/", "-")
        path = config.RAW_INVOICES / f"{sup}_{safe}.pdf"
        (_pdf_nwd if sup == "NWD" else _pdf_plw)(path, number, date, lines, sub, vat, total)
        stats["invoices"] += 1

    # one invoice received twice
    dup = invoices[int(rng.integers(5, len(invoices) - 5))]
    safe = dup[1].replace("/", "-")
    shutil.copy(config.RAW_INVOICES / f"{dup[0]}_{safe}.pdf", config.RAW_INVOICES / f"{dup[0]}_{safe}_resend.pdf")
    stats["duplicate"] = dup[1]
    return stats
