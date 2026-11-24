-- Shop spreadsheet typed by hand: title only (no SKU), dates as Excel dates or text
select
    _source_file || ':' || _row_number                     as sale_line_id,
    'store'                                                as channel,
    cast(null as varchar)                                  as order_id,
    coalesce(try_strptime(fecha, '%Y-%m-%d %H:%M:%S'),
             try_strptime(fecha, '%d/%m/%Y'))              as sold_at,
    cast(null as varchar)                                  as sku_raw,
    nullif(trim(libro), '')                                as title_raw,
    try_cast(try_cast(cant as double) as integer)          as quantity,
    {{ parse_number('pvp') }}                              as unit_price_eur,
    cast(0 as decimal(5, 2))                               as discount_pct,
    cast(0 as decimal(12, 2))                              as commission_eur,
    'ES'                                                   as country,
    _source_file || ':' || _row_number                     as dedup_key,
    _source_file                                           as source_file
from {{ source('bronze', 'sales_store') }}
