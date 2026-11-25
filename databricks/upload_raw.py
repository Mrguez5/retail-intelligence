"""Upload the raw source files (data/raw) to a Unity Catalog Volume, the landing zone of the
Databricks version of the pipeline.

    .venv-databricks/Scripts/python databricks/upload_raw.py            # upload new/changed files
    .venv-databricks/Scripts/python databricks/upload_raw.py --dry-run  # only list what would change

Connection comes from the environment (DATABRICKS_HOST, DATABRICKS_TOKEN), as for dbt.
Creates <catalog>.bronze and the managed volume <catalog>.bronze.landing if they do not exist.
A file is uploaded only when it is missing or its size differs; files removed locally are
removed from the volume too, so the landing zone mirrors data/raw.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.catalog import VolumeType

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CATALOG = os.environ.get("RI_DATABRICKS_CATALOG", "workspace")
SCHEMA, VOLUME = "bronze", "landing"
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"


def ensure_volume(w: WorkspaceClient) -> None:
    try:
        w.schemas.get(f"{CATALOG}.{SCHEMA}")
    except NotFound:
        w.schemas.create(name=SCHEMA, catalog_name=CATALOG, comment="Raw files as delivered (landing) and bronze tables")
        print(f"created schema {CATALOG}.{SCHEMA}")
    try:
        w.volumes.read(f"{CATALOG}.{SCHEMA}.{VOLUME}")
    except NotFound:
        w.volumes.create(catalog_name=CATALOG, schema_name=SCHEMA, name=VOLUME,
                         volume_type=VolumeType.MANAGED, comment="Landing zone: source files exactly as delivered")
        print(f"created volume {CATALOG}.{SCHEMA}.{VOLUME}")


def remote_files(w: WorkspaceClient, path: str = VOLUME_PATH) -> dict[str, int]:
    """Relative path -> size of every file already in the volume."""
    found: dict[str, int] = {}
    try:
        entries = list(w.files.list_directory_contents(path))
    except NotFound:
        return found
    for e in entries:
        if e.is_directory:
            found.update(remote_files(w, e.path))
        else:
            found[e.path[len(VOLUME_PATH) + 1:]] = e.file_size or 0
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    w = WorkspaceClient()
    if not args.dry_run:
        ensure_volume(w)
    local = {p.relative_to(RAW).as_posix(): p for p in sorted(RAW.rglob("*")) if p.is_file()}
    remote = remote_files(w)

    to_upload = [r for r, p in local.items() if remote.get(r) != p.stat().st_size]
    to_delete = [r for r in remote if r not in local]
    print(f"local {len(local)} files, in volume {len(remote)}: upload {len(to_upload)}, delete {len(to_delete)}")
    if args.dry_run:
        return
    for i, rel in enumerate(to_upload, start=1):
        with open(local[rel], "rb") as f:
            w.files.upload(f"{VOLUME_PATH}/{rel}", f, overwrite=True)
        if i % 25 == 0 or i == len(to_upload):
            print(f"  uploaded {i}/{len(to_upload)}")
    for rel in to_delete:
        w.files.delete(f"{VOLUME_PATH}/{rel}")
        print(f"  deleted {rel}")
    print(f"done: {VOLUME_PATH}")


if __name__ == "__main__":
    main()
