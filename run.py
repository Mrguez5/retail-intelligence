"""Command line entry point.

    python run.py generate     # (re)create the fictional source files in data/raw
    python run.py ingest       # load new/changed raw files into the bronze layer (incremental)
    python run.py transform    # dbt build: silver + gold models and all data tests
    python run.py all          # generate + ingest + transform
    python run.py scrape       # optional: real scrape of books.toscrape.com into data/raw/competitor
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time

from pipeline import config

log = logging.getLogger("run")


def cmd_generate() -> None:
    from generators import generate_all
    t = time.perf_counter()
    report = generate_all()
    log.info("generated sources in %.1fs\n%s", time.perf_counter() - t, json.dumps(report, indent=2, default=str))


def cmd_ingest() -> None:
    from pipeline import bronze
    bronze.ingest_all()


def cmd_transform() -> None:
    dbt_dir = config.ROOT / "dbt"
    exe = os.path.join(os.path.dirname(sys.executable), "dbt")
    env = {**os.environ, "DBT_PROFILES_DIR": str(dbt_dir)}
    # run from the repo root: the profile and vars use paths relative to it
    res = subprocess.run([exe, "build", "--project-dir", str(dbt_dir)], env=env, cwd=config.ROOT)
    if res.returncode != 0:
        sys.exit(res.returncode)


def cmd_scrape() -> None:
    from pipeline import scraper
    scraper.run_snapshot()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["generate", "ingest", "transform", "all", "scrape"])
    args = ap.parse_args()
    if args.command in ("generate", "all"):
        cmd_generate()
    if args.command in ("ingest", "all"):
        cmd_ingest()
    if args.command in ("transform", "all"):
        cmd_transform()
    if args.command == "scrape":
        cmd_scrape()


if __name__ == "__main__":
    main()
