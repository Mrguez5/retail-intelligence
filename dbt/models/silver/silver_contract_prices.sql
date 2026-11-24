-- Supplier contract prices as a type-2 history: one row per price period
select
    trim(supplier)                                              as supplier_code,
    trim(sku)                                                   as sku,
    {{ parse_number('unit_cost_eur') }}                         as unit_cost_eur,
    cast(try_cast(valid_from as timestamp) as date)             as valid_from,
    coalesce(cast(try_cast(valid_to as timestamp) as date), date '9999-12-31') as valid_to,
    valid_to is null                                            as is_current
from {{ source('bronze', 'contract_prices') }}
