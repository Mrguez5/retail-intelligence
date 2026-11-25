-- Calendar covering every month that has sales or competitor data (computed from the data,
-- so new months, late returns or a new scraper run extend it automatically)
with bounds as (
    select
        date_trunc('month', least(
            (select min(sale_date) from {{ ref('silver_sales') }}),
            (select min(run_date) from {{ ref('silver_competitor_runs') }})))                   as first_day,
        last_day(greatest(
            (select max(sale_date) from {{ ref('silver_sales') }}),
            (select max(run_date) from {{ ref('silver_competitor_runs') }})))                  as last_day
)
select
    cast(d as date)                         as date_day,
    cast(date_trunc('week', d) as date)     as week_start,
    cast(date_trunc('month', d) as date)    as month_start,
    year(d)                                 as year,
    month(d)                                as month,
    {{ format_date("d", "%Y-%m") }}                    as year_month,
    {{ iso_dow("d") }}                               as iso_weekday,
    {{ format_date("d", "%a") }}                       as weekday_name,
    {{ iso_dow("d") }} in (6, 7)                     as is_weekend
from bounds, {{ day_series("bounds.first_day", "bounds.last_day") }}
