{{ config(enabled=var('simulation')) }}  -- only meaningful on generated data
-- The generator records every defect it injects; the pipeline must find exactly those
with injected as (
    select * from {{ read_json_file(var('raw_dir') ~ '/_injected_defects.json') }}
),
found as (
    select
        (select count(*) from {{ ref('silver_sales_quarantine') }} where dq_issue = 'duplicate_line')   as duplicates,
        (select count(*) from {{ ref('silver_sales_quarantine') }} where dq_issue = 'price_outlier')    as store_price_typos,
        (select count(*) from {{ ref('silver_sales_quarantine') }} where dq_issue = 'missing_quantity') as missing_qty,
        (select count(*) from {{ ref('silver_sales_quarantine') }} where dq_issue = 'unknown_product')  as unknown_products,
        (select count(*) from {{ ref('silver_competitor_prices') }} where dq_issue is not null)          as competitor_glitches,
        (select count(*) from {{ ref('silver_competitor_runs') }} where is_partial)                     as partial_runs,
        (select count(*) from {{ ref('mart_invoice_audit') }} where is_duplicate)                       as duplicate_invoices,
        (select count(*) from {{ ref('mart_invoice_audit') }} where not arithmetic_ok)                  as total_errors,
        (select count(*) from {{ ref('silver_invoice_lines') }}
          where unit_price_diff > 0.005
            and invoice_number <> (select invoices.early_increase_invoice from injected))               as nwd_overbilled_lines
),
checks as (
    select 'marketplace overlap duplicates' as check_name, i.marketplace.overlap_rows as expected, f.duplicates as found from injected i, found f
    union all select 'store price typos',        i.store.price_typos,          f.store_price_typos   from injected i, found f
    union all select 'store missing quantity',   i.store.missing_qty,          f.missing_qty         from injected i, found f
    union all select 'gift cards (not books)',   i.store.gift_cards,           f.unknown_products    from injected i, found f
    union all select 'competitor glitch rows',   i.competitor.glitch_rows,     f.competitor_glitches from injected i, found f
    union all select 'competitor partial runs',  i.competitor.partial_runs,    f.partial_runs        from injected i, found f
    union all select 'duplicate invoices',       1,                            f.duplicate_invoices  from injected i, found f
    union all select 'invoice total errors',     {{ array_length('i.invoices.total_errors') }}, f.total_errors        from injected i, found f
    union all select 'NWD overbilled lines',     i.invoices.overbilled_lines,  f.nwd_overbilled_lines from injected i, found f
)
select * from checks where expected is distinct from found   -- null-safe: a missing value fails too
