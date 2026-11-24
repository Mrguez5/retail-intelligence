"""Robustness tests: break the input on purpose and check the pipeline copes.

The whole pipeline runs in an isolated copy (RI_DATA_DIR points to a temporary folder):
generate -> ingest -> build (baseline), then the landing zone is damaged the way real
sources damage it, and ingest -> build run again. The build must still succeed and the
numbers must not be inflated or lost.

    pytest -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    res = subprocess.run([sys.executable, str(ROOT / "run.py"), *args], env=env, cwd=ROOT,
                         capture_output=True, text=True)
    assert res.returncode == 0, f"run.py {' '.join(args)} failed:\n{res.stdout[-3000:]}\n{res.stderr[-3000:]}"
    return res


def _query(db: Path, sql: str):
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def damaged(tmp_path_factory):
    data = tmp_path_factory.mktemp("ri") / "data"
    env = {**os.environ, "RI_DATA_DIR": str(data), "RI_SIMULATION": "0", "PYTHONIOENCODING": "utf-8"}
    raw, db = data / "raw", data / "warehouse" / "retail.duckdb"

    _run(env, "generate")
    _run(env, "ingest")
    _run(env, "transform")
    baseline = {
        "march_web": _query(db, "select sum(net_revenue_eur) from gold.fct_sales "
                                "where channel = 'web' and strftime(sale_date, '%Y-%m') = '2026-03'")[0][0],
        "march_store": _query(db, "select count(*) from gold.fct_sales "
                                  "where channel = 'store' and strftime(sale_date, '%Y-%m') = '2026-03'")[0][0],
        "may_marketplace": _query(db, "select count(*) from gold.fct_sales "
                                      "where channel = 'marketplace' and strftime(sale_date, '%Y-%m') = '2026-05'")[0][0],
        "removed_snapshot": sorted((raw / "competitor").glob("*.csv"))[10].relative_to(data.parent).as_posix(),
    }

    # A. a file that is not a PDF at all
    (raw / "invoices" / "PLW_F26-9999.pdf").write_bytes(b"this is not a pdf")
    # B. the March web and shop files delivered again under another name
    shutil.copy(raw / "sales/web/web_orders_2026-03.csv", raw / "sales/web/web_orders_2026-03_resent.csv")
    store = pd.read_excel(raw / "sales/store/tienda_ventas_2026-03.xlsx")
    store.to_excel(raw / "sales/store/tienda_ventas_2026-03_copy.xlsx", sheet_name="Ventas", index=False)
    # C. shop lines with impossible dates and an absurd quantity (title and price are fine)
    pd.DataFrame({
        "Fecha": [pd.Timestamp("2062-03-18"), pd.Timestamp("2025-12-31"), pd.Timestamp("2026-03-18")],
        "Libro": ["The Silkworm (Cormoran Strike #2)"] * 3,
        "Cant": [1, 1, 100],
        "PVP": [29.99, 29.99, 29.99],
    }).to_excel(raw / "sales/store/tienda_ventas_extra.xlsx", sheet_name="Ventas", index=False)
    # D. a marketplace re-export with a renamed column ("Unidades" -> "Uds")
    src = raw / "sales/marketplace/marketplace_ventas_2026-05.csv"
    text = src.read_text(encoding="utf-8-sig").replace(";Unidades;", ";Uds;", 1)
    (raw / "sales/marketplace/marketplace_ventas_2026-05_v2.csv").write_text(text, encoding="utf-8-sig")
    # E. a scraper file deleted from the landing zone
    (data.parent / baseline["removed_snapshot"]).unlink()
    # F. an extra FX file with an absurd rate on a Saturday
    (raw / "fx" / "eur_gbp_corrections.csv").write_text("date,eur_gbp\n2026-03-07,0.0\n", encoding="utf-8")
    # G. a shop line whose title is equally close to two catalogue titles (volumes 4 and 6)
    pd.DataFrame({"Fecha": [pd.Timestamp("2026-03-18")],
                  "Libro": ["Fruits Basket, Vol. 5 (Fruits Basket #5)"], "Cant": [1], "PVP": [9.99]}
                 ).to_excel(raw / "sales/store/tienda_ventas_ambiguous.xlsx", sheet_name="Ventas", index=False)

    _run(env, "ingest")
    _run(env, "transform")        # must succeed despite all of the above
    return db, baseline


def test_unreadable_file_is_logged_and_reported(damaged):
    db, _ = damaged
    assert _query(db, "select count(*) from bronze._ingestion_log "
                      "where source_file like '%PLW_F26-9999.pdf' and status = 'FAILED'")[0][0] == 1
    assert _query(db, "select rows_failed from gold.mart_data_quality where rule = 'file_failed_to_load'")[0][0] == 1


def test_file_resent_under_new_name_is_not_counted_twice(damaged):
    db, base = damaged
    march_web = _query(db, "select sum(net_revenue_eur) from gold.fct_sales "
                           "where channel = 'web' and strftime(sale_date, '%Y-%m') = '2026-03'")[0][0]
    assert march_web == pytest.approx(base["march_web"])
    assert _query(db, "select rows_failed from gold.mart_data_quality "
                      "where rule = 'file_resent_with_same_content'")[0][0] >= 1


def test_shop_month_resent_as_a_copy_is_not_counted_twice(damaged):
    db, base = damaged
    # the Excel copy is a different file (different bytes), so line-level de-duplication must catch it
    march_store = _query(db, "select count(*) from gold.fct_sales "
                             "where channel = 'store' and strftime(sale_date, '%Y-%m') = '2026-03'")[0][0]
    assert march_store == base["march_store"]


def test_broken_reexport_does_not_push_out_valid_lines(damaged):
    db, base = damaged
    may = _query(db, "select count(*) from gold.fct_sales "
                     "where channel = 'marketplace' and strftime(sale_date, '%Y-%m') = '2026-05'")[0][0]
    assert may == base["may_marketplace"]
    assert _query(db, "select count(*) from bronze._schema_changes where column_name = 'uds'")[0][0] == 1


def test_impossible_dates_and_quantities_are_quarantined(damaged):
    db, _ = damaged
    issues = dict(_query(db, "select dq_issue, count(*) from silver.silver_sales_quarantine "
                             "where source_file like '%tienda_ventas_extra%' group by 1"))
    assert issues == {"date_out_of_range": 2, "quantity_outlier": 1}


def test_removed_file_leaves_no_rows(damaged):
    db, base = damaged
    rel = base["removed_snapshot"]
    assert _query(db, f"select count(*) from bronze.competitor_snapshots where _source_file = '{rel}'")[0][0] == 0
    assert _query(db, "select rows_failed from gold.mart_data_quality where rule = 'file_removed_from_source'")[0][0] == 1


def test_absurd_fx_rate_is_rejected(damaged):
    db, _ = damaged
    row = _query(db, "select eur_gbp, is_rejected_rate from silver.silver_fx_daily where rate_date = date '2026-03-07'")
    assert row[0][1] is True and 0.5 < row[0][0] < 1.5       # rejected, Friday's rate carried instead
    assert _query(db, "select rows_failed from gold.mart_data_quality where rule = 'rate_rejected_out_of_range'")[0][0] == 1


def test_ambiguous_title_is_not_guessed(damaged):
    db, _ = damaged
    assert _query(db, "select dq_issue from silver.silver_sales_quarantine "
                      "where source_file like '%tienda_ventas_ambiguous%'") == [("ambiguous_product",)]


def test_second_ingest_loads_nothing(damaged):
    db, _ = damaged
    env = {**os.environ, "RI_DATA_DIR": str(db.parents[1]), "PYTHONIOENCODING": "utf-8"}
    out = _run(env, "ingest")
    assert ": 0 files loaded" in (out.stdout + out.stderr)
