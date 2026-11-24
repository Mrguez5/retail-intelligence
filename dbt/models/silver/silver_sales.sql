-- Clean, de-duplicated sales lines from all channels (returns are negative quantities)
select
    sale_line_id, channel, order_id, sold_at, sale_date, sku, match_method, match_score, match_edits,
    quantity, unit_price_eur, discount_pct, commission_eur, country,
    price_date, original_sale_line_id, source_file
from {{ ref('int_sales_checked') }}
where dq_issue is null
