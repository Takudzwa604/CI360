-- Customers, cleaned the same way Data.ipynb loads them into MySQL:
--   * blank / "NULL" / non-numeric ages become NULL (never imputed)
--   * blank / "NULL" postal codes become NULL
--   * FN, Active, club status and news frequency are not used by CI360 and not loaded
-- One hashed postal code is shared by ~120K customers: a placeholder, flagged here
-- so no analysis treats it as a real location.
with cleaned as (
    select
        trim(customer_id)                                                    as customer_id,
        try_cast(try_cast(nullif(trim(age), '') as double) as integer)       as age,
        case when trim(coalesce(postal_code, '')) in ('', 'NULL') then null
             else trim(postal_code) end                                      as postal_code
    from {{ source('hm', 'customers') }}
)
select
    customer_id,
    age,
    postal_code,
    postal_code is not null
        and count(*) over (partition by postal_code) > 10000                 as is_placeholder_postal_code
from cleaned
