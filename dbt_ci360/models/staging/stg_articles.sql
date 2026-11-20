-- Product catalogue. article_id has a leading zero in the CSV ("0663713001");
-- cast to BIGINT so it joins to transactions. 416 products have no description:
-- kept, with the description set to NULL.
select
    cast(article_id as bigint)            as article_id,
    product_code,
    prod_name,
    product_type_name,
    product_group_name,
    colour_group_name,
    department_name,
    index_group_name,
    section_name,
    garment_group_name,
    nullif(trim(detail_desc), '')         as detail_desc
from {{ source('hm', 'articles') }}
