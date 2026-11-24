"""Extract supplier invoices from PDF into raw text fields (bronze).

Values are kept exactly as printed ("1.234,56 €", "03 Feb 2026"); typing and
validation happen in the silver layer, so a parsing rule can be fixed and
replayed without touching the PDFs again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


class InvoiceParseError(Exception):
    pass


@dataclass
class ParsedInvoice:
    layout: str
    supplier_name: str
    invoice_number: str
    invoice_date: str
    subtotal: str
    vat: str
    total: str
    lines: list[dict] = field(default_factory=list)


LAYOUTS = {
    # layout id -> marker line, header/total regexes and the regex of one invoice line
    "northwind_en": {
        "marker": "INVOICE",
        "number": r"^Invoice No: *(\S+)",
        "date": r"^Invoice Date: *(.+)$",
        "subtotal": r"^Subtotal: *(.+)$",
        "vat": r"^VAT[^:\n]*: *(.+)$",
        "total": r"^Total Due: *(.+)$",
        # Item | Description | Qty | Unit Price | Amount
        "line": r"^(?P<sku>INK-\d{5}) (?P<description>.+?) (?P<qty>\d+) "
                r"(?P<unit_price>[\d,]+\.\d{2} EUR) (?P<amount>[\d,]+\.\d{2} EUR)$",
    },
    "paperleaf_es": {
        "marker": "FACTURA",
        "number": r"^Nº factura: *(\S+)",
        "date": r"^Fecha: *(\S+)",
        "subtotal": r"^Base imponible: *(.+)$",
        "vat": r"^IVA[^:\n]*: *(.+)$",
        "total": r"^TOTAL FACTURA: *(.+)$",
        # Cant. | Descripción | Ref. | Precio | Importe
        "line": r"^(?P<qty>\d+) (?P<description>.+?) (?P<sku>INK-\d{5}) "
                r"(?P<unit_price>[\d.]+,\d{2} €) (?P<amount>[\d.]+,\d{2} €)$",
    },
}


def _find(pattern: str, text: str, what: str) -> str:
    m = re.search(pattern, text, re.M)
    if not m:
        raise InvoiceParseError(f"{what} not found")
    return m.group(1).strip()


def parse_invoice(path: Path) -> ParsedInvoice:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    lines_of_text = [l.strip() for l in text.splitlines()]
    if not any(lines_of_text):
        raise InvoiceParseError("no text layer (scanned PDF?)")

    layout_id = next((k for k, v in LAYOUTS.items() if v["marker"] in lines_of_text), None)
    if layout_id is None:
        raise InvoiceParseError("unknown invoice layout")
    spec = LAYOUTS[layout_id]

    inv = ParsedInvoice(
        layout=layout_id,
        supplier_name=lines_of_text[0],
        invoice_number=_find(spec["number"], text, "invoice number"),
        invoice_date=_find(spec["date"], text, "invoice date"),
        subtotal=_find(spec["subtotal"], text, "subtotal"),
        vat=_find(spec["vat"], text, "VAT"),
        total=_find(spec["total"], text, "total"),
    )
    # the line table is read from the text layer: one regex per layout is more robust
    # than table detection, which depends on how each supplier draws its grid lines
    for raw in lines_of_text:
        m = re.match(spec["line"], raw)
        if m:
            inv.lines.append(m.groupdict())
    if not inv.lines:
        raise InvoiceParseError("no invoice lines recognised")
    return inv
