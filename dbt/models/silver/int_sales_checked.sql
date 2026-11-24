-- One data-quality verdict per sales line (first failing rule wins)
select
    m.*,
    p.list_price_eur,
    case
        when m.dedup_rank > 1                                     then 'duplicate_line'
        when m.sold_at is null                                    then 'invalid_date'
        when m.sku is null                                        then 'unknown_product'
        when m.quantity is null                                   then 'missing_quantity'
        when m.quantity = 0                                       then 'zero_quantity'
        when m.unit_price_eur is null or m.unit_price_eur <= 0    then 'invalid_price'
        when m.unit_price_eur > 3 * p.list_price_eur
          or m.unit_price_eur < 0.3 * p.list_price_eur            then 'price_outlier'
    end as dq_issue
from {{ ref('int_sales_matched') }} m
left join {{ ref('silver_products') }} p on p.sku = m.sku
