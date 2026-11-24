"""Bronze layer: land every raw file in DuckDB, as text, with lineage columns.

Rules of this layer
- No business logic. Every value is stored as VARCHAR exactly as delivered.
- Column names are only made SQL-friendly ("Nº pedido" -> "n_pedido").
- Every row carries _source_file, _file_hash, _batch_id, _ingested_at and _row_number.
- Incremental and idempotent: a file is loaded once per content hash. If a file
  with the same name arrives with new content, its old rows are replaced.
- A file that cannot be read is logged as FAILED and the run carries on.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from . import config
from .pdf_invoices import InvoiceParseError, parse_invoice

log = logging.getLogger("bronze")


@dataclass(frozen=True)
class Source:
    table: str
    folder: Path
    pattern: str
    reader: Callable[[Path], dict[str, pd.DataFrame]]   # returns {table_name: frame}


# ------------------------------------------------------------------ readers
def _snake(name: str) -> str:
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    return name or "col"


def _as_text(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=_snake)
    return df.astype(object).where(df.notna(), None).map(lambda v: None if v is None else str(v))


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


SOURCES = [
    Source("erp_products", config.RAW_CATALOG, "erp_products*.csv", _single("erp_products", read_csv)),
    Source("erp_price_changes", config.RAW_CATALOG, "erp_price_changes*.csv", _single("erp_price_changes", read_csv)),
    Source("contract_prices", config.RAW_CONTRACTS, "*.xlsx", _single("contract_prices", read_excel)),
    Source("fx_rates", config.RAW_FX, "*.csv", _single("fx_rates", read_csv)),
    Source("competitor_snapshots", config.RAW_COMPETITOR, "*.csv", _single("competitor_snapshots", read_csv)),
    Source("sales_web", config.RAW_SALES_WEB, "*.csv", _single("sales_web", read_csv)),
    Source("sales_marketplace", config.RAW_SALES_MARKETPLACE, "*.csv", _single("sales_marketplace", read_csv)),
    Source("sales_store", config.RAW_SALES_STORE, "*.xlsx", _single("sales_store", read_excel)),
    Source("invoices", config.RAW_INVOICES, "*.pdf", read_invoice),
]


# ------------------------------------------------------------------ loading
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _q(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[tuple]:
    """Run one statement and always fetch its result (DuckDB 2.x needs results consumed)."""
    return con.execute(sql, params or []).fetchall()


def _ensure_log(con: duckdb.DuckDBPyConnection) -> None:
    _q(con, "create schema if not exists bronze")
    _q(con, """
        create table if not exists bronze._ingestion_log (
            batch_id varchar, source varchar, source_file varchar, file_hash varchar,
            status varchar, rows_loaded integer, error varchar, ingested_at timestamp
        )""")


def _write(con, table: str, df: pd.DataFrame, meta: dict) -> None:
    df = df.copy()
    df["_row_number"] = [str(i) for i in range(1, len(df) + 1)]
    for k, v in meta.items():
        df[k] = v
    con.register("incoming", df)
    exists = _q(con,
        "select count(*) from information_schema.tables where table_schema='bronze' and table_name=?", [table]
    )[0][0]
    if not exists:
        _q(con, f"create table bronze.{table} as select * from incoming")
    else:
        current = {r[0] for r in _q(con, f"describe bronze.{table}")}
        for col in df.columns:                           # schema drift: a new column appears
            if col not in current:
                log.warning("bronze.%s: new column %s", table, col)
                _q(con, f'alter table bronze.{table} add column "{col}" varchar')
        _q(con, f"insert into bronze.{table} by name select * from incoming")
    con.unregister("incoming")


def ingest_all(db_path: Path = config.DB_PATH) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    _ensure_log(con)
    batch = dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    seen = {(r[0], r[1]) for r in _q(con,
        "select source_file, file_hash from bronze._ingestion_log where status = 'LOADED'")}
    summary = {"loaded": 0, "skipped": 0, "failed": 0, "rows": 0}

    for src in SOURCES:
        files = sorted(src.folder.glob(src.pattern))
        for path in files:
            rel = path.relative_to(config.ROOT).as_posix()
            digest = _sha256(path)
            if (rel, digest) in seen:
                summary["skipped"] += 1
                continue
            now = dt.datetime.now()
            try:
                frames = src.reader(path)
            except (InvoiceParseError, ValueError, UnicodeDecodeError) as exc:
                _q(con, "insert into bronze._ingestion_log values (?,?,?,?,?,?,?,?)",
                            [batch, src.table, rel, digest, "FAILED", 0, str(exc), now])
                log.error("%s: %s", rel, exc)
                summary["failed"] += 1
                continue
            _q(con, "begin")
            rows = 0
            for table, df in frames.items():
                exists = _q(con,
                    "select count(*) from information_schema.tables where table_schema='bronze' and table_name=?",
                    [table])[0][0]
                if exists:                                # same file name, new content: replace its rows
                    _q(con, f"delete from bronze.{table} where _source_file = ?", [rel])
                _write(con, table, df, {"_source_file": rel, "_file_hash": digest,
                                        "_batch_id": batch, "_ingested_at": now.isoformat(timespec="seconds")})
                rows += len(df)
            _q(con, "insert into bronze._ingestion_log values (?,?,?,?,?,?,?,?)",
                        [batch, src.table, rel, digest, "LOADED", rows, None, now])
            _q(con, "commit")
            summary["loaded"] += 1
            summary["rows"] += rows
        log.info("%-22s %4d files", src.table, len(files))

    con.close()
    log.info("batch %s: %d files loaded (%d rows), %d unchanged, %d failed",
             batch, summary["loaded"], summary["rows"], summary["skipped"], summary["failed"])
    return summary
