-- Lifetime metrics for every registered customer, including those who never bought
-- (value 0, dates NULL). Recency is measured from the last date in the data
-- (2020-09-22), not from today, so the numbers do not drift as time passes.
with agg as (
    select
        customer_id,
        round(sum(price), 2) as customer_lifetime_value,
        count(*)             as total_transactions,
        min(t_dat)           as first_purchase_date,
        max(t_dat)           as last_purchase_date
    from {{ ref('stg_transactions') }}
    group by customer_id
),

dataset_end as (
    select max(t_dat) as end_date from {{ ref('stg_transactions') }}
)

select
    c.customer_id,
    c.age,
    coalesce(a.customer_lifetime_value, 0)                     as customer_lifetime_value,
    coalesce(a.total_transactions, 0)                          as total_transactions,
    a.first_purchase_date,
    a.last_purchase_date,
    date_diff('day', a.last_purchase_date, d.end_date)         as days_since_last_purchase,
    date_diff('day', a.first_purchase_date, d.end_date)        as tenure_days,
    strftime(a.first_purchase_date, '%Y-%m')                   as cohort_month
from {{ ref('stg_customers') }} c
left join agg a using (customer_id)
cross join dataset_end d
