-- Web shop lines typed and put in the common sales shape.
-- dedup_key = order + product (+ occurrence inside the file), so the same order line
-- delivered again in another file is recognised as a copy.
select
    _source_file || ':' || _row_number                     as sale_line_id,
    'web'                                                  as channel,
    trim(order_id)                                         as order_id,
    {{ try_parse_ts("order_ts", "%Y-%m-%dT%H:%M:%S") }}            as sold_at,
    nullif(trim(sku), '')                                  as sku_raw,
    cast(null as {{ dbt.type_string() }})                                  as title_raw,
    try_cast(quantity as integer)                          as quantity,
    {{ parse_number('unit_price_eur') }}                   as unit_price_eur,
    coalesce(try_cast(discount_pct as decimal(5, 2)), 0)   as discount_pct,
    cast(0 as decimal(12, 2))                              as commission_eur,
    nullif(trim(customer_country), '')                     as country,
    trim(order_id) || '|' || coalesce(trim(sku), '') || '#'
        || row_number() over (partition by _source_file, trim(order_id), trim(sku)
                              order by cast(_row_number as integer))      as dedup_key,
    _source_file                                           as source_file
from {{ source('bronze', 'sales_web') }}
