-- Inkwell's own catalogue (ERP export), typed and with a title key for matching
select
    trim(sku)                                   as sku,
    trim(title)                                 as title,
    {{ title_key('title') }}                    as title_key,
    trim(category)                              as category,
    trim(supplier_code)                         as supplier_code,
    {{ parse_number('list_price_eur') }}        as list_price_eur,
    nullif(trim(competitor_url), '')            as erp_competitor_url,
    active = 'Y'                                as is_active
from {{ source('bronze', 'erp_products') }}
