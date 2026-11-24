-- Every identified sale must be compared with some price; otherwise a typo would slip through
select sale_line_id, sku
from {{ ref('int_sales_checked') }}
where sku is not null and reference_price_eur is null
