"""Optional live source: take a real snapshot of books.toscrape.com.

books.toscrape.com is a public sandbox made for practising scraping. The snapshot
is written in exactly the same shape as the simulated runs, so bronze ingests it
with no changes: one CSV per run in data/raw/competitor/.

    python run.py scrape

Polite by design: robots.txt is checked, one request at a time, a fixed delay,
retries with back-off on 429/5xx, and an identifying User-Agent.
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

BASE = "https://books.toscrape.com/"
USER_AGENT = "Mozilla/5.0 (compatible; portfolio-price-monitor/1.0)"
CATEGORY_FIXES = {"Default": "Uncategorised", "Add a comment": "Uncategorised"}
log = logging.getLogger("scraper")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=4, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def run_snapshot(out_dir: Path = config.RAW_COMPETITOR, delay: float = 0.5,
                 categories: list[str] | None = None) -> Path:
    robots = RobotFileParser(urljoin(BASE, "robots.txt"))
    robots.read()
    s = _session()

    def get(url: str) -> BeautifulSoup:
        if not robots.can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt disallows {url}")
        time.sleep(delay)
        r = s.get(url, timeout=20)
        r.raise_for_status()
        r.encoding = "utf-8"
        return BeautifulSoup(r.text, "html.parser")

    started = dt.datetime.now()
    home = get(BASE)
    links = {a.get_text(strip=True): urljoin(BASE, a["href"])
             for a in home.select("div.side_categories ul li ul li a")}
    if categories:
        links = {k: v for k, v in links.items() if k in categories}

    rows, requests_made = [], 1
    for name, url in links.items():
        while url:
            page = get(url)
            requests_made += 1
            for art in page.select("article.product_pod"):
                a = art.select_one("h3 a")
                price = re.sub(r"[^0-9.]", "", art.select_one("p.price_color").get_text())
                rows.append({
                    "scraped_at": started.strftime("%Y-%m-%d %H:%M:%S"),
                    "url": urljoin(url, a["href"]),
                    "title": a["title"],
                    "category": CATEGORY_FIXES.get(name, name),
                    "price": price,
                    "in_stock": "In stock" in art.select_one("p.availability").get_text(),
                })
            nxt = page.select_one("li.next a")
            url = urljoin(url, nxt["href"]) if nxt else None

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"books_snapshot_{started:%Y%m%d_%H%M}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    log.info("%d products from %d categories in %.0fs (%d requests) -> %s",
             len(rows), len(links), (dt.datetime.now() - started).total_seconds(), requests_made, path.name)
    return path
