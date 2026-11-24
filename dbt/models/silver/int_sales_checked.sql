-- One data-quality verdict per sales line (first failing rule wins).
--
-- The price check compares each line with the price the book had ON THE SALE DATE:
--   A) the ERP list-price history (silver_list_price_history), when the title has one;
--   B) otherwise the median price of that title that week across all channels
--      (needs at least 3 sales, so a single wrong price cannot set its own reference);
--   C) last resort: today's list price from the ERP export.
-- The source used is kept in reference_price_source so every verdict can be audited.
with weekly_median as (
    select
        sku,
        date_trunc('week', sold_at)    as week_start,
        median(unit_price_eur)         as median_price_eur,
        count(*)                       as observations
    from {{ ref('int_sales_matched') }}
    where sku is not null and dedup_rank = 1 and unit_price_eur > 0
    group by 1, 2
),
priced as (
    select
        m.*,
        p.list_price_eur,
        h.list_price_eur                                                     as history_price_eur,
        case when w.observations >= 3 then w.median_price_eur end            as median_price_eur
    from {{ ref('int_sales_matched') }} m
    left join {{ ref('silver_products') }} p on p.sku = m.sku
    left join {{ ref('silver_list_price_history') }} h
        on h.sku = m.sku and m.sale_date between h.valid_from and h.valid_to
    left join weekly_median w
        on w.sku = m.sku and w.week_start = date_trunc('week', m.sold_at)
),
referenced as (
    select
        *,
        coalesce(history_price_eur, median_price_eur, list_price_eur)        as reference_price_eur,
        case
            when history_price_eur is not null then 'erp_history'
            when median_price_eur  is not null then 'weekly_median'
            when list_price_eur    is not null then 'current_list_price'
        end                                                                  as reference_price_source
    from priced
)
select
    * exclude (history_price_eur, median_price_eur),
    case
        when dedup_rank > 1                                       then 'duplicate_line'
        when sold_at is null                                      then 'invalid_date'
        when sku is null                                          then 'unknown_product'
        when quantity is null                                     then 'missing_quantity'
        when quantity = 0                                         then 'zero_quantity'
        when unit_price_eur is null or unit_price_eur <= 0        then 'invalid_price'
        when unit_price_eur > 3 * reference_price_eur
          or unit_price_eur < 0.3 * reference_price_eur           then 'price_outlier'
    end as dq_issue
from referenced
