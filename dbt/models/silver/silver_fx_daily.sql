-- Reference rates come on business days only: build a full calendar and carry the
-- last known rate forward over weekends and holidays (flagged, never hidden)
with rates as (
    select cast(date as date) as rate_date, cast(eur_gbp as double) as eur_gbp
    from {{ source('bronze', 'fx_rates') }}
),
calendar as (
    select cast(d as date) as rate_date
    from range((select min(rate_date) from rates), (select max(rate_date) from rates) + interval 3 day, interval 1 day) t(d)
)
select
    c.rate_date,
    last_value(r.eur_gbp ignore nulls) over (order by c.rate_date rows between unbounded preceding and current row) as eur_gbp,
    r.eur_gbp is null as is_carried_forward
from calendar c
left join rates r using (rate_date)
