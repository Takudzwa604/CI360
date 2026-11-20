-- Fails if the transaction count differs from transactions.csv (31,788,324 rows).
select count(*) as n_rows
from {{ ref('stg_transactions') }}
having count(*) != {{ var('expected_transactions') }}
