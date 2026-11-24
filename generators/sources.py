"""Write the World as the messy files each real source would deliver.

Every defect injected here is deliberate and is caught (and counted) by the pipeline.
"""
from __future__ import annotations

import datetime as dt
import shutil

import numpy as np
import pandas as pd

from pipeline import config
from .world import World


def _reset(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


# ------------------------------------------------------------------ catalogue (ERP export)
def write_catalog(w: World, rng: np.random.Generator) -> None:
    _reset(config.RAW_CATALOG)
    p = w.products.copy()
    p["competitor_url"] = w.competitor.loc[p["comp_idx"], "url"].to_numpy()
    p.loc[rng.random(len(p)) < 0.2, "competitor_url"] = ""     # 20 % never linked in the ERP
    p["active"] = "Y"
    p[["sku", "title", "category", "supplier_code", "list_price_eur", "competitor_url", "active"]].to_csv(
        config.RAW_CATALOG / "erp_products_export.csv", index=False, encoding="utf-8")


# ------------------------------------------------------------------ list price history (ERP)
def write_price_history(w: World) -> dict:
    """ERP log of list-price changes: one row each time a price changes.

    Titles migrated from the old ERP have no history (every 12th SKU), so the
    pipeline needs a fallback for them. Deterministic: does not use the random stream.
    """
    rows, missing = [], []
    for i, sku in enumerate(w.products["sku"]):
        if int(sku[4:]) % 12 == 0:
            missing.append(sku)
            continue
        prices = w.our_price[i]
        for d in range(len(w.days)):
            if d == 0 or prices[d] != prices[d - 1]:
                rows.append((sku, f"{prices[d]:.2f}", w.days[d].strftime("%Y-%m-%d")))
    pd.DataFrame(rows, columns=["sku", "list_price_eur", "valid_from"]).to_csv(
        config.RAW_CATALOG / "erp_price_changes.csv", index=False)
    return {"rows": len(rows), "skus_without_history": len(missing)}


# ------------------------------------------------------------------ supplier contracts (Excel)
def write_contracts(w: World) -> None:
    _reset(config.RAW_CONTRACTS)
    c = w.contract.merge(w.products[["sku", "title"]], on="sku")
    out = pd.DataFrame({
        "Supplier": c["supplier_code"],
        "SKU": c["sku"],
        "Title": c["title"],
        "Unit cost (EUR)": c["unit_cost_eur"],
        "Valid from": pd.to_datetime(c["valid_from"]),
        "Valid to": pd.to_datetime(c["valid_to"]),
    })
    with pd.ExcelWriter(config.RAW_CONTRACTS / "contract_prices_2026.xlsx") as xl:
        out.to_excel(xl, sheet_name="Contract prices", index=False)


# ------------------------------------------------------------------ FX (business days only)
def write_fx(w: World) -> None:
    _reset(config.RAW_FX)
    fx = w.fx[w.fx.index.dayofweek < 5]
    pd.DataFrame({"date": fx.index.strftime("%Y-%m-%d"), "eur_gbp": fx.to_numpy()}).to_csv(
        config.RAW_FX / "eur_gbp_reference_rates.csv", index=False)


# ------------------------------------------------------------------ competitor scraper runs
def write_competitor(w: World, rng: np.random.Generator) -> dict:
    _reset(config.RAW_COMPETITOR)
    run_days = [d for d in w.days if d.dayofweek in (0, 3)]        # Monday and Thursday
    failed = set(rng.choice(len(run_days), 2, replace=False))      # scraper did not run
    partial = int(rng.choice([i for i in range(len(run_days)) if i not in failed]))
    stats = {"runs": 0, "failed_runs": len(failed), "partial_runs": 1, "glitch_rows": 0}
    for k, day in enumerate(run_days):
        if k in failed:
            continue
        d = w.days.get_loc(day)
        ts = day + pd.Timedelta(hours=7, minutes=int(rng.integers(0, 40)), seconds=int(rng.integers(0, 60)))
        df = pd.DataFrame({
            "scraped_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "url": w.competitor["url"],
            "title": w.competitor["title"],
            "category": w.competitor["category"],
            "price": w.comp_price_gbp[:, d],
            "in_stock": w.comp_in_stock[:, d],
        })
        glitch = rng.random(len(df)) < 0.0004                       # parser glitches: 0.00 or x100
        df.loc[glitch, "price"] = np.where(rng.random(glitch.sum()) < 0.5, 0.0, df.loc[glitch, "price"] * 100)
        stats["glitch_rows"] += int(glitch.sum())
        if k == partial:                                            # run timed out half way
            df = df.iloc[:600]
        df.to_csv(config.RAW_COMPETITOR / f"books_snapshot_{ts:%Y%m%d_%H%M}.csv", index=False)
        stats["runs"] += 1
    return stats


# ------------------------------------------------------------------ sales: web shop
def write_sales_web(w: World, rng: np.random.Generator) -> dict:
    _reset(config.RAW_SALES_WEB)
    s = w.sales_lines[w.sales_lines["channel"] == "web"].copy()
    # group lines into orders of 1-3 lines placed at the same moment
    order_no, ids = 100000, []
    while len(ids) < len(s):
        order_no += 1
        n = min(int(rng.choice([1, 1, 1, 2, 2, 3])), len(s) - len(ids))
        ids.extend([f"W{order_no}"] * n)
    s["order_id"] = ids
    s["order_ts"] = s.groupby("order_id")["ts"].transform("min")
    s["discount_pct"] = np.where(rng.random(len(s)) < 0.15, rng.choice([5, 10], len(s)), 0)
    s["country"] = rng.choice(["ES", "PT", "FR", "IE", "DE", "IT"], len(s), p=[0.55, 0.1, 0.12, 0.08, 0.1, 0.05])
    # returns: 2 % of lines come back 3-20 days later
    ret = s.sample(frac=0.02, random_state=int(rng.integers(1e9))).copy()
    ret["order_ts"] = ret["order_ts"] + pd.to_timedelta(rng.integers(3, 21, len(ret)), unit="D")
    ret["order_id"] = ret["order_id"] + "-R"
    ret["qty"] = -ret["qty"]
    ret = ret[ret["order_ts"] <= pd.Timestamp(config.END) + pd.Timedelta(days=1)]
    s = pd.concat([s, ret]).sort_values("order_ts")
    out = pd.DataFrame({
        "order_id": s["order_id"],
        "order_ts": s["order_ts"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "sku": s["sku"],
        "quantity": s["qty"],
        "unit_price_eur": s["unit_price_eur"].map(lambda x: f"{x:.2f}"),
        "discount_pct": s["discount_pct"],
        "customer_country": s["country"],
    })
    month = s["order_ts"].dt.strftime("%Y-%m")
    for m, part in out.groupby(month.to_numpy()):
        part.to_csv(config.RAW_SALES_WEB / f"web_orders_{m}.csv", index=False)
    return {"lines": len(out), "returns": len(ret)}


# ------------------------------------------------------------------ sales: marketplace export
def write_sales_marketplace(w: World, rng: np.random.Generator) -> dict:
    """Spanish marketplace export: ';' separator, decimal comma, dd/mm/yyyy.

    Each monthly export also repeats the last 3 days of the previous month,
    so the files overlap and naive appends double-count sales.
    """
    _reset(config.RAW_SALES_MARKETPLACE)
    s = w.sales_lines[w.sales_lines["channel"] == "marketplace"].copy()
    s["order_id"] = [f"MKT-{n:07d}" for n in rng.choice(np.arange(1_000_000, 9_999_999), len(s), replace=False)]
    titles = w.products.set_index("sku")["title"]
    s["title"] = s["sku"].map(titles)
    s["seller_ref"] = np.where(rng.random(len(s)) < 0.10, "", s["sku"])   # seller ref sometimes missing
    s["commission"] = (s["unit_price_eur"] * s["qty"] * 0.15).round(2)

    def eu(x: float) -> str:
        return f"{x:.2f}".replace(".", ",")

    overlap_rows = 0
    for m in pd.period_range(config.START, config.END, freq="M"):
        lo = m.start_time - pd.Timedelta(days=3)
        hi = m.end_time
        part = s[(s["ts"] >= lo) & (s["ts"] <= hi)]
        overlap_rows += int((part["ts"] < m.start_time).sum())
        pd.DataFrame({
            "Nº pedido": part["order_id"],
            "Fecha": part["ts"].dt.strftime("%d/%m/%Y %H:%M"),
            "Ref. vendedor": part["seller_ref"],
            "Título": part["title"],
            "Unidades": part["qty"],
            "Precio unitario": part["unit_price_eur"].map(eu),
            "Comisión": part["commission"].map(eu),
        }).to_csv(config.RAW_SALES_MARKETPLACE / f"marketplace_ventas_{m}.csv", sep=";", index=False,
                  encoding="utf-8-sig")
    return {"lines": len(s), "overlap_rows": overlap_rows}


# ------------------------------------------------------------------ sales: physical shop (Excel by hand)
def _typo(title: str, rng: np.random.Generator) -> str:
    kind = rng.integers(0, 4)
    if kind == 0:
        return title.lower()
    if kind == 1 and len(title) > 6:                       # drop a character
        k = int(rng.integers(1, len(title) - 1))
        return title[:k] + title[k + 1:]
    if kind == 2 and len(title) > 6:                       # swap two characters
        k = int(rng.integers(1, len(title) - 2))
        return title[:k] + title[k + 1] + title[k] + title[k + 2:]
    return "  " + title.upper() + " "


def write_sales_store(w: World, rng: np.random.Generator) -> dict:
    _reset(config.RAW_SALES_STORE)
    s = w.sales_lines[w.sales_lines["channel"] == "store"].copy()
    titles = w.products.set_index("sku")["title"]
    s["book"] = s["sku"].map(titles)
    stats = {"lines": len(s), "typos": 0, "price_typos": 0, "missing_qty": 0, "gift_cards": 0, "text_dates": 0}

    typo = rng.random(len(s)) < 0.12
    s.loc[typo, "book"] = [_typo(t, rng) for t in s.loc[typo, "book"]]
    stats["typos"] = int(typo.sum())

    s["pvp"] = s["unit_price_eur"].astype(object)
    ptypo = rng.random(len(s)) < 0.005                    # 14.99 typed as 149.9
    s.loc[ptypo, "pvp"] = (s.loc[ptypo, "unit_price_eur"] * 10).round(1)
    stats["price_typos"] = int(ptypo.sum())

    s["cant"] = s["qty"].astype(object)
    miss = rng.random(len(s)) < 0.003
    s.loc[miss, "cant"] = None
    stats["missing_qty"] = int(miss.sum())

    # gift cards are not books: they must not end up as product sales
    gc_idx = rng.choice(len(s), 25, replace=False)
    gift = s.iloc[gc_idx].copy()
    gift["book"] = "Tarjeta regalo"
    gift["pvp"] = rng.choice([20.0, 30.0, 50.0], len(gift))
    gift["cant"] = 1
    s = pd.concat([s, gift]).sort_values("ts")
    stats["gift_cards"] = len(gift)

    s["fecha"] = s["ts"].dt.normalize().astype(object)
    text = rng.random(len(s)) < 0.05                      # some dates typed as text
    s.loc[text, "fecha"] = s.loc[text, "ts"].dt.strftime("%d/%m/%Y")
    stats["text_dates"] = int(text.sum())

    for m, part in s.groupby(s["ts"].dt.strftime("%Y-%m")):
        out = pd.DataFrame({"Fecha": part["fecha"], "Libro": part["book"], "Cant": part["cant"], "PVP": part["pvp"]})
        with pd.ExcelWriter(config.RAW_SALES_STORE / f"tienda_ventas_{m}.xlsx") as xl:
            out.to_excel(xl, sheet_name="Ventas", index=False)
    return stats
