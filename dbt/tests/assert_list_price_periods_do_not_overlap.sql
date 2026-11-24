-- A title can have only one list price on any given day
select a.sku, a.valid_from, b.valid_from as overlapping_from
from {{ ref('silver_list_price_history') }} a
join {{ ref('silver_list_price_history') }} b
  on a.sku = b.sku and a.valid_from < b.valid_from and b.valid_from <= a.valid_to
