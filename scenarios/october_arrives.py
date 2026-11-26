"""Scenario "October arrives": replay the arrival of a new month, with the surprises real sources bring.

    python scenarios/october_arrives.py rewind  --target local|databricks   # landing zone up to September
    python scenarios/october_arrives.py arrive  --target local|databricks   # October's 16 files + 3 surprises
    python scenarios/october_arrives.py restore --target local|databricks   # back to the full data, no surprises

Run the pipeline after each step (local: run.py ingest + transform; Databricks: deploy.py pipeline)
and watch how each layer reacts. Use RI_SIMULATION=0 / --real-data while the landing zone is not
the full generated dataset: the test that compares the defects found with the generator's register
of injected defects only holds for the complete data.

The surprises that come with October:
- resent file:  web_orders_2026-10 (1).csv, byte-identical to web_orders_2026-10.csv
                -> bronze logs it as DUPLICATE_FILE and loads nothing (sales are not doubled);
- corrupt file: NWD_NWD-2026-0021.pdf, a PDF cut off half way through the upload
                -> bronze logs it as FAILED and every other file still loads;
- duplicate invoice: NWD_NWD-2026-0020_resend.pdf, the supplier sends invoice NWD-2026-0020 again
                -> silver marks it as a duplicate, the audit says BLOCK (do not pay it twice).

local: works on the isolated copy RI_DATA_DIR (default data/warehouse/demo), never on data/raw.
databricks: works on the Volume /Volumes/<catalog>/bronze/landing (data/raw stays the master copy).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "data" / "raw"
OCTOBER = re.compile(r"(2026-10|_202610\d\d_|NWD-2026-00(19|20)\.pdf$|F26-00(19|20)\.pdf$)")


def october_files() -> list[str]:
    return sorted(p.relative_to(MASTER).as_posix() for p in MASTER.rglob("*")
                  if p.is_file() and OCTOBER.search(p.name))


def surprises() -> dict[str, bytes]:
    web = (MASTER / "sales/web/web_orders_2026-10.csv").read_bytes()
    invoice = (MASTER / "invoices/NWD_NWD-2026-0020.pdf").read_bytes()
    return {
        "sales/web/web_orders_2026-10 (1).csv": web,                    # resent file
        "invoices/NWD_NWD-2026-0021.pdf": invoice[: len(invoice) // 2],  # upload cut off half way
        "invoices/NWD_NWD-2026-0020_resend.pdf": invoice,               # same invoice sent twice
    }


class Local:
    def __init__(self) -> None:
        data = Path(os.environ.get("RI_DATA_DIR", ROOT / "data" / "warehouse" / "demo")).resolve()
        if data == (ROOT / "data").resolve():
            raise SystemExit("refusing to touch data/raw: set RI_DATA_DIR to an isolated copy")
        self.raw = data / "raw"
        if not self.raw.exists():
            shutil.copytree(MASTER, self.raw)
            print(f"isolated copy created: {self.raw}")
        self.where = str(self.raw)

    def put(self, rel: str, content: bytes) -> None:
        (self.raw / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.raw / rel).write_bytes(content)

    def delete(self, rel: str) -> None:
        (self.raw / rel).unlink(missing_ok=True)


class Databricks:
    def __init__(self) -> None:
        import io
        from databricks.sdk import WorkspaceClient
        self.io, self.w = io, WorkspaceClient()
        self.where = f"/Volumes/{os.environ.get('RI_DATABRICKS_CATALOG', 'workspace')}/bronze/landing"

    def put(self, rel: str, content: bytes) -> None:
        self.w.files.upload(f"{self.where}/{rel}", self.io.BytesIO(content), overwrite=True)

    def delete(self, rel: str) -> None:
        try:
            self.w.files.delete(f"{self.where}/{rel}")
        except Exception:
            pass                                    # already gone


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["rewind", "arrive", "restore"])
    ap.add_argument("--target", choices=["local", "databricks"], required=True)
    args = ap.parse_args()
    zone = Local() if args.target == "local" else Databricks()
    october, extra = october_files(), surprises()

    if args.step == "rewind":                       # the landing zone as it was on 30 September
        for rel in october + list(extra):
            zone.delete(rel)
        print(f"{zone.where}: {len(october)} October files taken out, landing zone ends in September")
    elif args.step == "arrive":                     # 1 October onwards: the new files, as delivered
        for rel in october:
            zone.put(rel, (MASTER / rel).read_bytes())
            print(f"  + {rel}")
        for rel, content in extra.items():
            zone.put(rel, content)
            print(f"  + {rel}   <- surprise")
        print(f"{zone.where}: {len(october)} October files + {len(extra)} surprises arrived")
    else:                                           # back to the complete generated dataset
        for rel in extra:
            zone.delete(rel)
        for rel in october:
            zone.put(rel, (MASTER / rel).read_bytes())
        print(f"{zone.where}: surprises removed, all {len(october)} October files in place (full data)")


if __name__ == "__main__":
    main()
