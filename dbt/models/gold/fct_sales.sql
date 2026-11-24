-- One row per sales line. Cost = supplier contract price valid on the price date (standard cost):
-- the sale date, or for a web return the date of the original sale, so a return reverses
-- exactly the cost that was booked.
select
    s.sale_line_id,
    s.sale_date,
    s.sold_at,
    s.channel,
    s.order_id,
    s.sku,
    s.quantity,
    s.unit_price_eur,
    s.discount_pct,
    round(s.quantity * s.unit_price_eur, 2)                                  as gross_revenue_eur,
    round(s.quantity * s.unit_price_eur * (1 - s.discount_pct / 100), 2)     as net_revenue_eur,
    s.commission_eur,
    c.unit_cost_eur,
    round(s.quantity * c.unit_cost_eur, 2)                                   as cogs_eur,
    round(s.quantity * s.unit_price_eur * (1 - s.discount_pct / 100)
          - s.commission_eur - s.quantity * c.unit_cost_eur, 2)              as margin_eur,
    s.quantity < 0                                                           as is_return,
    s.price_date,
    s.original_sale_line_id,
    s.match_method
from {{ ref('silver_sales') }} s
join {{ ref('silver_products') }} p using (sku)
left join {{ ref('silver_contract_prices') }} c
    on c.sku = s.sku and c.supplier_code = p.supplier_code
   and s.price_date between c.valid_from and c.valid_to
