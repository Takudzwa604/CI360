-- Fails if any recency or tenure is negative (a purchase after the dataset end).
select customer_id, days_since_last_purchase, tenure_days
from {{ ref('customer_metrics') }}
where days_since_last_purchase < 0 or tenure_days < 0
