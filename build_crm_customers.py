"""
Build the crm_customers table: one row per purchasing customer, with everything
the CRM screens need (segment, value, churn risk, revenue at risk), indexed for
fast filtering and sorting.

Run after push_metrics.py and score_churn.py (and again whenever customer
metrics or scores change):
    python build_crm_customers.py

Where each column comes from:
    segment, days_since_last_purchase, expected_90d_spend
                             dbt (dbt_ci360/models/marts), pushed to hm_customers
                             by push_metrics.py. The segment rules live only in
                             customer_segments.sql.
    churn_risk_score         score_churn.py
    risk_level               VERY HIGH / HIGH / MEDIUM / LOW / VERY LOW by score,
                             NOT SCORED when churn_risk_score is NULL
    revenue_at_risk          churn_risk_score * expected_90d_spend
                             (expected spend lost in the next 90 days if they churn)

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
    c.segment,
    c.age,
    c.cohort_month,
    c.first_purchase_date,
    c.last_purchase_date,
    c.days_since_last_purchase,
    c.total_transactions,
    c.customer_lifetime_value,
    c.expected_90d_spend,
    c.churn_risk_score,
    CASE
        WHEN c.churn_risk_score IS NULL THEN 'NOT SCORED'
        WHEN c.churn_risk_score >= 0.8 THEN 'VERY HIGH'
        WHEN c.churn_risk_score >= 0.6 THEN 'HIGH'
        WHEN c.churn_risk_score >= 0.4 THEN 'MEDIUM'
        WHEN c.churn_risk_score >= 0.2 THEN 'LOW'
        ELSE 'VERY LOW'
    END                                          AS risk_level,
    c.churn_risk_score * c.expected_90d_spend    AS revenue_at_risk
FROM hm_customers c
WHERE c.customer_lifetime_value > 0
"""


def main():
    conn = get_connection(read_timeout=1800, write_timeout=1800)
    try:
        cur = conn.cursor()
        start = time.time()

        # Segments come from dbt via push_metrics.py; refuse to build without them.
        cur.execute("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'hm_customers' AND COLUMN_NAME = 'segment'
        """)
        if not cur.fetchone()[0]:
            raise RuntimeError("hm_customers.segment is missing. Run dbt build and push_metrics.py first.")
        cur.execute("SELECT COUNT(*) FROM hm_customers WHERE customer_lifetime_value > 0 AND segment IS NULL")
        (missing,) = cur.fetchone()
        if missing:
            raise RuntimeError(f"{missing:,} purchasing customers have no segment. "
                               "Run dbt build and push_metrics.py first.")

        print("Building crm_customers_new...")
        cur.execute("DROP TABLE IF EXISTS crm_customers_new")
        cur.execute(CREATE_SQL)
        cur.execute(INSERT_SQL)
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
            print("  Note: unscored customers show as NOT SCORED. Run score_churn.py, then rebuild.")
        print(f"\nOK: crm_customers ready ({time.time() - start:.0f}s)")
    except Exception as e:
        conn.rollback()
        print(f"Error building crm_customers: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
