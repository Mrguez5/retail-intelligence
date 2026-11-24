"""Fictional source data for the Retail Price & Procurement Intelligence demo."""
from __future__ import annotations

import json

import numpy as np

from pipeline import config
from . import invoices, sources, world


def generate_all() -> dict:
    rng = np.random.default_rng(config.RANDOM_SEED)
    w = world.build(rng)
    report = {
        "products": len(w.products),
        "competitor_books": len(w.competitor),
        "true_sales_lines": len(w.sales_lines),
    }
    sources.write_catalog(w, rng)
    sources.write_contracts(w)
    sources.write_fx(w)
    report["competitor"] = sources.write_competitor(w, rng)
    report["web"] = sources.write_sales_web(w, rng)
    report["marketplace"] = sources.write_sales_marketplace(w, rng)
    report["store"] = sources.write_sales_store(w, rng)
    report["invoices"] = invoices.write_invoices(w, rng)
    # what was injected, so tests can check the pipeline finds exactly this
    (config.RAW / "_injected_defects.json").write_text(json.dumps(report, indent=2, default=str))
    return report
