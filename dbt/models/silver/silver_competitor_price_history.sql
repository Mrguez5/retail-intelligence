-- Type-2 history of competitor prices: one row per period with an unchanged price
-- (gaps-and-islands over the valid observations)
with valid as (
    select url, scraped_at, price_gbp
    from {{ ref('silver_competitor_prices') }}
    where dq_issue is null
),
flagged as (
    select
        *,
        case when price_gbp is distinct from lag(price_gbp) over (partition by url order by scraped_at)
             then 1 else 0 end as is_change
    from valid
),
grouped as (
    select *, sum(is_change) over (partition by url order by scraped_at rows unbounded preceding) as period_no
    from flagged
)
select
    url,
    period_no,
    price_gbp,
    min(scraped_at)                                                            as valid_from,
    lead(min(scraped_at)) over (partition by url order by min(scraped_at))     as valid_to,
    count(*)                                                                   as observations,
    lead(min(scraped_at)) over (partition by url order by min(scraped_at)) is null as is_current
from grouped
group by url, period_no, price_gbp
