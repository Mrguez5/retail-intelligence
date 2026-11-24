select
    cast(d as date)                         as date_day,
    cast(date_trunc('week', d) as date)     as week_start,
    cast(date_trunc('month', d) as date)    as month_start,
    year(d)                                 as year,
    month(d)                                as month,
    strftime(d, '%Y-%m')                    as year_month,
    isodow(d)                               as iso_weekday,
    strftime(d, '%a')                       as weekday_name,
    isodow(d) in (6, 7)                     as is_weekend
from range(date '2026-01-01', date '2026-11-30', interval 1 day) t(d)
