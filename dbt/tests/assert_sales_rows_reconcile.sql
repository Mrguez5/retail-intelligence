-- Nothing is lost: every bronze sales row ends up either in silver_sales or in quarantine
with bronze as (
    select (select count(*) from {{ source('bronze', 'sales_web') }})
         + (select count(*) from {{ source('bronze', 'sales_marketplace') }})
         + (select count(*) from {{ source('bronze', 'sales_store') }}) as n
),
silver as (
    select (select count(*) from {{ ref('silver_sales') }})
         + (select count(*) from {{ ref('silver_sales_quarantine') }}) as n
)
select bronze.n as bronze_rows, silver.n as silver_rows
from bronze, silver
where bronze.n <> silver.n
