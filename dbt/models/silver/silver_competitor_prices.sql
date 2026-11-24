-- Every competitor observation, typed, with a data-quality verdict per row
with typed as (
    select
        try_cast(scraped_at as timestamp)  as scraped_at,
        trim(url)                          as url,
        trim(title)                        as title,
        {{ title_key('title') }}           as title_key,
        trim(category)                     as category,
        try_cast(price as double)          as price_gbp,
        lower(in_stock) = 'true'           as in_stock,
        _source_file                       as source_file,
        _row_number                        as source_row
    from {{ source('bronze', 'competitor_snapshots') }}
),
with_history as (
    select
        *,
        median(price_gbp) over (partition by url) as median_price_gbp
    from typed
)
select
    *,
    case
        when scraped_at is null              then 'invalid_timestamp'
        when price_gbp is null               then 'price_not_numeric'
        when price_gbp <= 0                  then 'price_zero_or_negative'
        when price_gbp > 5 * median_price_gbp then 'price_outlier'
    end                                      as dq_issue
from with_history
