"""
Populate churn_risk_score column in hm_customers table.

churn_risk_score = probability that the customer makes no purchase in the
next HORIZON_DAYS days (see churn_pipeline.py). The model is trained on the
most recent fully labelled period and scores every customer who has at least
one transaction. Customers with no transactions are left NULL; the API
reports them as "NOT SCORED".

Run churn_model.py to see the out-of-time evaluation for this model.
"""
import sys
import time

import pymysql

from churn_pipeline import FEATURES, HORIZON_DAYS, build_features, build_labeled, get_cutoffs, train
from db_config import get_connection

print("=" * 70)
print("POPULATING CHURN RISK SCORES IN hm_customers TABLE")
print("=" * 70)

conn = get_connection()
try:
    _, label_cutoff, score_date = get_cutoffs(conn)
    print(f"Training on cutoff {label_cutoff} (labels through {score_date})...")
    train_df = build_labeled(conn, label_cutoff)
    print(f"Scoring as of {score_date}...")
    df = build_features(conn, score_date)
except Exception as e:
    print(f"Error reading data: {e}")
    sys.exit(1)
finally:
    conn.close()

print(f"Training set: {len(train_df):,} customers | churn rate {train_df['churned'].mean():.1%}")
print("Training Random Forest model...")
model = train(train_df)
print("✓ Model trained\n")

df["churn_risk_score"] = model.predict_proba(df[FEATURES])[:, 1]
print(f"✓ Scored {len(df):,} customers (P(no purchase in next {HORIZON_DAYS} days))")
print(f"  Score range: {df['churn_risk_score'].min():.4f} - {df['churn_risk_score'].max():.4f}")
print(f"  Mean score: {df['churn_risk_score'].mean():.4f}\n")

# Update database with scores.
# Row-by-row UPDATEs mean ~1.25M round trips to the remote server (slow and
# prone to dropped connections). Instead: bulk-insert scores into a staging
# table (pymysql turns executemany INSERT into multi-row statements), then
# apply them with one joined UPDATE. The staging table is a real table, so a
# reconnect mid-load doesn't lose progress; INSERT ... ON DUPLICATE KEY makes
# retried batches safe.
print("Updating hm_customers table with churn scores...")
conn = get_connection(read_timeout=1800, write_timeout=1800)
cur = conn.cursor()

cur.execute("DROP TABLE IF EXISTS churn_scores_stage")
cur.execute("""
    CREATE TABLE churn_scores_stage (
        customer_id VARCHAR(64) COLLATE utf8mb4_0900_ai_ci PRIMARY KEY,
        score FLOAT NOT NULL
    ) ENGINE=InnoDB
""")
conn.commit()

STAGE_SQL = (
    "INSERT INTO churn_scores_stage (customer_id, score) VALUES (%s, %s) "
    "ON DUPLICATE KEY UPDATE score = VALUES(score)"
)
batch_size = 20000
total_rows = len(df)
loaded = 0
max_retries = 3

for i in range(0, total_rows, batch_size):
    batch = df.iloc[i:i + batch_size]
    params = [
        (cid, float(s))
        for cid, s in zip(batch["customer_id"], batch["churn_risk_score"])
    ]

    for attempt in range(1, max_retries + 1):
        try:
            conn.ping(reconnect=True)
            cur.executemany(STAGE_SQL, params)
            conn.commit()
            loaded += len(batch)
            print(f"  Staged {loaded:,} / {total_rows:,} scores ({loaded / total_rows * 100:.1f}%)...", end="\r")
            break
        except pymysql.err.OperationalError as e:
            if attempt == max_retries:
                print(f"\n  ERROR: Failed after {max_retries} retries: {e}")
                sys.exit(1)
            print(f"\n  Connection lost, retrying ({attempt}/{max_retries})...")
            time.sleep(5)
            try:
                conn.close()
            except Exception:
                pass
            conn = get_connection(read_timeout=1800, write_timeout=1800)
            cur = conn.cursor()

print(f"\n  Applying scores to hm_customers...")
try:
    conn.ping(reconnect=True)
    # LEFT JOIN: customers with no transactions get NULL, clearing any stale score.
    cur.execute("""
        UPDATE hm_customers c
        LEFT JOIN churn_scores_stage s ON s.customer_id = c.customer_id
        SET c.churn_risk_score = s.score
    """)
    conn.commit()
    cur.execute("DROP TABLE churn_scores_stage")
    conn.commit()
except Exception as e:
    print(f"\n  ERROR applying scores: {e}")
    conn.close()
    sys.exit(1)
updated = loaded

print(f"✓ Updated {updated:,} customers\n")

# Verify update
null_scores = None
try:
    cur.execute("""
    SELECT
        COUNT(*) as total,
        COUNT(churn_risk_score) as with_scores,
        COUNT(*) - COUNT(churn_risk_score) as null_scores
    FROM hm_customers
    """)
    total, with_scores, null_scores = cur.fetchone()
    print("Verification:")
    print(f"  Total customers: {total:,}")
    print(f"  With churn scores: {with_scores:,}")
    print(f"  Not scored (no transactions): {null_scores:,}")
except Exception as e:
    print(f"Error verifying: {e}")

# Show distribution
print("\nChurn Score Distribution:")
try:
    cur.execute("""
    SELECT
        CASE
            WHEN churn_risk_score >= 0.8 THEN '0.8 - 1.0 (VERY HIGH)'
            WHEN churn_risk_score >= 0.6 THEN '0.6 - 0.8 (HIGH)'
            WHEN churn_risk_score >= 0.4 THEN '0.4 - 0.6 (MEDIUM)'
            WHEN churn_risk_score >= 0.2 THEN '0.2 - 0.4 (LOW)'
            ELSE '0.0 - 0.2 (VERY LOW)'
        END as risk_level,
        COUNT(*) as customer_count,
        ROUND(COUNT(*) / (SELECT COUNT(*) FROM hm_customers) * 100, 2) as percentage,
        ROUND(AVG(customer_lifetime_value), 2) as avg_ltv,
        ROUND(SUM(customer_lifetime_value), 2) as total_ltv
    FROM hm_customers
    WHERE churn_risk_score IS NOT NULL
    GROUP BY risk_level
    ORDER BY MIN(churn_risk_score) DESC
    """)
    for row in cur.fetchall():
        avg_ltv = row[3] or 0
        total_ltv = row[4] or 0
        print(f"  {row[0]:25s} | {row[1]:10,} customers ({row[2]:6}%) | Avg LTV: ${avg_ltv:,.2f} | Total LTV: ${total_ltv:,.2f}")
except Exception as e:
    print(f"Error getting distribution: {e}")

conn.close()

print("\n" + "=" * 70)
print("✓ CHURN SCORES POPULATED IN hm_customers TABLE")
print("=" * 70)
print(f"Scored {updated:,} customers.", end=" ")
if null_scores:
    print(f"{null_scores:,} customers without transactions remain unscored (API returns 'NOT SCORED').")
else:
    print()
print("Ready for FastAPI endpoints to use.")
