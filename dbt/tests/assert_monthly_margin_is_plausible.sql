-- Guard rail: a cost or revenue bug shows up as an impossible margin
select * from {{ ref('mart_kpis_monthly') }}
where margin_pct not between 0.15 and 0.60
