-- Latest known competitor catalogue (to link Inkwell products by title when the ERP has no link)
with latest as (
    select url, title, title_key, category
    from {{ ref('silver_competitor_prices') }}
    qualify row_number() over (partition by url order by scraped_at desc) = 1
)
select
    *,
    count(*) over (partition by title_key) as titles_with_same_key
from latest
