-- Units, unique buyers and revenue per product.
select
    article_id,
    count(*)                    as units_sold,
    count(distinct customer_id) as unique_customers,
    sum(price)                  as revenue
from {{ ref('stg_transactions') }}
group by article_id
