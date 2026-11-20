-- One row per purchasing customer with a value + recency segment and expected
-- 90-day spend. This is the only place the segment rules live; rules are applied
-- top to bottom and the first match wins. push_metrics.py writes the result to
-- hm_customers, and build_crm_customers.py adds the churn score and revenue at risk.
select
    customer_id,
    case
        when customer_lifetime_value >= 1.50 and days_since_last_purchase < 30              then 'High-Value Loyal'
        when customer_lifetime_value >= 0.75 and days_since_last_purchase between 31 and 90 then 'Growth Potential'
        when days_since_last_purchase > 180                                                 then 'Dormant'
        when days_since_last_purchase between 91 and 180                                    then 'At-Risk'
        else 'One-Time'
    end                                                                                     as segment,
    customer_lifetime_value,
    total_transactions,
    days_since_last_purchase,
    tenure_days,
    customer_lifetime_value * 90 / greatest(tenure_days, 90)                                as expected_90d_spend
from {{ ref('customer_metrics') }}
where customer_lifetime_value > 0
