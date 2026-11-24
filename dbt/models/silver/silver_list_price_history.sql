-- Inkwell's own list prices as a type-2 history, from the ERP change log:
-- one row per price period, so any sale can be compared with the price valid that day
with changes as (
    select
        trim(sku)                                   as sku,
        {{ parse_number('list_price_eur') }}        as list_price_eur,
        cast(valid_from as date)                    as valid_from
    from {{ source('bronze', 'erp_price_changes') }}
)
select
    sku,
    list_price_eur,
    valid_from,
    coalesce(lead(valid_from) over (partition by sku order by valid_from) - 1, date '9999-12-31') as valid_to,
    lead(valid_from) over (partition by sku order by valid_from) is null                         as is_current
from changes
