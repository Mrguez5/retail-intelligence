"""Generate docs/data_dictionary.md from dbt's metadata and the warehouse itself.

Descriptions, primary keys and grain come from the dbt YAML files (dbt/models/**/*.yml,
dbt/seeds/_seeds.yml, reusable texts in dbt/models/_docs.md). Data types, column order and
row counts come from DuckDB. Checks and foreign keys come from the dbt tests. Nothing is
typed twice, so the document cannot drift from the code: regenerate it after any change.

    python run.py docs
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path

import duckdb

from . import config

MANIFEST = config.ROOT / "dbt" / "target" / "manifest.json"
OUT = config.ROOT / "docs" / "data_dictionary.md"
LAYERS = [
    ("bronze", "Bronze — raw files as delivered",
     "Loaded by `pipeline/bronze.py`. No business logic: every business column is text exactly as it "
     "arrived, plus five lineage columns starting with `_`. Loaded incrementally by file hash."),
    ("reference", "Reference — hand-maintained lists",
     "Small CSV files in `dbt/seeds/`, loaded by `dbt seed`."),
    ("silver", "Silver — typed, cleaned, validated",
     "dbt models. Types, parsing, de-duplication, product matching, type-2 histories and one "
     "data-quality verdict per row. Rejected rows go to a quarantine table with their reason."),
    ("gold", "Gold — star schema and business marts",
     "dbt models ready for dashboards and Power BI: dimensions (`dim_`), facts (`fct_`) and marts (`mart_`)."),
]


def _cell(text: str | None) -> str:
    """Make a value safe inside a Markdown table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _anchor(relation: str) -> str:
    return relation.replace(".", "-").replace("_", "-")


def _ref_name(expr: str) -> str:
    m = re.search(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)", expr or "")
    if m:
        return m.group(1)
    m = re.search(r"source\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", expr or "")
    return m.group(1) if m else expr


def _load() -> tuple[dict, dict]:
    if not MANIFEST.exists():
        raise SystemExit("dbt/target/manifest.json not found: run `python run.py transform` (or dbt parse) first.")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tables: dict[str, dict] = {}          # unique_id -> info
    for uid, n in manifest["nodes"].items():
        if n["resource_type"] in ("model", "seed"):
            tables[uid] = {
                "uid": uid, "name": n["name"], "schema": n["schema"], "relation": f'{n["schema"]}.{n.get("alias") or n["name"]}',
                "kind": n["resource_type"], "description": n.get("description", ""),
                "meta": (n.get("config") or {}).get("meta") or n.get("meta") or {},
                "columns": n.get("columns", {}), "depends_on": n.get("depends_on", {}).get("nodes", []),
                "path": "dbt/" + n.get("original_file_path", "").replace("\\", "/"),
            }
    for uid, s in manifest["sources"].items():
        tables[uid] = {
            "uid": uid, "name": s["name"], "schema": s["schema"], "relation": f'{s["schema"]}.{s.get("identifier") or s["name"]}',
            "kind": "source", "description": s.get("description", ""),
            "meta": (s.get("config") or {}).get("meta") or s.get("meta") or {},
            "columns": s.get("columns", {}), "depends_on": [], "path": "pipeline/bronze.py",
        }
    return manifest, tables


def _tests(manifest: dict, tables: dict) -> tuple[dict, dict, list]:
    """Column checks, foreign keys and table-level / cross-table checks, from the dbt tests."""
    col_checks: dict[tuple[str, str], list[str]] = defaultdict(list)
    fks: dict[tuple[str, str], str] = {}
    table_checks: dict[str, list[str]] = defaultdict(list)
    singular: list[tuple[str, str, list[str]]] = []
    for n in manifest["nodes"].values():
        if n["resource_type"] != "test":
            continue
        meta = n.get("test_metadata")
        targets = [d for d in n["depends_on"]["nodes"] if d in tables]
        if not meta:                                         # singular test: a SQL file in dbt/tests
            sql = (config.ROOT / "dbt" / n["original_file_path"]).read_text(encoding="utf-8")
            header = []                                      # the leading "--" comment block explains the test
            for line in sql.splitlines():
                if not line.strip().startswith("--"):
                    break
                header.append(line.strip()[2:].strip())
            singular.append((n["name"], " ".join(header), [tables[t]["relation"] for t in targets]))
            continue
        kw = meta.get("kwargs", {})
        args = kw.get("arguments") or kw
        owner = n.get("attached_node") or (targets[0] if targets else None)
        if owner not in tables:
            continue
        column = n.get("column_name") or kw.get("column_name")
        name = meta["name"]
        if name == "relationships":
            to = _ref_name(args.get("to", ""))
            target = next((t for t in tables.values() if t["name"] == to), None)
            label = f'{target["relation"] if target else to}.{args.get("field")}'
            fks[(owner, column)] = label
            col_checks[(owner, column)].append(f"exists in `{label}`")
        elif name == "accepted_values":
            col_checks[(owner, column)].append("one of: " + ", ".join(f"`{v}`" for v in args.get("values", [])))
        elif name == "unique_combination":
            table_checks[owner].append("unique (" + ", ".join(args.get("columns", [])) + ")")
        elif column:
            col_checks[(owner, column)].append(name.replace("_", " "))
    return col_checks, fks, table_checks, singular


