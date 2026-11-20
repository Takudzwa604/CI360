-- Transactions, typed on read. Exact duplicate rows (2.97M, 9.4%) are KEPT:
-- the data has no quantity column, so a repeated row is another unit sold.
-- Prices are DECIMAL(10,5), the same type MySQL stores, so totals reconcile.
select
    t_dat,
    customer_id,
    article_id,
    price,
    sales_channel_id
from {{ source('hm', 'transactions') }}
