-- All channels in one shape, each line resolved to a product:
-- 1) SKU given and known  2) exact title key
-- 3) closest title: at most 2 typing errors (Damerau-Levenshtein: missing, extra or swapped
--    characters) or Jaro-Winkler >= 0.95; the score is kept so every guess can be audited.
--    If two catalogue titles are equally close, no product is chosen (match_ambiguous):
--    e.g. "Fruits Basket, Vol. 4" and "Vol. 6" differ by only two characters.
with unioned as (
    {{ union_all_by_name([ref('stg_sales_web'), ref('stg_sales_marketplace'), ref('stg_sales_store')]) }}
),
products as (
    select sku, title_key from {{ ref('silver_products') }}
),
unmatched_titles as (
    select distinct {{ title_key('title_raw') }} as title_key
    from unioned u
    where u.title_raw is not null
      and (u.sku_raw is null or u.sku_raw not in (select sku from products))
      and {{ title_key('title_raw') }} not in (select title_key from products)
),
candidates as (
    select
        t.title_key,
        p.sku,
        {{ title_similarity('t.title_key', 'p.title_key') }} as score,
        {{ title_edits('t.title_key', 'p.title_key') }}     as edits
    from unmatched_titles t
    cross join products p
),
ranked as (
    select
        *,
        row_number() over (partition by title_key order by edits, score desc, sku) as rn,
        count(*) over (partition by title_key, edits)                             as same_distance
    from candidates
),
fuzzy as (
    select
        title_key, sku, score, edits,
        same_distance > 1                                                         as is_ambiguous,
        same_distance = 1 and ((edits <= 2 and length(title_key) >= 8) or score >= 0.95) as accepted
    from ranked
    where rn = 1
)
select
    u.*,
    cast(u.sold_at as date)                                             as sale_date,
    coalesce(ps.sku, pt.sku, case when f.accepted then f.sku end)       as sku,
    case
        when ps.sku is not null then 'sku'
        when pt.sku is not null then 'title_exact'
        when f.accepted        then 'title_fuzzy'
    end                                                                 as match_method,
    f.score                                                             as match_score,
    f.edits                                                             as match_edits,
    coalesce(f.is_ambiguous and ps.sku is null and pt.sku is null, false) as match_ambiguous,
    -- newest copy first; the final choice (valid copy first) is made in int_sales_checked
    row_number() over (partition by u.channel, u.dedup_key order by u.source_file desc) as dedup_rank
from unioned u
left join products ps on ps.sku = u.sku_raw
left join products pt on pt.title_key = {{ title_key('u.title_raw') }} and ps.sku is null
left join fuzzy f on f.title_key = {{ title_key('u.title_raw') }} and ps.sku is null and pt.sku is null
