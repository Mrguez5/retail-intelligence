-- EUR/GBP for every calendar day the pipeline needs.
-- Reference rates come on business days only: the last valid rate is carried forward over
-- weekends and holidays, but never for more than max_fx_age_days (then the rate is empty:
-- a feed that stopped must show up, not be hidden). Rates outside 0.5-1.5 are rejected.
with raw as (
    select try_cast(date as date) as rate_date, try_cast(eur_gbp as double) as eur_gbp
    from {{ source('bronze', 'fx_rates') }}
),
valid as (
    select rate_date, eur_gbp from raw where eur_gbp between 0.5 and 1.5
),
needed as (
    select
        (select min(rate_date) from raw) as first_day,
        greatest((select max(rate_date) from raw),
                 (select max(cast(scraped_at as date)) from {{ ref('silver_competitor_prices') }})) as last_day
),
calendar as (
    select cast(d as date) as rate_date
    from needed, {{ day_series("needed.first_day", "needed.last_day") }}
),
filled as (
    select
        c.rate_date,
        v.eur_gbp                                                                   as rate_that_day,
        {{ last_value_ignore_nulls("v.eur_gbp") }} over w                                   as last_rate,
        {{ last_value_ignore_nulls("case when v.eur_gbp is not null then c.rate_date end") }} over w as last_rate_date,
        exists (select 1 from raw r where r.rate_date = c.rate_date
                                     and (r.eur_gbp is null or r.eur_gbp not between 0.5 and 1.5)) as is_rejected_rate
    from calendar c
    left join valid v using (rate_date)
    window w as (order by c.rate_date rows between unbounded preceding and current row)
)
select
    rate_date,
    case when {{ days_between("last_rate_date", "rate_date") }} <= {{ var("max_fx_age_days") }} then last_rate end as eur_gbp,
    rate_that_day is null                                  as is_carried_forward,
    {{ days_between("last_rate_date", "rate_date") }}            as days_since_rate,
    is_rejected_rate
from filled
