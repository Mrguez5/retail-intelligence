{# Reusable descriptions for columns that appear in many tables. Use: '{{ doc("col_sku") }}' #}

{% docs col_bronze_row_number %}
Line number of the row inside its source file (1 = first data row, header excluded). With `_source_file` it identifies the row uniquely.
{% enddocs %}

{% docs col_bronze_source_file %}
Path of the raw file the row came from, relative to the repository root (e.g. `data/raw/sales/web/web_orders_2026-03.csv`).
{% enddocs %}

{% docs col_bronze_file_hash %}
SHA-256 of the file content when it was loaded. A file is loaded again only if this changes.
{% enddocs %}

{% docs col_bronze_batch_id %}
Ingestion run that loaded the row (`yyyymmddhhmmss-xxxxxx`). Matches `bronze._ingestion_log.batch_id`.
{% enddocs %}

{% docs col_bronze_ingested_at %}
When the row was loaded into bronze (ISO timestamp, stored as text like every bronze value).
{% enddocs %}

{% docs col_sku %}
Inkwell's product code (`INK-00001` … `INK-00240`). One SKU = one book title.
{% enddocs %}

{% docs col_sale_line_id %}
Unique id of a sales line: `<source file>:<row number>`. Traces any figure back to one row of one raw file.
{% enddocs %}

{% docs col_channel %}
Sales channel: `web` (online shop), `marketplace` or `store` (Madrid shop).
{% enddocs %}

{% docs col_order_id %}
Order number given by the channel (`W…` web, `MKT-…` marketplace). Web returns end in `-R`. Empty for the shop, which records no order number.
{% enddocs %}

{% docs col_sold_at %}
Date and time of the sale. The shop records only the date (time 00:00).
{% enddocs %}

{% docs col_sale_date %}
Calendar date of the sale (`sold_at` without the time).
{% enddocs %}

{% docs col_sku_raw %}
SKU exactly as the source gave it. Always empty for the shop; empty for ~10 % of marketplace lines.
{% enddocs %}

{% docs col_title_raw %}
Book title exactly as the source gave it, typos included. Empty for the web, which sends only the SKU.
{% enddocs %}

{% docs col_quantity %}
Units sold on the line. Negative for returns.
{% enddocs %}

{% docs col_unit_price_eur %}
Price charged per unit in EUR, before any web discount code.
{% enddocs %}

{% docs col_discount_pct %}
Discount code applied on the web, in percent (0, 5 or 10). Always 0 for the other channels.
{% enddocs %}

{% docs col_commission_eur %}
Marketplace commission for the line in EUR (15 % of the line amount). 0 for web and shop.
{% enddocs %}

{% docs col_country %}
Customer country (ISO 3166 alpha-2). Marketplace and shop sales are recorded as `ES`.
{% enddocs %}

{% docs col_dedup_key %}
Business key used to detect the same line arriving twice (overlapping exports, a file re-sent under another name). Marketplace: order number. Web: order number + SKU. Shop (no order number): date + title + units + price. Web and shop keys end in `#n`, the occurrence inside the file, so two identical sales in one file stay two.
{% enddocs %}

{% docs col_source_file %}
Raw file the row came from, relative to the repository root.
{% enddocs %}

{% docs col_match_method %}
How the line was linked to a product: `sku` (SKU given and known), `title_exact` (normalised title identical) or `title_fuzzy` (closest title, at most 2 typing errors or Jaro-Winkler ≥ 0.95). Empty if no product was found.
{% enddocs %}

{% docs col_match_score %}
Jaro-Winkler similarity (0–1) between the typed title and the matched title. Only for `title_fuzzy`.
{% enddocs %}

{% docs col_match_edits %}
Damerau-Levenshtein distance (missing, extra or swapped characters) between the typed title and the matched title. Only for `title_fuzzy`.
{% enddocs %}

{% docs col_dedup_rank %}
1 = the copy that is kept; 2 or more = a repeated copy of the same line. In `int_sales_matched`: newest export first. In `int_sales_checked` (final): the valid copy first, then the newest export.
{% enddocs %}

{% docs col_list_price_eur_current %}
Inkwell's list price in EUR **today**, from the ERP product export.
{% enddocs %}

{% docs col_reference_price_eur %}
Price the line is checked against: the list price **valid on the sale date** (see `reference_price_source`).
{% enddocs %}

{% docs col_reference_price_source %}
Where the reference price came from: `erp_history` (ERP list-price history), `weekly_median` (no history: median price of that title that week across channels, ≥ 3 sales) or `current_list_price` (last resort).
{% enddocs %}

{% docs col_dq_issue %}
First data-quality rule the row fails, or empty if it passes all of them.
{% enddocs %}

{% docs col_title %}
Book title, as written in Inkwell's catalogue.
{% enddocs %}

{% docs col_title_key %}
Normalised title used for matching: lower case, no accents, punctuation turned into single spaces.
{% enddocs %}

{% docs col_category %}
Book category, as published by books.toscrape.com.
{% enddocs %}

{% docs col_supplier_code %}
Supplier code: `NWD` (Northwind Book Distribution, Ireland) or `PLW` (Paperleaf Distribuciones, Spain).
{% enddocs %}

{% docs col_competitor_url %}
Product page of the same book on the competitor's site (books.toscrape.com). Identifies a competitor product.
{% enddocs %}

{% docs col_valid_from %}
First day the value applies (inclusive).
{% enddocs %}

{% docs col_valid_to %}
Last day the value applies (inclusive). `9999-12-31` = still valid.
{% enddocs %}

{% docs col_is_current %}
True for the period that is valid today.
{% enddocs %}

{% docs col_invoice_number %}
Invoice number printed by the supplier (`NWD-2026-0001`, `F26/0001`). Not unique on its own: a supplier can send the same invoice twice.
{% enddocs %}

{% docs col_week_start %}
Monday of the ISO week.
{% enddocs %}

{% docs col_price_date %}
Date whose prices apply to the line: the sale date, or for a web return the date of the original sale (so the return is checked and costed with the price it was sold at).
{% enddocs %}
