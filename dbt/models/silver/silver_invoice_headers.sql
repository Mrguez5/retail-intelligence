-- Supplier invoices: typed amounts, recomputed totals and duplicate detection
with h as (
    select
        s.supplier_code,
        trim(b.invoice_number)                                    as invoice_number,
        case b.layout
            when 'northwind_en' then cast(try_strptime(b.invoice_date, '%d %b %Y') as date)
            when 'paperleaf_es' then cast(try_strptime(b.invoice_date, '%d/%m/%Y') as date)
        end                                                       as invoice_date,
        {{ parse_eur('b.subtotal') }}                             as subtotal_printed,
        {{ parse_eur('b.vat') }}                                  as vat_printed,
        {{ parse_eur('b.total') }}                                as total_printed,
        s.vat_rate,
        cast(b.line_count as integer)                             as line_count,
        b._source_file                                            as source_file,
        b._ingested_at                                            as ingested_at
    from {{ source('bronze', 'invoice_headers') }} b
    left join {{ ref('suppliers') }} s on s.invoice_layout = b.layout
),
lines as (
    select source_file, sum(line_amount_printed) as lines_total
    from {{ ref('silver_invoice_lines') }}
    group by 1
)
select
    h.*,
    l.lines_total,
    round(l.lines_total * h.vat_rate, 2)                          as vat_expected,
    round(l.lines_total + round(l.lines_total * h.vat_rate, 2), 2) as total_expected,
    -- the copy received first is the original; later copies are duplicates
    row_number() over (partition by h.supplier_code, h.invoice_number
                       order by h.ingested_at, h.source_file) > 1 as is_duplicate
from h
left join lines l using (source_file)
