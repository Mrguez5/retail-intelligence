{{ config(enabled=var('simulation')) }}  -- only meaningful on generated data
-- The clearance title sold at full price before its markdown. Those sales are correct and
-- must not be flagged as price outliers just because today's price is much lower.
-- (The only outliers allowed are real typos: a price typed x10.)
with clearance as (
    select i.clearance.sku as sku
    from {{ read_json_file(var('raw_dir') ~ '/_injected_defects.json') }} i
)
select c.sale_line_id, c.sale_date, c.unit_price_eur, c.reference_price_eur, c.reference_price_source
from {{ ref('int_sales_checked') }} c
join clearance using (sku)
where c.dq_issue = 'price_outlier'
  and not (c.unit_price_eur between 9.5 * c.reference_price_eur and 10.5 * c.reference_price_eur)
