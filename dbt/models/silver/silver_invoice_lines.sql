-- Invoice lines, typed, with the contract price that applied on the invoice date
with l as (
    select
        b._source_file                                 as source_file,
        trim(b.invoice_number)                         as invoice_number,
        cast(b.line_no as integer)                     as line_no,
        trim(b.sku)                                    as sku,
        trim(b.description)                            as description,
        cast(b.qty as integer)                         as quantity,
        {{ parse_eur('b.unit_price') }}                as unit_price_billed,
        {{ parse_eur('b.amount') }}                    as line_amount_printed,
        h.layout,
        case h.layout
            when 'northwind_en' then cast({{ try_parse_ts("h.invoice_date", "%d %b %Y") }} as date)
            when 'paperleaf_es' then cast({{ try_parse_ts("h.invoice_date", "%d/%m/%Y") }} as date)
        end                                            as invoice_date
    from {{ source('bronze', 'invoice_lines') }} b
    join {{ source('bronze', 'invoice_headers') }} h using (_source_file)
)
select
    {{ star_except(['layout'], prefix='l.') }},
    s.supplier_code,
    c.unit_cost_eur                                         as unit_price_contract,
    round(l.unit_price_billed - c.unit_cost_eur, 2)         as unit_price_diff,
    round(l.quantity * l.unit_price_billed, 2) - l.line_amount_printed as line_arithmetic_diff
from l
left join {{ ref('suppliers') }} s on s.invoice_layout = l.layout
left join {{ ref('silver_contract_prices') }} c
    on c.sku = l.sku
   and c.supplier_code = s.supplier_code
   and l.invoice_date between c.valid_from and c.valid_to
