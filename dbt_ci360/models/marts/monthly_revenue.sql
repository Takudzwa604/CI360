-- Revenue per calendar month.
select
    strftime(t_dat, '%Y-%m') as month,
    sum(price)               as revenue
from {{ ref('stg_transactions') }}
group by month
order by month
