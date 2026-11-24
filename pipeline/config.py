"""Shared paths and settings for the whole project.

The data folder can be moved with the environment variable RI_DATA_DIR (used by the
robustness tests to run the whole pipeline in an isolated copy). Default: <repo>/data.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA = Path(os.environ.get("RI_DATA_DIR", ROOT / "data")).resolve()
SEED = ROOT / "data" / "seed"         # versioned input of the generator, never moved
RAW = DATA / "raw"                    # landing zone: files exactly as the sources deliver them
WAREHOUSE = DATA / "warehouse"
DB_PATH = WAREHOUSE / "retail.duckdb"

RAW_CATALOG = RAW / "catalog"         # ERP product export + list-price history (CSV)
RAW_CONTRACTS = RAW / "suppliers"     # supplier contract price list (Excel)
RAW_FX = RAW / "fx"                   # EUR/GBP reference rates (CSV, business days only)
RAW_COMPETITOR = RAW / "competitor"   # one CSV per scraper run
RAW_SALES_WEB = RAW / "sales" / "web"                  # monthly CSV, ISO formats
RAW_SALES_MARKETPLACE = RAW / "sales" / "marketplace"  # monthly CSV, ';' + decimal comma, overlapping windows
RAW_SALES_STORE = RAW / "sales" / "store"              # monthly Excel typed by hand in the shop
RAW_INVOICES = RAW / "invoices"       # supplier invoices (PDF, two layouts)

# Period covered by the fictional business (generator only; the pipeline reads dates from the data)
START = dt.date(2026, 1, 1)
END = dt.date(2026, 10, 31)

RANDOM_SEED = 2026
