-- Fails if revenue differs between the raw transactions and either summary table.
with totals as (
    select
        (select sum(price)   from {{ ref('stg_transactions') }}) as tx_revenue,
        (select sum(revenue) from {{ ref('article_summary') }})  as article_revenue,
        (select sum(revenue) from {{ ref('monthly_revenue') }})  as monthly_revenue
)
select * from totals
where tx_revenue != article_revenue or tx_revenue != monthly_revenue
