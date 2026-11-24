-- Each price period ends exactly when the next one starts, and consecutive periods differ in price
with h as (
    select *,
        lead(valid_from) over (partition by url order by valid_from) as next_from,
        lead(price_gbp)  over (partition by url order by valid_from) as next_price
    from {{ ref('silver_competitor_price_history') }}
)
select * from h
where (next_from is not null and valid_to <> next_from)
   or next_price = price_gbp
   or valid_to <= valid_from
