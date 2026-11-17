"""
Build the crm_customers table: one row per purchasing customer, with everything
the CRM screens need (segment, value, churn risk, revenue at risk), indexed for
fast filtering and sorting.

Run after duckdb_metrics.py push and Populate_Churn_score.py (and again whenever
customer metrics or scores change):
    python build_crm_customers.py

Definitions (dataset end = latest last_purchase_date in hm_customers):
    days_since_last_purchase = days from last purchase to dataset end
    segment                  = value + recency rules, first match wins:
                               High-Value Loyal  CLV >= 1.50 and last purchase < 30 days ago
                               Growth Potential  CLV >= 0.75 and last purchase 31-90 days ago
                               Dormant           last purchase > 180 days ago
                               At-Risk           last purchase 91-180 days ago
                               One-Time          everyone else
    expected_90d_spend       = customer_lifetime_value * 90 / max(tenure_days, 90)
                               (average 90-day spend over the customer's history)
    revenue_at_risk          = churn_risk_score * expected_90d_spend
                               (expected spend lost in the next 90 days if they churn)
    risk_level               = VERY HIGH / HIGH / MEDIUM / LOW / VERY LOW by score,
                               NOT SCORED when churn_risk_score is NULL

Customers with no purchases (customer_lifetime_value = 0) are excluded.
The new table is built under a temporary name and swapped in atomically, so the
API never sees a half-built table.
"""
import functools
import sys
import time

from db_config import get_connection

print = functools.partial(print, flush=True)  # show progress immediately, even when piped

CREATE_SQL = """
CREATE TABLE crm_customers_new (
    customer_id               VARCHAR(64) PRIMARY KEY,
    segment                   VARCHAR(20),
    age                       INT,
    cohort_month              VARCHAR(7),
    first_purchase_date       DATE,
    last_purchase_date        DATE,
    days_since_last_purchase  INT,
    total_transactions        INT,
    customer_lifetime_value   DECIMAL(12,2),
    expected_90d_spend        DOUBLE,
    churn_risk_score          FLOAT,
    risk_level                VARCHAR(10),
    revenue_at_risk           DOUBLE
) ENGINE=InnoDB
"""

# Secondary indexes are added after the bulk insert: building them once is far
# faster than maintaining five indexes row by row during a ~1.25M-row insert.
INDEX_SQL = """
ALTER TABLE crm_customers_new
    ADD INDEX idx_revenue_at_risk (revenue_at_risk),
    ADD INDEX idx_segment_rar (segment, revenue_at_risk),
    ADD INDEX idx_risk_rar (risk_level, revenue_at_risk),
    ADD INDEX idx_churn_score (churn_risk_score),
    ADD INDEX idx_ltv (customer_lifetime_value)
"""

INSERT_SQL = """
INSERT INTO crm_customers_new
SELECT
    c.customer_id,
    CASE
        WHEN c.customer_lifetime_value >= 1.50
             AND DATEDIFF(%(end)s, c.last_purchase_date) < 30                 THEN 'High-Value Loyal'
        WHEN c.customer_lifetime_value >= 0.75
             AND DATEDIFF(%(end)s, c.last_purchase_date) BETWEEN 31 AND 90    THEN 'Growth Potential'
        WHEN DATEDIFF(%(end)s, c.last_purchase_date) > 180                    THEN 'Dormant'
        WHEN DATEDIFF(%(end)s, c.last_purchase_date) BETWEEN 91 AND 180       THEN 'At-Risk'
        ELSE 'One-Time'
    END                                                                       AS segment,
    c.age,
    c.cohort_month,
    c.first_purchase_date,
    c.last_purchase_date,
    DATEDIFF(%(end)s, c.last_purchase_date)                                   AS days_since_last_purchase,
    c.total_transactions,
    c.customer_lifetime_value,
    c.customer_lifetime_value * 90
        / GREATEST(DATEDIFF(%(end)s, c.first_purchase_date), 90)              AS expected_90d_spend,
    c.churn_risk_score,
    CASE
        WHEN c.churn_risk_score IS NULL THEN 'NOT SCORED'
        WHEN c.churn_risk_score >= 0.8 THEN 'VERY HIGH'
        WHEN c.churn_risk_score >= 0.6 THEN 'HIGH'
        WHEN c.churn_risk_score >= 0.4 THEN 'MEDIUM'
        WHEN c.churn_risk_score >= 0.2 THEN 'LOW'
        ELSE 'VERY LOW'
    END                                                                       AS risk_level,
    c.churn_risk_score * c.customer_lifetime_value * 90
        / GREATEST(DATEDIFF(%(end)s, c.first_purchase_date), 90)              AS revenue_at_risk
FROM hm_customers c
WHERE c.customer_lifetime_value > 0
"""


def main():
    conn = get_connection(read_timeout=1800, write_timeout=1800)
    try:
        cur = conn.cursor()
        start = time.time()

        cur.execute("SELECT MAX(last_purchase_date) FROM hm_customers")
        (end_date,) = cur.fetchone()
        if end_date is None:
            raise RuntimeError("hm_customers.last_purchase_date is empty")
        print(f"Dataset end date: {end_date}")

        print("Building crm_customers_new...")
        cur.execute("DROP TABLE IF EXISTS crm_customers_new")
        cur.execute(CREATE_SQL)
        cur.execute(INSERT_SQL, {"end": end_date})
        inserted = cur.rowcount
        conn.commit()
        print(f"  Inserted {inserted:,} customers ({time.time() - start:.0f}s)")
        print("Adding indexes...")
        cur.execute(INDEX_SQL)
        print(f"  Indexes built ({time.time() - start:.0f}s)")

        # Atomic swap
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'crm_customers'"
        )
        if cur.fetchone()[0]:
            cur.execute("DROP TABLE IF EXISTS crm_customers_old")
            cur.execute("RENAME TABLE crm_customers TO crm_customers_old, crm_customers_new TO crm_customers")
            cur.execute("DROP TABLE crm_customers_old")
        else:
            cur.execute("RENAME TABLE crm_customers_new TO crm_customers")
        conn.commit()

        cur.execute("""
            SELECT COUNT(*), COUNT(churn_risk_score), ROUND(SUM(revenue_at_risk), 2),
                   SUM(segment IS NULL)
            FROM crm_customers
        """)
        total, scored, rar, no_segment = cur.fetchone()
        print("\nSummary:")
        print(f"  Customers:              {total:,}")
        print(f"  With churn score:       {scored:,}")
        print(f"  Without a segment:      {no_segment:,}")
        print(f"  Total revenue at risk:  {rar}")
        if scored < total:
            print("  Note: unscored customers show as NOT SCORED. Run Populate_Churn_score.py, then rebuild.")
        print(f"\nOK: crm_customers ready ({time.time() - start:.0f}s)")
    except Exception as e:
        conn.rollback()
        print(f"Error building crm_customers: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
