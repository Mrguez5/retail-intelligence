select
    p.sku, p.title, p.category, p.supplier_code, s.supplier_name,
    p.list_price_eur, l.competitor_url, l.link_method,
    l.competitor_url is not null as has_competitor_link
from {{ ref('silver_products') }} p
left join {{ ref('silver_product_competitor_link') }} l using (sku)
left join {{ ref('suppliers') }} s using (supplier_code)
