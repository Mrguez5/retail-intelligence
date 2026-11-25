"""Bronze layer on Databricks: land the files of a Unity Catalog Volume in Delta tables.

Same rules as pipeline/bronze.py (the local DuckDB loader), same readers (pipeline/readers.py):
- every value stored as text exactly as delivered, plus _source_file, _file_hash, _batch_id,
  _ingested_at and _row_number;
- incremental: a file is loaded once per content hash; a changed file replaces its own rows;
- a file identical to one already loaded under another name is DUPLICATE_FILE (except invoices);
- a file removed from the landing zone has its rows taken out (REMOVED);
- an unreadable file is logged as FAILED and the run carries on;
- new columns are added and recorded in bronze._schema_changes.

Writes are grouped per source (one delete + one append per table) to keep serverless usage low.
Run as a Databricks job (spark_python_task):  bronze_spark.py <repo_root|auto> <catalog>
("auto": the repository is the folder above this file, e.g. when the job checks it out from Git)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = sys.argv[1] if len(sys.argv) > 1 else "auto"
if REPO_ROOT == "auto":
    REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))
CATALOG = sys.argv[2] if len(sys.argv) > 2 else "workspace"
sys.path.insert(0, REPO_ROOT)

import pandas as pd  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql.types import StringType, StructField, StructType  # noqa: E402

from pipeline.readers import _single, read_csv, read_excel, read_invoice  # noqa: E402

spark = SparkSession.builder.getOrCreate()
SCHEMA = f"{CATALOG}.bronze"
LANDING = Path(f"/Volumes/{CATALOG}/bronze/landing")
META = ["_source_file", "_file_hash", "_batch_id", "_ingested_at"]

# (table, folder in the landing zone, glob, reader, bronze tables written, skip identical files)
SOURCES = [
    ("erp_products", "catalog", "erp_products*.csv", _single("erp_products", read_csv), ("erp_products",), True),
    ("erp_price_changes", "catalog", "erp_price_changes*.csv", _single("erp_price_changes", read_csv), ("erp_price_changes",), True),
    ("contract_prices", "suppliers", "*.xlsx", _single("contract_prices", read_excel), ("contract_prices",), True),
    ("fx_rates", "fx", "*.csv", _single("fx_rates", read_csv), ("fx_rates",), True),
    ("competitor_snapshots", "competitor", "*.csv", _single("competitor_snapshots", read_csv), ("competitor_snapshots",), True),
    ("sales_web", "sales/web", "*.csv", _single("sales_web", read_csv), ("sales_web",), True),
    ("sales_marketplace", "sales/marketplace", "*.csv", _single("sales_marketplace", read_csv), ("sales_marketplace",), True),
    ("sales_store", "sales/store", "*.xlsx", _single("sales_store", read_excel), ("sales_store",), True),
    ("invoices", "invoices", "*.pdf", read_invoice, ("invoice_headers", "invoice_lines"), False),
]


def q(sql: str):
    return spark.sql(sql)


def lit(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def table_exists(table: str) -> bool:
    return spark.catalog.tableExists(f"{SCHEMA}.{table}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_log() -> None:
    q(f"create schema if not exists {SCHEMA}")
    q(f"""create table if not exists {SCHEMA}._ingestion_log (
            batch_id string, source string, source_file string, file_hash string,
            status string, rows_loaded int, error string, ingested_at timestamp)""")
    q(f"""create table if not exists {SCHEMA}._schema_changes (
            batch_id string, table_name string, column_name string, source_file string,
            detected_at timestamp)""")


LOG_ROWS: list[tuple] = []


def log(batch, source, rel, digest, status, rows, error) -> None:
    LOG_ROWS.append((batch, source, rel, digest, status, int(rows), error, dt.datetime.now()))
    print(f"{status:<15} {rel}" + (f"  ({error})" if error else ""))


def flush_log() -> None:
    if LOG_ROWS:
        df = spark.createDataFrame(LOG_ROWS, f"batch_id string, source string, source_file string, "
                                             f"file_hash string, status string, rows_loaded int, error string, "
                                             f"ingested_at timestamp")
        df.write.mode("append").saveAsTable(f"{SCHEMA}._ingestion_log")
        LOG_ROWS.clear()


def delete_files(tables, rels) -> None:
    if not rels:
        return
    in_list = ", ".join(lit(r) for r in rels)
    for table in tables:
        if table_exists(table):
            q(f"delete from {SCHEMA}.{table} where _source_file in ({in_list})")


def append(table: str, frame: pd.DataFrame, batch: str) -> None:
    """Append text columns; add (and record) columns the table does not have yet."""
    cols = list(frame.columns)
    sdf = spark.createDataFrame(frame.astype(object).where(frame.notna(), None),
                                StructType([StructField(c, StringType(), True) for c in cols]))
    full = f"{SCHEMA}.{table}"
    if not table_exists(table):
        sdf.write.format("delta").saveAsTable(full)
        return
    current = set(spark.table(full).columns)
    new = [c for c in cols if c not in current]
    if new:
        q(f"alter table {full} add columns ({', '.join(f'`{c}` string' for c in new)})")
        changes = [(batch, table, c, ", ".join(sorted(set(frame.loc[frame[c].notna(), '_source_file']))) or None,
                    dt.datetime.now()) for c in new]
        spark.createDataFrame(changes, "batch_id string, table_name string, column_name string, "
                                       "source_file string, detected_at timestamp") \
            .write.mode("append").saveAsTable(f"{SCHEMA}._schema_changes")
        print(f"schema drift: {full} new columns {new}")
    sdf.write.mode("append").option("mergeSchema", "false").saveAsTable(full)


def main() -> None:
    ensure_log()
    batch = dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    state = {r.source_file: {"source": r.source, "hash": r.file_hash, "status": r.status} for r in q(f"""
        select source_file, source, file_hash, status from {SCHEMA}._ingestion_log
        qualify row_number() over (partition by source_file order by ingested_at desc) = 1""").collect()}
    summary = {"loaded": 0, "skipped": 0, "failed": 0, "duplicates": 0, "removed": 0, "rows": 0}

    for table, folder, pattern, reader, tables, skip_identical in SOURCES:
        files = sorted((LANDING / folder).glob(pattern))
        rel_of = {p: "data/raw/" + p.relative_to(LANDING).as_posix() for p in files}   # same names as locally
        present = set(rel_of.values())

        # 1) files gone from the landing zone
        gone = [rel for rel, st in state.items()
                if st["source"] == table and rel not in present and st["status"] != "REMOVED"]
        delete_files(tables, gone)
        for rel in gone:
            log(batch, table, rel, state[rel]["hash"], "REMOVED", 0, "file no longer in the landing zone")
            state[rel] = {**state[rel], "status": "REMOVED"}
            summary["removed"] += 1

        loaded = {st["hash"]: rel for rel, st in state.items() if st["source"] == table and st["status"] == "LOADED"}
        frames: dict[str, list[pd.DataFrame]] = {t: [] for t in tables}
        to_replace, to_log = [], []

        # 2) decide, file by file, exactly as the local loader does
        for path in files:
            rel = rel_of[path]
            digest = sha256(path)
            known = state.get(rel)
            twin = loaded.get(digest) if skip_identical else None
            if known and known["hash"] == digest and (
                    known["status"] == "LOADED" or (known["status"] == "DUPLICATE_FILE" and twin and twin != rel)):
                summary["skipped"] += 1
                continue
            if twin and twin != rel:
                to_replace.append(rel)
                log(batch, table, rel, digest, "DUPLICATE_FILE", 0, f"same content as {twin}")
                state[rel] = {"source": table, "hash": digest, "status": "DUPLICATE_FILE"}
                summary["duplicates"] += 1
                continue
            try:
                result = reader(path)
            except Exception as exc:                   # unreadable file: log it and carry on
                log(batch, table, rel, digest, "FAILED", 0, f"{type(exc).__name__}: {exc}")
                state[rel] = {"source": table, "hash": digest, "status": "FAILED"}
                summary["failed"] += 1
                continue
            rows = 0
            for t, df in result.items():
                df = df.copy()
                df["_row_number"] = [str(i) for i in range(1, len(df) + 1)]
                df["_source_file"], df["_file_hash"], df["_batch_id"], df["_ingested_at"] = rel, digest, batch, stamp
                frames[t].append(df)
                rows += len(df)
            to_replace.append(rel)
            to_log.append((rel, digest, rows, known))

        # 3) one delete + one append per table for the whole source
        try:
            delete_files(tables, to_replace)
            for t, parts in frames.items():
                if parts:
                    append(t, pd.concat(parts, ignore_index=True), batch)
        except Exception as exc:                       # nothing half-written stays behind
            delete_files(tables, [rel for rel, *_ in to_log])
            for rel, digest, _, _ in to_log:
                log(batch, table, rel, digest, "FAILED", 0, f"write failed: {type(exc).__name__}: {exc}")
                summary["failed"] += 1
            flush_log()
            continue
        for rel, digest, rows, known in to_log:
            log(batch, table, rel, digest, "LOADED", rows, None)
            if known and loaded.get(known["hash"]) == rel:
                del loaded[known["hash"]]
            loaded[digest] = rel
            state[rel] = {"source": table, "hash": digest, "status": "LOADED"}
            summary["loaded"] += 1
            summary["rows"] += rows
        flush_log()
        print(f"{table:<22} {len(files):4d} files")

    print(f"batch {batch}: {summary}")


main()
