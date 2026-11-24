-- One row per check: what was looked at, what failed and what the pipeline did about it
with file_state as (
    -- latest status of every file the loader has seen
    select source as table_name, source_file, status
    from {{ source('bronze', '_ingestion_log') }}
    qualify row_number() over (partition by source_file order by ingested_at desc) = 1
),
files as (
    select 'ingestion' as source, 'file_failed_to_load' as rule, count(*) filter (where status = 'FAILED') as rows_failed
    from file_state
    union all
    select 'ingestion', 'file_resent_with_same_content', count(*) filter (where status = 'DUPLICATE_FILE') from file_state
    union all
    select 'ingestion', 'file_removed_from_source', count(*) filter (where status = 'REMOVED') from file_state
    union all
    select 'ingestion', 'new_column_in_source', count(*) from {{ source('bronze', '_schema_changes') }}
),
sales_rules as (
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
costs as (
    select 'sales_' || channel as source, 'sale_without_contract_cost' as rule, count(*) as rows_failed
    from {{ ref('fct_sales') }}
    where unit_cost_eur is null
    group by 1
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
    -- runs are expected every Monday and Thursday from the start of the calendar to the last run
    select 'competitor_runs', 'missing_run', count(*)
    from range((select min(date_day) from {{ ref('dim_date') }}),
               (select max(run_date) from {{ ref('silver_competitor_runs') }}) + interval 1 day,
               interval 1 day) t(d)
    where isodow(d) in (1, 4)
      and cast(d as date) not in (select run_date from {{ ref('silver_competitor_runs') }})
),
stale_prices as (
    select 'competitor_prices_daily' as source, 'price_too_old_not_used' as rule,
           count(*) filter (where is_stale) as rows_failed
    from {{ ref('fct_competitor_price_daily') }}
),
fx as (
    select 'fx_rates' as source, 'rate_carried_forward' as rule, count(*) filter (where is_carried_forward) as rows_failed
    from {{ ref('silver_fx_daily') }}
    union all
    select 'fx_rates', 'rate_rejected_out_of_range', count(*) filter (where is_rejected_rate) from {{ ref('silver_fx_daily') }}
    union all
    select 'fx_rates', 'rate_too_old_not_used', count(*) filter (where eur_gbp is null) from {{ ref('silver_fx_daily') }}
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
    select 'supplier_invoices', 'total_does_not_add_up', count(*) filter (where not arithmetic_ok and not is_duplicate)
    from {{ ref('mart_invoice_audit') }}
    union all
    select 'supplier_invoices', 'line_billed_above_contract', coalesce(sum(overbilled_lines) filter (where not is_duplicate), 0)
    from {{ ref('mart_invoice_audit') }}
),
all_checks as (
    select * from files
    union all select * from sales_rules
    union all select * from matches
    union all select * from reference_prices
    union all select * from costs
    union all select * from competitor
    union all select * from runs
    union all select * from stale_prices
    union all select * from fx
    union all select * from links
    union all select * from invoices
),
checked as (
    select 'ingestion' as source, count(*) as rows_checked from file_state
    union all select 'sales_' || channel, count(*) from {{ ref('int_sales_checked') }} group by 1
    union all select 'competitor_snapshots', count(*) from {{ ref('silver_competitor_prices') }}
    union all select 'competitor_runs', count(*) from {{ ref('silver_competitor_runs') }}
    union all select 'competitor_prices_daily', count(*) from {{ ref('fct_competitor_price_daily') }}
    union all select 'fx_rates', count(*) from {{ ref('silver_fx_daily') }}
    union all select 'erp_products', count(*) from {{ ref('silver_products') }}
    union all select 'supplier_invoices', count(*) from {{ ref('mart_invoice_audit') }}
)
select
    a.source,
    a.rule,
    c.rows_checked,
    a.rows_failed,
    round(a.rows_failed / nullif(c.rows_checked, 0), 4) as failed_share,
    case
        when a.rule = 'file_failed_to_load'                   then 'logged in bronze._ingestion_log; fix the file and re-run'
        when a.rule = 'file_resent_with_same_content'         then 'not loaded again (identical to a file already loaded)'
        when a.rule = 'file_removed_from_source'              then 'its rows were taken out of bronze'
        when a.rule = 'new_column_in_source'                  then 'column added in bronze; check bronze._schema_changes (renamed column?)'
        when a.rule = 'duplicate_line'                        then 'dropped (the valid copy from the latest export kept)'
        when a.rule like 'matched_by_%'                       then 'fixed automatically'
        when a.rule = 'price_check_used_weekly_median'        then 'no ERP price history: compared with that week''s median price'
        when a.rule = 'price_check_used_current_list_price'   then 'no history and too few sales: compared with today''s list price'
        when a.rule = 'sale_without_contract_cost'            then 'kept, but cost and margin are empty: add the contract price'
        when a.rule = 'competitor_link_title_match'           then 'fixed automatically'
        when a.rule like 'competitor_link_%'                  then 'no competitor price for this product'
        when a.rule = 'rate_carried_forward'                  then 'filled with last business-day rate'
        when a.rule = 'rate_rejected_out_of_range'            then 'rate ignored (outside 0.5-1.5)'
        when a.rule in ('rate_too_old_not_used', 'price_too_old_not_used') then 'left empty rather than use an old value'
        when a.rule in ('partial_run', 'missing_run')         then 'previous observation used'
        when a.source = 'supplier_invoices'                   then 'listed in invoice audit'
        when a.source = 'competitor_snapshots'                then 'observation ignored'
        else 'quarantined'
    end as action
from all_checks a
join checked c using (source)
where a.rows_failed > 0
order by a.source, a.rows_failed desc
