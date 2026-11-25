-- Competitor price for every linked product and day: the latest valid observation
-- at the end of that day (ASOF join), converted to EUR with that day's rate.
-- An observation older than max_price_age_days is not used (the book may have left the
-- competitor's site): price_gbp / price_eur are then empty and is_stale is true.
with grid as (
    select d.date_day, d.date_day + interval 1 day as day_end, p.sku, p.competitor_url
    from {{ ref('dim_date') }} d
    cross join {{ ref('dim_product') }} p
    where p.has_competitor_link
      and d.date_day <=(select max(cast(scraped_at as date)) from {{ ref('silver_competitor_prices') }})
),
obs as (
    select url, scraped_at, price_gbp, in_stock
    from {{ ref('silver_competitor_prices') }}
    where dq_issue is null
),
joined as (
    select
        g.date_day,
        g.sku,
        o.price_gbp                                               as last_seen_price_gbp,
        o.in_stock,
        o.scraped_at                                              as observed_at,
        {{ days_between("cast(o.scraped_at as date)", "g.date_day") }}  as days_since_observed
    from grid g
    {% if target.type == 'duckdb' -%}
    asof left join obs o
        on o.url = g.competitor_url and g.day_end > o.scraped_at
    {%- else -%}
    -- no ASOF join in Spark SQL: keep the latest observation before the end of the day
    left join obs o
        on o.url = g.competitor_url and o.scraped_at < g.day_end
    qualify row_number() over (partition by g.sku, g.date_day order by o.scraped_at desc) = 1
    {%- endif %}
)
select
    j.date_day,
    j.sku,
    case when not is_stale then j.last_seen_price_gbp end                     as price_gbp,
    j.last_seen_price_gbp,
    fx.eur_gbp,
    case when not is_stale then round(j.last_seen_price_gbp / fx.eur_gbp, 2) end as price_eur,
    j.in_stock,
    j.observed_at,
    j.days_since_observed,
    is_stale
from (
    select *, coalesce(days_since_observed > {{ var("max_price_age_days") }}, true) as is_stale from joined
) j
left join {{ ref('silver_fx_daily') }} fx on fx.rate_date = j.date_day
