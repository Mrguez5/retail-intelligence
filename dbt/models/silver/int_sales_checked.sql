-- One data-quality verdict per sales line.
--
-- 1) Every line is validated first (date, product, quantity, price).
-- 2) Only then are copies resolved: among the copies of the same line (dedup_key) the
--    VALID one is kept, and among valid copies the newest export. A broken re-export can
--    therefore never push a good line out.
--
-- Price check: each line is compared with the price the book had on its price date
-- (the sale date; for a web return, the date of the original sale):
--   A) the ERP list-price history, when the title has one;
--   B) otherwise the median price of that title that week across all channels
--      (needs at least 3 sales, so a single wrong price cannot set its own reference);
--   C) last resort: today's list price from the ERP export.
with matched as (
    select * from {{ ref('int_sales_matched') }}
),
originals as (
    -- web returns carry the original order number + "-R": find the original line
    select
        r.sale_line_id,
        min_by(o.sale_line_id, o.sold_at) as original_sale_line_id,
        min(o.sale_date)                  as original_sale_date
    from matched r
    join matched o
      on r.channel = 'web' and r.quantity < 0 and r.order_id like '%-R'
     and o.order_id = left(r.order_id, length(r.order_id) - 2)
     and o.sku = r.sku and o.quantity > 0
    group by r.sale_line_id
),
dated as (
    select
        m.*,
        o.original_sale_line_id,
        coalesce(o.original_sale_date, m.sale_date) as price_date
    from matched m
    left join originals o using (sale_line_id)
),
weekly_median as (
    select
        sku,
        date_trunc('week', sold_at)    as week_start,
        median(unit_price_eur)         as median_price_eur,
        count(*)                       as observations
    from matched
    where sku is not null and dedup_rank = 1 and unit_price_eur > 0
    group by 1, 2
),
priced as (
    select
        d.*,
        p.list_price_eur,
        h.list_price_eur                                                     as history_price_eur,
        case when w.observations >= 3 then w.median_price_eur end            as median_price_eur
    from dated d
    left join {{ ref('silver_products') }} p on p.sku = d.sku
    left join {{ ref('silver_list_price_history') }} h
        on h.sku = d.sku and d.price_date between h.valid_from and h.valid_to
    left join weekly_median w
        on w.sku = d.sku and w.week_start = date_trunc('week', d.price_date)
),
validated as (
    select
        * exclude (history_price_eur, median_price_eur),
        coalesce(history_price_eur, median_price_eur, list_price_eur)        as reference_price_eur,
        case
            when history_price_eur is not null then 'erp_history'
            when median_price_eur  is not null then 'weekly_median'
            when list_price_eur    is not null then 'current_list_price'
        end                                                                  as reference_price_source,
        case
            when sold_at is null                                         then 'invalid_date'
            when sale_date < date '{{ var("data_start") }}'
              or sale_date > current_date                                then 'date_out_of_range'
            when match_ambiguous                                         then 'ambiguous_product'
            when sku is null                                             then 'unknown_product'
            when quantity is null                                        then 'missing_quantity'
            when quantity = 0                                            then 'zero_quantity'
            when abs(quantity) > {{ var("max_units_per_line") }}         then 'quantity_outlier'
            when unit_price_eur is null or unit_price_eur <= 0           then 'invalid_price'
            when unit_price_eur > 3 * coalesce(history_price_eur, median_price_eur, list_price_eur)
              or unit_price_eur < 0.3 * coalesce(history_price_eur, median_price_eur, list_price_eur)
                                                                         then 'price_outlier'
        end                                                                  as line_issue
    from priced
),
resolved as (
    select
        * exclude (dedup_rank),
        row_number() over (partition by channel, dedup_key
                           order by (line_issue is null) desc, source_file desc) as dedup_rank
    from validated
)
select
    * exclude (line_issue),
    case when dedup_rank > 1 then 'duplicate_line' else line_issue end as dq_issue
from resolved
