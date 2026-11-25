"""Bronze layer: land every raw file in DuckDB, as text, with lineage columns.

Rules of this layer
- No business logic. Every value is stored as VARCHAR exactly as delivered.
- Column names are only made SQL-friendly ("Nº pedido" -> "n_pedido").
- Every row carries _source_file, _file_hash, _batch_id, _ingested_at and _row_number.
- Incremental and idempotent: a file is loaded once per content hash. If a file
  with the same name arrives with new content, its old rows are replaced.
- A file whose content is identical to one already loaded (re-sent under another
  name) is logged as DUPLICATE_FILE and not loaded again. Except supplier invoices:
  an invoice received twice is loaded, so the invoice audit can block it.
- A file that disappears from the landing zone has its rows taken out (REMOVED).
- A file that cannot be read, or fails while being written, is logged as FAILED
  (nothing half-written stays) and the run carries on with the next file.
- New columns are added automatically and recorded in bronze._schema_changes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from . import config

log = logging.getLogger("bronze")


@dataclass(frozen=True)
class Source:
    table: str
    folder: Path
    pattern: str
    reader: Callable[[Path], dict[str, pd.DataFrame]]   # returns {table_name: frame}
    tables: tuple[str, ...] = ()                          # bronze tables it writes (default: its own table)
    # skip a file identical to one already loaded under another name. Not for invoices: a
    # supplier sending the same invoice twice is a business fact the invoice audit must see.
    skip_identical_files: bool = True

    def __post_init__(self):
        if not self.tables:
            object.__setattr__(self, "tables", (self.table,))


# ------------------------------------------------------------------ readers
from .readers import _single, read_csv, read_excel, read_invoice  # noqa: E402  (shared with Databricks)


def sources() -> list[Source]:
    """Built on demand so a moved data folder (RI_DATA_DIR) is honoured."""
    return [
        Source("erp_products", config.RAW_CATALOG, "erp_products*.csv", _single("erp_products", read_csv)),
        Source("erp_price_changes", config.RAW_CATALOG, "erp_price_changes*.csv",
               _single("erp_price_changes", read_csv)),
        Source("contract_prices", config.RAW_CONTRACTS, "*.xlsx", _single("contract_prices", read_excel)),
        Source("fx_rates", config.RAW_FX, "*.csv", _single("fx_rates", read_csv)),
        Source("competitor_snapshots", config.RAW_COMPETITOR, "*.csv", _single("competitor_snapshots", read_csv)),
        Source("sales_web", config.RAW_SALES_WEB, "*.csv", _single("sales_web", read_csv)),
        Source("sales_marketplace", config.RAW_SALES_MARKETPLACE, "*.csv", _single("sales_marketplace", read_csv)),
        Source("sales_store", config.RAW_SALES_STORE, "*.xlsx", _single("sales_store", read_excel)),
        Source("invoices", config.RAW_INVOICES, "*.pdf", read_invoice, ("invoice_headers", "invoice_lines"),
               skip_identical_files=False),
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
    _q(con, """
        create table if not exists bronze._schema_changes (
            batch_id varchar, table_name varchar, column_name varchar, source_file varchar,
            detected_at timestamp
        )""")


def _table_exists(con, table: str) -> bool:
    return bool(_q(con, "select count(*) from information_schema.tables "
                        "where table_schema = 'bronze' and table_name = ?", [table])[0][0])


def _write(con, table: str, df: pd.DataFrame, meta: dict) -> None:
    df = df.copy()
    df["_row_number"] = [str(i) for i in range(1, len(df) + 1)]
    for k, v in meta.items():
        df[k] = v
    con.register("incoming", df)
    try:
        if not _table_exists(con, table):
            _q(con, f"create table bronze.{table} as select * from incoming")
        else:
            current = {r[0] for r in _q(con, f"describe bronze.{table}")}
            for col in df.columns:                       # schema drift: a new column appears
                if col not in current:
                    log.warning("bronze.%s: new column %s in %s", table, col, meta["_source_file"])
                    _q(con, f'alter table bronze.{table} add column "{col}" varchar')
                    _q(con, "insert into bronze._schema_changes values (?, ?, ?, ?, ?)",
                       [meta["_batch_id"], table, col, meta["_source_file"], dt.datetime.now()])
            _q(con, f"insert into bronze.{table} by name select * from incoming")
    finally:
        con.unregister("incoming")


def _delete_rows(con, tables: tuple[str, ...], rel: str) -> None:
    for table in tables:
        if _table_exists(con, table):
            _q(con, f"delete from bronze.{table} where _source_file = ?", [rel])


def _log(con, batch, source, rel, digest, status, rows, error) -> None:
    _q(con, "insert into bronze._ingestion_log values (?, ?, ?, ?, ?, ?, ?, ?)",
       [batch, source, rel, digest, status, rows, error, dt.datetime.now()])


def ingest_all(db_path: Path | None = None) -> dict:
    """Mirror the landing zone into bronze. Every action is written to bronze._ingestion_log:

    LOADED          new or changed file, rows written (a changed file replaces its own rows)
    FAILED          the file could not be read or written; the run carries on
    DUPLICATE_FILE  same content as a file already loaded under another name: not loaded
    REMOVED         the file is no longer in the landing zone: its rows are taken out of bronze
    Unchanged files are skipped.
    """
    db_path = db_path or config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    _ensure_log(con)
    batch = dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    state = {r[0]: {"source": r[1], "hash": r[2], "status": r[3]} for r in _q(con, """
        select source_file, source, file_hash, status from bronze._ingestion_log
        qualify row_number() over (partition by source_file order by ingested_at desc) = 1""")}
    summary = {"loaded": 0, "skipped": 0, "failed": 0, "duplicates": 0, "removed": 0, "rows": 0}

    for src in sources():
        files = sorted(src.folder.glob(src.pattern))
        present = {p.relative_to(config.DATA.parent).as_posix() for p in files}

        # 1) files that were there before and are gone now
        for rel, st in list(state.items()):
            if st["source"] == src.table and rel not in present and st["status"] != "REMOVED":
                _delete_rows(con, src.tables, rel)
                _log(con, batch, src.table, rel, st["hash"], "REMOVED", 0, "file no longer in the landing zone")
                log.warning("%s: no longer in the landing zone, its rows were taken out", rel)
                state[rel] = {**st, "status": "REMOVED"}
                summary["removed"] += 1

        # content currently loaded in bronze for this source: hash -> file
        loaded = {st["hash"]: rel for rel, st in state.items()
                  if st["source"] == src.table and st["status"] == "LOADED"}

        # 2) new, changed or re-sent files
        for path in files:
            rel = path.relative_to(config.DATA.parent).as_posix()
            digest = _sha256(path)
            known = state.get(rel)
            twin = loaded.get(digest) if src.skip_identical_files else None
            if known and known["hash"] == digest and (
                    known["status"] == "LOADED" or (known["status"] == "DUPLICATE_FILE" and twin and twin != rel)):
                summary["skipped"] += 1
                continue
            if twin and twin != rel:                     # identical content already loaded under another name
                _delete_rows(con, src.tables, rel)
                _log(con, batch, src.table, rel, digest, "DUPLICATE_FILE", 0, f"same content as {twin}")
                log.warning("%s: same content as %s, not loaded again", rel, twin)
                state[rel] = {"source": src.table, "hash": digest, "status": "DUPLICATE_FILE"}
                summary["duplicates"] += 1
                continue
            try:
                frames = src.reader(path)
            except Exception as exc:                     # any unreadable file: log it and carry on
                _log(con, batch, src.table, rel, digest, "FAILED", 0, f"{type(exc).__name__}: {exc}")
                log.error("%s: %s: %s", rel, type(exc).__name__, exc)
                state[rel] = {"source": src.table, "hash": digest, "status": "FAILED"}
                summary["failed"] += 1
                continue
            stamp = dt.datetime.now().isoformat(timespec="seconds")
            try:
                _q(con, "begin")
                _delete_rows(con, src.tables, rel)       # same name, new content: replace its rows
                rows = 0
                for table, df in frames.items():
                    _write(con, table, df, {"_source_file": rel, "_file_hash": digest,
                                            "_batch_id": batch, "_ingested_at": stamp})
                    rows += len(df)
                _log(con, batch, src.table, rel, digest, "LOADED", rows, None)
                _q(con, "commit")
            except Exception as exc:                     # nothing half-written stays behind
                _q(con, "rollback")
                _log(con, batch, src.table, rel, digest, "FAILED", 0, f"write failed: {type(exc).__name__}: {exc}")
                log.error("%s: write failed: %s", rel, exc)
                summary["failed"] += 1
                continue
            if known and loaded.get(known["hash"]) == rel:
                del loaded[known["hash"]]
            loaded[digest] = rel
            state[rel] = {"source": src.table, "hash": digest, "status": "LOADED"}
            summary["loaded"] += 1
            summary["rows"] += rows
        log.info("%-22s %4d files", src.table, len(files))

    con.close()
    log.info("batch %s: %d files loaded (%d rows), %d unchanged, %d failed, %d duplicate files, %d removed",
             batch, summary["loaded"], summary["rows"], summary["skipped"], summary["failed"],
             summary["duplicates"], summary["removed"])
    return summary
