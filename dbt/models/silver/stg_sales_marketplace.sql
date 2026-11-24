-- Spanish export: dd/mm/yyyy, decimal comma, seller reference sometimes empty.
-- Monthly files overlap by 3 days, so the same order line arrives twice (dedup_key).
select
    _source_file || ':' || _row_number                     as sale_line_id,
    'marketplace'                                          as channel,
    trim(no_pedido)                                        as order_id,
    try_strptime(fecha, '%d/%m/%Y %H:%M')                  as sold_at,
    nullif(trim(ref_vendedor), '')                         as sku_raw,
    nullif(trim(titulo), '')                               as title_raw,
    try_cast(unidades as integer)                          as quantity,
    {{ parse_number('precio_unitario') }}                  as unit_price_eur,
    cast(0 as decimal(5, 2))                               as discount_pct,
    {{ parse_number('comision') }}                         as commission_eur,
    'ES'                                                   as country,
    trim(no_pedido)                                        as dedup_key,
    _source_file                                           as source_file
from {{ source('bronze', 'sales_marketplace') }}
