-- A product can have only one contract price on any given day
select a.sku, a.valid_from, b.valid_from as overlapping_from
from {{ ref('silver_contract_prices') }} a
join {{ ref('silver_contract_prices') }} b
  on a.sku = b.sku and a.supplier_code = b.supplier_code and a.valid_from < b.valid_from
 and b.valid_from <= a.valid_to
