"""File readers shared by the local (DuckDB) and the Databricks (Delta) bronze loaders.

Each reader returns text only: every value as str (or None), column names made SQL-friendly.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Callable

import pandas as pd

from .pdf_invoices import parse_invoice


def _snake(name: str) -> str:
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    return name or "col"


def _as_text(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=_snake)
    df = df.astype(object).where(df.notna(), None)
    to_text = lambda v: None if v is None else str(v)  # noqa: E731
    # DataFrame.map is pandas >= 2.1; older runtimes (e.g. Databricks serverless) only have applymap
    return df.map(to_text) if hasattr(df, "map") else df.applymap(to_text)


def read_csv(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8-sig") as fh:
        sep = ";" if fh.readline().count(";") > 0 else ","
    return _as_text(pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False, encoding="utf-8-sig"))


def read_excel(path: Path) -> pd.DataFrame:
    return _as_text(pd.read_excel(path, sheet_name=0, dtype=object))


def _single(table: str, fn: Callable[[Path], pd.DataFrame]) -> Callable[[Path], dict[str, pd.DataFrame]]:
    return lambda p: {table: fn(p)}


def read_invoice(path: Path) -> dict[str, pd.DataFrame]:
    inv = parse_invoice(path)
    header = pd.DataFrame([{
        "layout": inv.layout, "supplier_name": inv.supplier_name, "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date, "subtotal": inv.subtotal, "vat": inv.vat, "total": inv.total,
        "line_count": str(len(inv.lines)),
    }])
    lines = pd.DataFrame(inv.lines)
    lines.insert(0, "invoice_number", inv.invoice_number)
    lines.insert(1, "line_no", [str(i) for i in range(1, len(lines) + 1)])
    return {"invoice_headers": header, "invoice_lines": lines}
