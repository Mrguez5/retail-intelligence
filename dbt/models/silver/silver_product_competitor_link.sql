-- Which competitor page is the same book? ERP link first, else an unambiguous exact title match
with by_title as (
    select p.sku, c.url, c.titles_with_same_key
    from {{ ref('silver_products') }} p
    join {{ ref('silver_competitor_catalog') }} c using (title_key)
)
select
    p.sku,
    coalesce(p.erp_competitor_url, case when t.titles_with_same_key = 1 then t.url end) as competitor_url,
    case
        when p.erp_competitor_url is not null then 'erp_link'
        when t.titles_with_same_key = 1       then 'title_match'
        when t.titles_with_same_key > 1       then 'ambiguous_title'
        else 'not_found'
    end as link_method
from {{ ref('silver_products') }} p
left join by_title t on t.sku = p.sku
qualify row_number() over (partition by p.sku order by t.url) = 1
