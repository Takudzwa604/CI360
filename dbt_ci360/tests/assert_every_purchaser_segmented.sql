-- Fails if any customer with spend is missing a segment, or a segment holds a non-buyer.
with counts as (
    select
        (select count(*) from {{ ref('customer_metrics') }} where customer_lifetime_value > 0) as purchasers,
        (select count(*) from {{ ref('customer_segments') }})                                  as segmented
)
select * from counts
where purchasers != segmented
