-- Every supplier invoice checked three ways: arithmetic, duplicates and contract prices
with line_checks as (
    select
        source_file,
        count(*)                                                     as lines,
        count(*) filter (where unit_price_diff > 0.005)              as overbilled_lines,
        coalesce(sum(unit_price_diff * quantity) filter (where unit_price_diff > 0.005), 0) as overbilled_eur,
        count(*) filter (where unit_price_contract is null)          as lines_without_contract,
        count(*) filter (where abs(line_arithmetic_diff) > 0.01)     as line_arithmetic_errors
    from {{ ref('silver_invoice_lines') }}
    group by 1
)
select
    h.supplier_code,
    h.invoice_number,
    h.invoice_date,
    h.source_file,
    h.subtotal_printed,
    h.vat_printed,
    h.total_printed,
    h.total_expected,
    round(h.total_printed - h.total_expected, 2)                     as total_diff_eur,
    abs(h.total_printed - h.total_expected) <= 0.01
        and abs(h.subtotal_printed - h.lines_total) <= 0.01          as arithmetic_ok,
    h.is_duplicate,
    l.lines,
    l.overbilled_lines,
    round(l.overbilled_eur * (1 + h.vat_rate), 2)                    as overbilled_eur_incl_vat,
    l.lines_without_contract,
    l.line_arithmetic_errors,
    -- money at stake if the invoice were paid as printed
    round(case
        when h.is_duplicate then h.total_printed
        else greatest(h.total_printed - h.total_expected, 0) + l.overbilled_eur * (1 + h.vat_rate)
    end, 2)                                                          as amount_at_risk_eur,
    case
        when h.is_duplicate                                   then 'BLOCK - duplicate'
        when abs(h.total_printed - h.total_expected) > 0.01   then 'REVIEW - total does not add up'
        when l.overbilled_lines > 0                           then 'CLAIM - billed above contract'
        when l.lines_without_contract > 0                     then 'REVIEW - no contract price'
        else 'OK'
    end                                                              as audit_status
from {{ ref('silver_invoice_headers') }} h
join line_checks l using (source_file)
