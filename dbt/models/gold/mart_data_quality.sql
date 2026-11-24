-- One row per check: what was looked at, what failed and what the pipeline did about it
with sales_rules as (
    select 'sales_' || channel as source, dq_issue as rule, count(*) as rows_failed
    from {{ ref('silver_sales_quarantine') }}
    group by 1, 2
),
matches as (
    select 'sales_' || channel as source, 'matched_by_' || match_method as rule, count(*) as rows_failed
    from {{ ref('silver_sales') }}
    where match_method in ('title_exact', 'title_fuzzy')
    group by 1, 2
),
reference_prices as (
    select 'sales_' || channel as source, 'price_check_used_' || reference_price_source as rule, count(*) as rows_failed
    from {{ ref('int_sales_checked') }}
    where reference_price_source in ('weekly_median', 'current_list_price')
    group by 1, 2
),
competitor as (
    select 'competitor_snapshots' as source, dq_issue as rule, count(*) as rows_failed
    from {{ ref('silver_competitor_prices') }}
    where dq_issue is not null
    group by 2
),
runs as (
    select 'competitor_runs' as source, 'partial_run' as rule, count(*) filter (where is_partial) as rows_failed
    from {{ ref('silver_competitor_runs') }}
    union all
    select
        'competitor_runs', 'missing_run',
        (select count(*) from {{ ref('dim_date') }} where iso_weekday in (1, 4) and date_day <= date '2026-10-31')
        - (select count(distinct run_date) from {{ ref('silver_competitor_runs') }})
),
fx as (
    select 'fx_rates' as source, 'rate_carried_forward' as rule, count(*) filter (where is_carried_forward) as rows_failed
    from {{ ref('silver_fx_daily') }}
),
links as (
    select 'erp_products' as source, 'competitor_link_' || link_method as rule, count(*) as rows_failed
    from {{ ref('silver_product_competitor_link') }}
    where link_method <> 'erp_link'
    group by 2
),
invoices as (
    select 'supplier_invoices' as source, 'duplicate_invoice' as rule, count(*) filter (where is_duplicate) as rows_failed
    from {{ ref('mart_invoice_audit') }}
    union all
    select 'supplier_invoices', 'total_does_not_add_up', count(*) filter (where not arithmetic_ok)
    from {{ ref('mart_invoice_audit') }}
    union all
    select 'supplier_invoices', 'line_billed_above_contract', sum(overbilled_lines)
    from {{ ref('mart_invoice_audit') }}
),
all_checks as (
    select * from sales_rules
    union all select * from matches
    union all select * from reference_prices
    union all select * from competitor
    union all select * from runs
    union all select * from fx
    union all select * from links
    union all select * from invoices
),
checked as (
    select 'sales_' || channel as source, count(*) as rows_checked from {{ ref('int_sales_checked') }} group by 1
    union all select 'competitor_snapshots', count(*) from {{ ref('silver_competitor_prices') }}
    union all select 'competitor_runs', count(*) from {{ ref('silver_competitor_runs') }}
    union all select 'fx_rates', count(*) from {{ ref('silver_fx_daily') }}
    union all select 'erp_products', count(*) from {{ ref('silver_products') }}
    union all select 'supplier_invoices', count(*) from {{ ref('mart_invoice_audit') }}
)
select
    a.source,
    a.rule,
    c.rows_checked,
    a.rows_failed,
    round(a.rows_failed / c.rows_checked, 4) as failed_share,
    case
        when a.rule = 'duplicate_line'                        then 'dropped (copy from the latest export kept)'
        when a.rule like 'matched_by_%'                       then 'fixed automatically'
        when a.rule = 'competitor_link_title_match'           then 'fixed automatically'
        when a.rule like 'competitor_link_%'                  then 'no competitor price for this product'
        when a.rule = 'price_check_used_weekly_median'        then 'no ERP price history: compared with that week''s median price'
        when a.rule = 'price_check_used_current_list_price'   then 'no history and too few sales: compared with today''s list price'
        when a.rule = 'rate_carried_forward'                  then 'filled with last business-day rate'
        when a.rule in ('partial_run', 'missing_run')         then 'previous observation used'
        when a.source = 'supplier_invoices'                   then 'listed in invoice audit'
        when a.source = 'competitor_snapshots'                then 'observation ignored'
        else 'quarantined'
    end as action
from all_checks a
join checked c using (source)
where a.rows_failed > 0
order by a.source, a.rows_failed desc
