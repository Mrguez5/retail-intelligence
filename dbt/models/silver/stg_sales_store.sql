-- Shop spreadsheet typed by hand: title only (no SKU), dates as Excel dates or text.
-- The shop has no order number, so a line is identified by its content: date, title,
-- units and price, numbered inside the file (two identical sales the same day stay two).
-- The same line found in another file is a copy (e.g. the month re-sent under a new name).
with typed as (
    select
        _source_file || ':' || _row_number                     as sale_line_id,
        'store'                                                as channel,
        cast(null as {{ dbt.type_string() }})                                  as order_id,
        coalesce({{ try_parse_ts("fecha", "%Y-%m-%d %H:%M:%S") }},
                 {{ try_parse_ts("fecha", "%d/%m/%Y") }})              as sold_at,
        cast(null as {{ dbt.type_string() }})                                  as sku_raw,
        nullif(trim(libro), '')                                as title_raw,
        try_cast(try_cast(cant as double) as integer)          as quantity,
        {{ parse_number('pvp') }}                              as unit_price_eur,
        cast(0 as decimal(5, 2))                               as discount_pct,
        cast(0 as decimal(12, 2))                              as commission_eur,
        'ES'                                                   as country,
        _source_file                                           as source_file,
        cast(_row_number as integer)                           as row_no
    from {{ source('bronze', 'sales_store') }}
)
select
    {{ star_except(['row_no']) }},
    concat_ws('|', cast(sold_at as date), {{ title_key('title_raw') }}, quantity, unit_price_eur) || '#'
        || row_number() over (partition by source_file, cast(sold_at as date), {{ title_key('title_raw') }},
                                           quantity, unit_price_eur
                              order by row_no)                 as dedup_key
from typed
