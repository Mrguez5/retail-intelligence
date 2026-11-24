select
    _source_file || ':' || _row_number                     as sale_line_id,
    'web'                                                  as channel,
    trim(order_id)                                         as order_id,
    try_strptime(order_ts, '%Y-%m-%dT%H:%M:%S')            as sold_at,
    nullif(trim(sku), '')                                  as sku_raw,
    cast(null as varchar)                                  as title_raw,
    try_cast(quantity as integer)                          as quantity,
    {{ parse_number('unit_price_eur') }}                   as unit_price_eur,
    coalesce(try_cast(discount_pct as decimal(5, 2)), 0)   as discount_pct,
    cast(0 as decimal(12, 2))                              as commission_eur,
    nullif(trim(customer_country), '')                     as country,
    _source_file || ':' || _row_number                     as dedup_key,   -- monthly files do not overlap
    _source_file                                           as source_file
from {{ source('bronze', 'sales_web') }}
