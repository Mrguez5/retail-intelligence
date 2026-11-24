-- One row per scraper run: was it complete?
with runs as (
    select
        _source_file                          as source_file,
        min(try_cast(scraped_at as timestamp)) as scraped_at,
        count(*)                              as rows_scraped
    from {{ source('bronze', 'competitor_snapshots') }}
    group by 1
)
select
    *,
    cast(scraped_at as date)                                    as run_date,
    median(rows_scraped) over ()                                as expected_rows,
    rows_scraped < 0.95 * median(rows_scraped) over ()          as is_partial
from runs