def generate() -> Path:
    manifest, tables = _load()
    col_checks, fks, table_checks, singular = _tests(manifest, tables)
    con = duckdb.connect(str(config.DB_PATH), read_only=True)
    physical = defaultdict(list)
    for schema, table, column, dtype, nullable in con.execute(
        "select table_schema, table_name, column_name, data_type, is_nullable from information_schema.columns "
        "order by table_schema, table_name, ordinal_position"
    ).fetchall():
        physical[f"{schema}.{table}"].append((column, dtype, nullable))
    rows = {rel: con.execute(f"select count(*) from {rel}").fetchall()[0][0]
            for rel in {t["relation"] for t in tables.values()} if rel in physical}
    by_relation = {t["relation"]: t for t in tables.values()}
    used_by = defaultdict(list)
    for t in tables.values():
        for d in t["depends_on"]:
            if d in tables:
                used_by[d].append(t["relation"])

    lines = [
        "# Data dictionary",
        "",
        f"Generated by `python run.py docs` on {dt.date.today():%Y-%m-%d} from the dbt metadata and the "
        f"warehouse `{config.DB_PATH.relative_to(config.ROOT).as_posix()}`. **Do not edit by hand**: change the "
        "descriptions in `dbt/models/**/_*.yml` (shared texts in `dbt/models/_docs.md`) and regenerate.",
        "",
        "## How to read it",
        "",
        "- **PK** — primary key: the column(s) that identify one row. Every PK is enforced by a dbt test "
        "(`unique` + `not null`, or `unique_combination` for composite keys).",
        "- **FK →** — foreign key: the value must exist in the referenced table (dbt `relationships` test).",
        "- **Type** — DuckDB type of the physical column. Bronze is all `VARCHAR` on purpose.",
        "- **Rows** — row count when the document was generated.",
        "- **Grain** — what one row represents.",
        "",
        "## Tables",
        "",
        "| Layer | Table | Grain | Primary key | Rows |",
        "|---|---|---|---|---:|",
    ]
    for schema, _, _ in LAYERS:
        for rel in sorted(r for r in by_relation if r.startswith(schema + ".")):
            t = by_relation[rel]
            pk = ", ".join(t["meta"].get("primary_key", []))
            lines.append(f"| {schema} | [`{rel}`](#{_anchor(rel)}) | {_cell(t['meta'].get('grain'))} | "
                         f"{pk} | {rows.get(rel, 0):,} |")

    for schema, title, intro in LAYERS:
        lines += ["", f"## {title}", "", intro]
        for rel in sorted(r for r in by_relation if r.startswith(schema + ".")):
            t = by_relation[rel]
            pk = t["meta"].get("primary_key", [])
            lines += ["", f'<a id="{_anchor(rel)}"></a>', f"### `{rel}`", "", t["description"] or "_No description._", ""]
            facts = [f"**Grain:** {t['meta'].get('grain', '—')}",
                     f"**Primary key:** {', '.join(f'`{c}`' for c in pk) or '—'}",
                     f"**Rows:** {rows.get(rel, 0):,}",
                     f"**Defined in:** `{t['path']}`"]
            lines.append(" · ".join(facts))
            ups = [tables[d]["relation"] for d in t["depends_on"] if d in tables]
            if ups:
                lines.append("")
                lines.append("**Built from:** " + ", ".join(f"[`{u}`](#{_anchor(u)})" for u in sorted(set(ups))))
            if used_by.get(t["uid"]):
                lines.append("")
                lines.append("**Used by:** " + ", ".join(f"[`{u}`](#{_anchor(u)})" for u in sorted(set(used_by[t["uid"]]))))
            if table_checks.get(t["uid"]):
                lines.append("")
                lines.append("**Table checks:** " + "; ".join(table_checks[t["uid"]]))
            lines += ["", "| # | Column | Type | Key | Description | Checks |", "|---:|---|---|---|---|---|"]
            documented = {k.lower(): v for k, v in t["columns"].items()}
            for i, (column, dtype, nullable) in enumerate(physical.get(rel, []), 1):
                key = []
                if column in pk:
                    key.append("PK")
                if (t["uid"], column) in fks:
                    key.append(f"FK → `{fks[(t['uid'], column)]}`")
                desc = documented.get(column.lower(), {}).get("description", "")
                checks = col_checks.get((t["uid"], column), [])
                lines.append(f"| {i} | `{column}` | {dtype} | {' '.join(key)} | {_cell(desc) or '⚠ not documented'} | "
                             f"{_cell('; '.join(checks))} |")

    lines += ["", "## Cross-table checks", "",
              "Singular dbt tests in `dbt/tests/`: each one is a query that must return no rows.", "",
              "| Test | What it guarantees | Tables |", "|---|---|---|"]
    for name, what, rels in sorted(singular):
        lines.append(f"| `{name}` | {_cell(what)} | {', '.join(f'`{r}`' for r in sorted(set(rels)))} |")

    missing = [(rel, c) for rel, t in by_relation.items() for (c, _, _) in physical.get(rel, [])
               if c.lower() not in {k.lower() for k in t["columns"]}]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(config.ROOT)}: {len(by_relation)} tables, "
          f"{sum(len(physical.get(r, [])) for r in by_relation)} columns, {len(missing)} undocumented")
    for rel, c in missing:
        print(f"  undocumented: {rel}.{c}")
    return OUT
