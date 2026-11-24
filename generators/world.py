"""The fictional "ground truth" every source file is cut from.

Inkwell Books (fictional) sells 240 titles online, on a marketplace and in one shop.
Its competitor's catalogue is the public scraping sandbox books.toscrape.com (prices in GBP).
Everything here is simulated from a fixed seed, so every run produces the same files.

The simulation is built so the analysis has something real to find:
- demand drops when Inkwell is more expensive than the competitor (price elasticity),
- Inkwell re-prices only part of the catalogue in April and August,
- one supplier bills its July price increase early, another overbills a few lines.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pipeline import config

N_PRODUCTS = 240
SUPPLIERS = {
    "NWD": "Northwind Book Distribution Ltd.",   # Irish supplier, English invoices, VAT reverse charge
    "PLW": "Paperleaf Distribuciones S.L.",      # Spanish supplier, Spanish invoices, IVA 4 %
}
PLW_PRICE_INCREASE = dt.date(2026, 7, 1)
REPRICING_DAYS = [dt.date(2026, 4, 1), dt.date(2026, 8, 1)]
# One title is put on clearance at 28 % of its price. Earlier full-price sales of it are
# correct, but more than 3x today's list price: a check against the CURRENT price would
# flag them by mistake. The pipeline must compare with the price valid on the sale date.
CLEARANCE_DAY = dt.date(2026, 9, 1)
CLEARANCE_FACTOR = 0.28


@dataclass
class World:
    days: pd.DatetimeIndex
    competitor: pd.DataFrame      # 1,000 books: url, title, category, in_stock (base)
    comp_price_gbp: np.ndarray    # [book, day]
    comp_in_stock: np.ndarray     # [book, day] bool
    fx: pd.Series                 # EUR->GBP per calendar day (true value, filled)
    products: pd.DataFrame        # Inkwell catalogue (240 rows) incl. competitor index
    our_price: np.ndarray         # [product, day] EUR
    contract: pd.DataFrame        # supplier, sku, unit_cost_eur, valid_from, valid_to
    sales_lines: pd.DataFrame     # true sales events (before any source mess)
    clearance: dict | None = None # the clearance title (sku, day, prices)


def _stable_hash(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)


def _charm_price(x: float) -> float:
    """Round to a retail-looking price: 12.49, 12.99 ..."""
    return max(round(round(x * 2) / 2 - 0.01, 2), 4.99)


def build(rng: np.random.Generator) -> World:
    days = pd.date_range(config.START, config.END, freq="D")
    n_days = len(days)

    # ---------------------------------------------------------------- competitor
    comp = pd.read_csv(config.SEED / "books_catalog_seed.csv").reset_index(drop=True)
    n_books = len(comp)
    price = np.empty((n_books, n_days))
    for b in range(n_books):
        p = float(comp.loc[b, "price"])
        path = np.empty(n_days)
        for d in range(n_days):
            if rng.random() < 1 / 45:                         # list price change every ~45 days
                p = round(p * (1 + rng.choice([-1, 1]) * rng.uniform(0.03, 0.12)), 2)
                p = float(np.clip(p, 8.0, 65.0))
            path[d] = p
        if rng.random() < 0.25:                               # a two-week promotion
            s = rng.integers(0, n_days - 14)
            path[s:s + 14] *= 1 - rng.uniform(0.15, 0.25)
        price[b] = np.round(path, 2)
    # category campaign: Fantasy -20 % for three weeks in May
    fantasy = (comp["category"] == "Fantasy").to_numpy()
    may = (days >= "2026-05-04") & (days < "2026-05-25")
    price[np.ix_(fantasy, may)] = np.round(price[np.ix_(fantasy, may)] * 0.8, 2)

    in_stock = rng.random((n_books, n_days)) > 0.04
    # stock-outs come in runs, not single days
    for b in rng.choice(n_books, 60, replace=False):
        s = rng.integers(0, n_days - 20)
        in_stock[b, s:s + rng.integers(7, 21)] = False

    # ---------------------------------------------------------------- FX
    steps = rng.normal(0, 0.0018, n_days)
    eur_gbp = pd.Series(np.round(0.858 + np.cumsum(steps), 4), index=days)

    # ---------------------------------------------------------------- our catalogue
    candidates = comp[comp["category"] != "Uncategorised"].drop_duplicates("title")
    pick = candidates.sample(N_PRODUCTS, random_state=int(rng.integers(1e9))).sort_values("title")
    products = pd.DataFrame({
        "comp_idx": pick.index.to_numpy(),
        "title": pick["title"].to_numpy(),
        "category": pick["category"].to_numpy(),
    }).reset_index(drop=True)
    products.insert(0, "sku", [f"INK-{i:05d}" for i in range(1, N_PRODUCTS + 1)])
    products["supplier_code"] = [
        "NWD" if _stable_hash(c) % 2 == 0 else "PLW" for c in products["category"]
    ]
    products["popularity"] = rng.lognormal(0, 0.9, N_PRODUCTS)

    comp_eur = price[products["comp_idx"]] / eur_gbp.to_numpy()[None, :]
    start_factor = np.clip(rng.normal(1.04, 0.07, N_PRODUCTS), 0.88, 1.25)
    our = np.empty((N_PRODUCTS, n_days))
    current = np.array([_charm_price(comp_eur[i, 0] * start_factor[i]) for i in range(N_PRODUCTS)])
    reprice_idx = {days.get_loc(pd.Timestamp(d)) for d in REPRICING_DAYS}
    for d in range(n_days):
        if d in reprice_idx:
            gap = current / comp_eur[:, d] - 1
            for i in np.where(gap > 0.08)[0]:
                if rng.random() < 0.6:                         # only part of the catalogue is fixed
                    current[i] = _charm_price(comp_eur[i, d] * 1.02)
        our[:, d] = current

    # ---------------------------------------------------------------- contracts
    base_cost = np.round(our[:, 0] * rng.uniform(0.52, 0.62, N_PRODUCTS), 2)
    rows = []
    for i, r in products.iterrows():
        if r["supplier_code"] == "PLW":
            rows.append((r["supplier_code"], r["sku"], base_cost[i], config.START, PLW_PRICE_INCREASE - dt.timedelta(days=1)))
            rows.append((r["supplier_code"], r["sku"], round(base_cost[i] * 1.04, 2), PLW_PRICE_INCREASE, None))
        else:
            rows.append((r["supplier_code"], r["sku"], base_cost[i], config.START, None))
    contract = pd.DataFrame(rows, columns=["supplier_code", "sku", "unit_cost_eur", "valid_from", "valid_to"])

    # ---------------------------------------------------------------- demand
    weekday = np.array([0.9, 0.85, 0.9, 1.0, 1.15, 1.35, 1.1])
    month = {1: 1.1, 2: 0.85, 3: 0.9, 4: 1.15, 5: 0.95, 6: 0.9, 7: 1.05, 8: 1.1, 9: 1.0, 10: 1.05}
    comp_stock = in_stock[products["comp_idx"]]
    lines = []
    for d, day in enumerate(days):
        gap = our[:, d] / comp_eur[:, d] - 1
        elastic = np.exp(-3.2 * np.clip(gap, 0, None)) * (1 + 0.6 * np.clip(-gap, 0, None))
        lam = 0.24 * products["popularity"].to_numpy() * weekday[day.weekday()] * month[day.month] * elastic
        lam = lam * np.where(comp_stock[:, d], 1.0, 1.3)
        units = rng.poisson(lam)
        for i in np.nonzero(units)[0]:
            left = int(units[i])
            while left > 0:
                q = int(min(left, rng.choice([1, 1, 1, 1, 2, 2, 3])))
                lines.append((day, i, q))
                left -= q
    # clearance: applied after demand so the random stream (and every other number) is unchanged;
    # the title is the most expensive of the better-selling half of the catalogue
    cday = days.get_loc(pd.Timestamp(CLEARANCE_DAY))
    popular = products["popularity"].to_numpy() >= np.median(products["popularity"])
    ci = int(np.argmax(np.where(popular, our[:, cday], -1)))
    before = float(our[ci, cday - 1])
    our[ci, cday:] = _charm_price(before * CLEARANCE_FACTOR)
    clearance = {"sku": products.loc[ci, "sku"], "day": CLEARANCE_DAY.isoformat(),
                 "price_before": before, "price_after": float(our[ci, cday])}
    products["list_price_eur"] = our[:, -1]

    sales = pd.DataFrame(lines, columns=["date", "pidx", "qty"])
    sales["sku"] = products["sku"].to_numpy()[sales["pidx"]]
    sales["unit_price_eur"] = our[sales["pidx"], days.get_indexer(sales["date"])]
    sales["channel"] = rng.choice(["web", "marketplace", "store"], len(sales), p=[0.55, 0.30, 0.15])
    seconds = rng.integers(8 * 3600, 23 * 3600, len(sales))
    sales["ts"] = sales["date"] + pd.to_timedelta(seconds, unit="s")
    sales = sales.sort_values("ts").reset_index(drop=True)

    return World(
        days=days, competitor=comp, comp_price_gbp=price, comp_in_stock=in_stock,
        fx=eur_gbp, products=products, our_price=our, contract=contract, sales_lines=sales,
        clearance=clearance,
    )
