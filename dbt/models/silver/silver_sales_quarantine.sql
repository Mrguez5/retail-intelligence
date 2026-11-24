-- Lines kept out of the analysis, with the reason, so they can be fixed at the source
select
    sale_line_id, channel, order_id, sold_at, sku_raw, title_raw, sku as matched_sku,
    quantity, unit_price_eur, list_price_eur, dq_issue, source_file
from {{ ref('int_sales_checked') }}
where dq_issue is not null
