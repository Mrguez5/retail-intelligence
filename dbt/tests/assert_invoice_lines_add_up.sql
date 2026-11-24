-- Every printed line amount equals quantity x unit price (rounding tolerance 1 cent)
select * from {{ ref('silver_invoice_lines') }}
where abs(line_arithmetic_diff) > 0.01
