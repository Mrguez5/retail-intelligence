# Retail Price & Procurement Intelligence

An end-to-end data platform for a (fictional) online bookseller: four messy real-world
sources go into a **bronze → silver → gold lakehouse** in DuckDB + dbt, with **143 automated
data tests**. The output answers three business questions:

1. **Are we more expensive than the competitor, and what does it cost us?**
2. **Are suppliers billing what the contract says?**
3. **Can we trust the numbers?** Every defect is counted, explained and traced to its source file.

Runs from scratch in about 20 seconds on a laptop, for free. No cloud account needed.

📊 **Dashboard**: [`docs/index.html`](docs/index.html), one self-contained page (open it from disk; no server, no external library). · 📖 **[Data dictionary](docs/data_dictionary.md)**: every table and column, with type, primary and foreign keys, grain, lineage and checks (also as a [PDF](docs/data_dictionary.pdf)).

> All companies, people and figures are fictional. The competitor catalogue is
> [books.toscrape.com](https://books.toscrape.com), a public sandbox built for scraping practice.

## Dashboard

![Dashboard overview](docs/img/dashboard_overview.png)

Four tabs, built by `python run.py dashboard` from the gold layer:

| Tab | Answers |
|---|---|
| **Overview** | Revenue by month and channel, margin, books sold, money at risk in supplier invoices |
| **Price vs competitor** | Weekly price gap with the two repricing rounds, units sold by price position, and the titles still much dearer than the competitor |
| **Supplier invoices** | Money at risk per invoice, by status (block a duplicate, claim an overcharge, review a total) |
| **Data quality** | Lines set aside by reason, the raw = clean + quarantined reconciliation, and every data-quality check |

Charts are hand-built SVG with hover and keyboard tooltips, a table view for every chart, and light and dark themes
(colour palette validated for colour-vision deficiency). The same views also exist as a **Power BI project**
(semantic model with DAX measures + report, generated as PBIP): see [powerbi/README.md](powerbi/README.md).
More screenshots: [prices](docs/img/dashboard_prices.png) · [invoices](docs/img/dashboard_invoices.png) · [data quality](docs/img/dashboard_quality.png) · [dark theme](docs/img/dashboard_overview_dark.png).

---

## Architecture

```mermaid
flowchart LR
    subgraph Sources["Raw sources (data/raw)"]
        A1["Competitor scraper<br/>85 runs × 1,000 books<br/>CSV, GBP"]
        A2["Supplier invoices<br/>41 PDFs, 2 layouts<br/>EN + ES formats"]
        A3["Sales: web CSV · marketplace CSV (;) ·<br/>shop Excel typed by hand"]
        A4["ERP catalogue + price history · contract prices (Excel) ·<br/>EUR/GBP rates"]
    end
    subgraph Bronze["Bronze (Python)"]
        B["Text as delivered<br/>+ lineage columns<br/>incremental by file hash"]
    end
    subgraph Silver["Silver (dbt)"]
        S1["Typed and cleaned<br/>de-duplicated<br/>products matched"]
        S2["Quarantine<br/>with the reason"]
        S3["Type-2 history<br/>contract and competitor prices"]
    end
    subgraph Gold["Gold (dbt)"]
        G1["Star schema<br/>fct_sales · fct_competitor_price_daily<br/>dim_product · dim_date · …"]
        G2["Marts<br/>price position · invoice audit ·<br/>monthly KPIs · data quality"]
    end
    A1 & A2 & A3 & A4 --> B --> S1 --> G1 --> G2
    S1 --> S2
    S1 --> S3 --> G1
```

| Layer | Tool | Rule |
|---|---|---|
| **Bronze** | Python + DuckDB | No business logic. Every value is stored as text, exactly as delivered, with `_source_file`, `_file_hash`, `_batch_id`, `_ingested_at` and `_row_number`. A file is loaded once per content hash; a changed file replaces its own rows; a file re-sent under another name is not loaded twice; a file removed from the source has its rows taken out; any file that cannot be read or written is logged as `FAILED` (nothing half-written stays) and the run carries on. New columns are added and recorded (schema drift). |
| **Silver** | dbt | Types, parsing (`1.234,56 €` and `1,234.56 EUR`, `dd/mm/yyyy` and ISO), product matching, one data-quality verdict per row, **then** de-duplication (the valid copy of a line always wins). Bad rows go to quarantine with their reason; nothing is dropped silently. |
| **Gold** | dbt | Star schema and business marts, ready for a dashboard or Power BI. |

## What the data shows (Jan–Oct 2026)

| | |
|---|---|
| Net revenue | **€1.05 M** across 3 channels, 24,691 books sold |
| Gross margin | **35.1 %** after marketplace commission and supplier cost |
| Price position | Inkwell was **+3.2 % above the competitor in January**. After the April and August repricing it was **2.9 % below**. |
| Price elasticity | Products priced *much higher* (>10 %) than the competitor sell **23 % fewer units a week** than products priced *lower* (2.82 vs 3.64). |
| Supplier invoices | **€9,695 at risk** in 12 of 41 invoices: one invoice sent twice (€8,543), a price increase billed two weeks early, 10 lines above contract, and 2 totals that don't add up |

## Data quality: every defect found and counted

The sources are messy on purpose, the way real exports are. The generator writes down every
defect it injects (`data/raw/_injected_defects.json`), and a dbt test fails the build
unless the pipeline finds **exactly** those defects.

| Source | Problem | Rows | What the pipeline does |
|---|---|---|---|
| Marketplace CSV | Monthly exports overlap by 3 days | 590 | De-duplicated (the valid copy from the latest export kept) |
| Marketplace CSV | Seller SKU missing | 697 | Matched by title |
| Shop Excel | No SKU, titles typed by hand (case, missing or swapped letters) | 3,289 | Exact title key, then fuzzy match (Damerau-Levenshtein ≤ 2 or Jaro-Winkler ≥ 0.95); score kept for audit |
| Shop Excel | Price typed ×10 (14.99 → 149.9) | 16 | Quarantined (`price_outlier`): compared with the price valid **on the sale date**, not today's |
| ERP | 20 titles migrated without price history | 1,893 lines | Price check falls back to that week's median price (≥ 3 sales), then to today's list price |
| Shop Excel | Gift cards sold through the till (not books) | 25 | Quarantined (`unknown_product`) |
| Shop Excel | Missing quantity · dates as text | 8 · 162 | Quarantined · parsed |
| Competitor scraper | Price 0.00, ×100 or ÷100 (parser glitch) | 34 | Observation ignored |
| Competitor scraper | 2 runs missing, 1 run stopped half way | 3 runs | Last valid price carried forward (ASOF join), for 7 days at most |
| ERP | Product without a link to the competitor page | 45 | Linked by unique title |
| FX rates | Weekends and holidays have no rate | 86 days | Last business-day rate, flagged, for 5 days at most; rates outside 0.5–1.5 rejected |
| Supplier PDFs | Duplicate · wrong total · price above contract | 1 · 2 · 25 lines | Listed in the invoice audit with the amount at risk |

Every bronze sales row ends up in `silver_sales` or in `silver_sales_quarantine`. A
reconciliation test checks that no row is lost.

### Damage the pipeline is built to survive

Beyond the defects above, `tests/test_robustness.py` breaks the landing zone on purpose in an isolated copy and checks the result (`python -m pytest -q`, also run in CI):

| Damage | Without protection | What happens now |
|---|---|---|
| A file that is not a valid PDF | the whole load stopped | logged as `FAILED`, reported in `mart_data_quality`, the run carries on |
| A month of web sales re-sent under another name | revenue of that month doubled | identical file not loaded again (`DUPLICATE_FILE`) |
| The shop's month copied into a new Excel | sales doubled | line-level business key (date + title + units + price) finds the copies |
| A re-export with a renamed column | 619 valid lines replaced by broken copies | lines validated **before** de-duplication: the valid copy wins; the new column is recorded |
| A sale dated 2062 or before the data starts | the whole gold layer failed to build | quarantined as `date_out_of_range` |
| 100 units typed instead of 1 | accepted | quarantined as `quantity_outlier` |
| A source file deleted | its rows stayed forever | rows taken out of bronze (`REMOVED`) |
| An FX rate of 0 | prices divided by zero | rate rejected, previous one carried |
| A typed title equally close to two books (Vol. 4 / Vol. 6) | one picked silently | quarantined as `ambiguous_product` |

Returns are checked and costed with the price and cost of the **original sale**, and the calendar, KPIs and
data-quality checks take their date ranges from the data (no dates written in the code).

**Why the price check uses the price on the sale date.** One title goes on clearance on 1 September
(€73.99 → €20.49). Its 48 earlier sales at full price are correct, but they are more than 3× today's
price. Comparing with the current ERP price would quarantine all 48 by mistake (64 flagged instead of 16).
The pipeline compares each sale with (A) the ERP list-price history, (B) if a title has no history,
the median price of that title that week across channels, or (C) as a last resort today's list price.
The source used is stored on every row (`reference_price_source`), and a test fails if any clearance
sale is flagged.

## Techniques shown

- **Medallion architecture** with lineage columns and an ingestion log
- **Incremental, idempotent ingestion** by content hash: changed, re-sent, removed and unreadable files all handled and logged, schema drift recorded
- **PDF extraction** of two supplier layouts (pdfplumber + one regex per layout), with raw values kept for replay
- **Type-2 history (SCD2)**: contract prices and list prices with validity periods, and competitor prices built with gaps-and-islands
- **Point-in-time validation**: each sale checked against the price valid that day, with a two-level fallback
- **ASOF join** for the latest competitor price at the end of each day, with a "days since observed" column
- **Fuzzy matching** of hand-typed titles, with the score and edit distance kept for audit
- **Data-quality framework**: a verdict per row, validation before de-duplication, quarantine tables, a `mart_data_quality` summary and 143 dbt tests (generic, custom generic and singular, including a primary-key test on every table)
- **Polite live scraper** (`python run.py scrape`): robots.txt, delays, retries with back-off. It writes the same file shape as the simulated runs.
- **Documentation as code**: table and column descriptions live in the dbt YAML (shared texts as dbt doc blocks); the data dictionary is generated from them plus the warehouse, so it cannot drift from the code
- **Robustness tests** (pytest): the landing zone is damaged on purpose in an isolated copy and the results are checked
- **Dashboard without dependencies**: SVG charts, hover/keyboard tooltips, table views, light/dark themes, palette checked for colour blindness
- **CI**: GitHub Actions builds the whole platform (and the dashboard) from scratch, checks that a second ingest loads nothing and runs the robustness tests

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python run.py all                 # generate sources → bronze → dbt build (models + tests)
```

| Command | Does |
|---|---|
| `python run.py generate` | (Re)creates the fictional source files in `data/raw/` (same seed, same files) |
| `python run.py ingest` | Loads new or changed files into bronze. Run it twice: the second run loads nothing. |
| `python run.py transform` | `dbt build`: silver and gold models and all data tests. On real data set `RI_SIMULATION=0` to skip the two tests that compare with the generator's defect list |
| `python -m pytest -q` | Robustness tests: breaks the input on purpose in a temporary copy (about 35 s) |
| `python run.py scrape` | Optional: a real snapshot of books.toscrape.com into `data/raw/competitor/` |
| `python run.py report` | Prints the main results: `kpis`, `invoices`, `prices`, `quality` (or `--sql "select …"` for your own query) |
| `python run.py ui` | Opens every table in DuckDB's web UI at http://localhost:4213 (press Enter in the terminal to close it) |
| `python run.py docs` | Regenerates [docs/data_dictionary.md](docs/data_dictionary.md) from the dbt YAML descriptions and the warehouse (types, keys, row counts, tests) |
| `python run.py docs --pdf` | Same, plus a styled HTML page and an A4 landscape PDF (printed with Edge or Chrome, no extra package) |
| `python run.py dashboard` | Rebuilds the dashboard [docs/index.html](docs/index.html) from the gold layer (also done by `all`) |
| `python run.py powerbi` | Exports gold to Parquet and generates the Power BI project `powerbi/RetailIntelligence.pbip` (open it, then Home > Refresh) |
| `python run.py screenshots` | 1600×1200 screenshots of every tab, light and dark, into `docs/img/` (Edge or Chrome) |

The warehouse is a single file, `data/warehouse/retail.duckdb`. You can open it with the DuckDB
CLI, DBeaver, Python or Power BI (ODBC).

## Project layout

```
generators/          fictional sources: world.py (ground truth), sources.py, invoices.py (PDFs)
pipeline/            bronze.py (ingestion), pdf_invoices.py, scraper.py, config.py
dbt/models/silver/   staging, matching, quarantine, SCD2 histories
dbt/models/gold/     dim_*, fct_*, mart_*
dbt/tests/           reconciliation, SCD2 continuity, invoice arithmetic, injected-defects check
tests/               robustness tests (pytest): damaged input in an isolated copy
run.py               one entry point for every step
```

## Roadmap

- [x] Interactive dashboard (static, self-contained) — publish with GitHub Pages from `/docs` once the repo is public
- [ ] Orchestration with Dagster (asset graph, schedules)
- [ ] Databricks Free Edition variant (PySpark + Delta) of the silver/gold layers
