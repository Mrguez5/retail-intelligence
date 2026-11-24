with sales as (
    select
        d.month_start,
        sum(f.net_revenue_eur)                                    as net_revenue_eur,
        sum(f.margin_eur)                                         as margin_eur,
        sum(f.quantity) filter (where not f.is_return)            as units_sold,
        -sum(f.quantity) filter (where f.is_return)               as units_returned,
        count(distinct coalesce(f.order_id, f.sale_line_id))      as orders
    from {{ ref('fct_sales') }} f
    join {{ ref('dim_date') }} d on d.date_day = f.sale_date
    group by 1
),
price_position as (
    select
        date_trunc('month', week_start) as month_start,
        sum(price_gap_pct * units) / nullif(sum(units) filter (where price_gap_pct is not null), 0) as weighted_price_gap_pct
    from {{ ref('mart_price_position_weekly') }}
    group by 1
),
audit as (
    select
        date_trunc('month', invoice_date)                     as month_start,
        sum(total_printed) filter (where not is_duplicate)    as purchases_eur,
        sum(amount_at_risk_eur)                               as invoice_amount_at_risk_eur
    from {{ ref('mart_invoice_audit') }}
    group by 1
)
select
    s.month_start,
    strftime(s.month_start, '%Y-%m')                          as year_month,
    round(s.net_revenue_eur, 2)                               as net_revenue_eur,
    round(s.margin_eur, 2)                                    as margin_eur,
    round(s.margin_eur / nullif(s.net_revenue_eur, 0), 4)     as margin_pct,
    s.units_sold,
    s.units_returned,
    s.orders,
    round(p.weighted_price_gap_pct, 4)                        as weighted_price_gap_pct,
    round(a.purchases_eur, 2)                                 as purchases_eur,
    round(coalesce(a.invoice_amount_at_risk_eur, 0), 2)       as invoice_amount_at_risk_eur
from sales s
left join price_position p using (month_start)
left join audit a using (month_start)
where s.month_start <= date '2026-10-01'
order by 1
