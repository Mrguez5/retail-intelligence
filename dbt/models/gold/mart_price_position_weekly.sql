-- Are we more expensive than the competitor, and does it cost us volume?
with ours as (
    select
        d.week_start, f.sku,
        sum(f.quantity) filter (where not f.is_return)                          as units,
        sum(f.net_revenue_eur)                                                  as net_revenue_eur,
        sum(f.unit_price_eur * f.quantity) filter (where not f.is_return)
            / nullif(sum(f.quantity) filter (where not f.is_return), 0)         as our_avg_price_eur
    from {{ ref('fct_sales') }} f
    join {{ ref('dim_date') }} d on d.date_day = f.sale_date
    group by 1, 2
),
theirs as (
    select
        d.week_start, c.sku,
        avg(c.price_eur)                                as competitor_avg_price_eur,
        avg(case when c.in_stock then 1 else 0 end)     as competitor_in_stock_share
    from {{ ref('fct_competitor_price_daily') }} c
    join {{ ref('dim_date') }} d using (date_day)
    group by 1, 2
),
joined as (
    select
        t.week_start, t.sku, p.title, p.category,
        coalesce(o.units, 0)                            as units,
        coalesce(o.net_revenue_eur, 0)                  as net_revenue_eur,
        o.our_avg_price_eur,
        t.competitor_avg_price_eur,
        t.competitor_in_stock_share,
        o.our_avg_price_eur / t.competitor_avg_price_eur - 1 as gap
    from theirs t
    join {{ ref('dim_product') }} p using (sku)
    left join ours o using (week_start, sku)
    where t.competitor_avg_price_eur is not null
)
select
    week_start, sku, title, category, units, net_revenue_eur,
    round(our_avg_price_eur, 2)          as our_avg_price_eur,
    round(competitor_avg_price_eur, 2)   as competitor_avg_price_eur,
    competitor_in_stock_share,
    round(gap, 4)                        as price_gap_pct,
    case
        when gap is null   then 'no sales'
        when gap < -0.03   then 'cheaper'
        when gap <= 0.03   then 'aligned'
        when gap <= 0.10   then 'pricier'
        else 'much pricier'
    end                                  as price_band
from joined
