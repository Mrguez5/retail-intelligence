-- Competitor price for every linked product and day: the latest valid observation
-- at the end of that day (ASOF join), converted to EUR with that day's rate.
with grid as (
    select d.date_day, d.date_day + interval 1 day as day_end, p.sku, p.competitor_url
    from {{ ref('dim_date') }} d
    cross join {{ ref('dim_product') }} p
    where p.has_competitor_link
      and d.date_day <= (select max(cast(scraped_at as date)) from {{ ref('silver_competitor_prices') }})
),
obs as (
    select url, scraped_at, price_gbp, in_stock
    from {{ ref('silver_competitor_prices') }}
    where dq_issue is null
)
select
    g.date_day,
    g.sku,
    o.price_gbp,
    fx.eur_gbp,
    round(o.price_gbp / fx.eur_gbp, 2)                         as price_eur,
    o.in_stock,
    o.scraped_at                                               as observed_at,
    date_diff('day', cast(o.scraped_at as date), g.date_day)   as days_since_observed
from grid g
asof left join obs o
    on o.url = g.competitor_url and g.day_end > o.scraped_at
left join {{ ref('silver_fx_daily') }} fx on fx.rate_date = g.date_day
