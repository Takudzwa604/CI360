"""
Push the dbt-built analytics tables from DuckDB to MySQL.

The tables are built and tested by the dbt project in dbt_ci360/ (run `dbt build`
there first). This script only copies the small results to MySQL, where the API
reads them.

Usage:
    cd dbt_ci360 && dbt build && cd ..
    python push_metrics.py

Safeguards (nothing is written unless all pass):
    1. The last dbt command was a full `dbt build` and every model and test passed.
    2. MySQL hm_transactions has exactly as many rows as the dbt transactions model,
       so the MySQL tables always describe the data the API queries.

What it writes:
    article_summary    article_id, units_sold, unique_customers, revenue
    monthly_revenue    month, revenue
    hm_customers       customer_lifetime_value, total_transactions,
                       first_purchase_date, last_purchase_date,
                       days_since_last_purchase, cohort_month,
                       segment, expected_90d_spend
                       (churn_risk_score is left untouched)
Segment rules live only in the dbt model customer_segments; build_crm_customers.py
reads the pushed segment rather than recomputing it.
Summary tables are built under *_new names and swapped in atomically, so the API
never sees a half-built table. The swap replaces article_summary, which drops its
foreign key, so re-run setup_relationships.sql afterwards.

Then run score_churn.py (if scores need refreshing) and build_crm_customers.py.
"""
import functools
import json
import os
import sys
import time

import duckdb

print = functools.partial(print, flush=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DBT_DIR = os.path.join(BASE_DIR, "dbt_ci360")
DUCK_PATH = os.path.join(DBT_DIR, "ci360_dbt.duckdb")
RUN_RESULTS = os.path.join(DBT_DIR, "target", "run_results.json")
CHUNK = 20_000

OK_STATUSES = {"success", "pass"}   # models report "success", tests report "pass"


def check_dbt_build():
    """Refuse to push unless the most recent dbt command was a full, green `dbt build`."""
    if not os.path.exists(DUCK_PATH) or not os.path.exists(RUN_RESULTS):
        sys.exit("No dbt build found. Run `dbt build` in dbt_ci360/ first. Nothing written.")
    with open(RUN_RESULTS, encoding="utf-8") as f:
        results = json.load(f)
    args = results.get("args", {})
    if args.get("which") != "build" or args.get("select"):
        sys.exit("The last dbt command was not a full `dbt build` (e.g. `dbt docs generate` "
                 "overwrites the results). Run `dbt build` in dbt_ci360/ first. Nothing written.")
    checked = [r for r in results["results"] if r["unique_id"].startswith(("model.", "test."))]
    failed = [r["unique_id"] for r in checked if r["status"] not in OK_STATUSES]
    if failed:
        sys.exit("dbt build did not pass:\n  " + "\n  ".join(failed) + "\nNothing written.")
    n_models = sum(r["unique_id"].startswith("model.") for r in results["results"])
    n_tests = sum(r["unique_id"].startswith("test.") for r in results["results"])
    print(f"dbt build passed ({n_models} models, {n_tests} tests, "
          f"{results['metadata']['generated_at'][:19]} UTC)")


# ---------------------------------------------------------------- push to MySQL

SUMMARY_DDL = {
    "article_summary": """
        CREATE TABLE article_summary_new (
            article_id BIGINT PRIMARY KEY,
            units_sold BIGINT,
            unique_customers BIGINT,
            revenue DECIMAL(20,5)
        ) ENGINE=InnoDB""",
    "monthly_revenue": """
        CREATE TABLE monthly_revenue_new (
            month VARCHAR(7) PRIMARY KEY,
            revenue DECIMAL(20,5)
        ) ENGINE=InnoDB""",
}


def insert_rows(conn, table, cols, rows):
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    done = 0
    with conn.cursor() as cur:
        for i in range(0, len(rows), CHUNK):
            cur.executemany(sql, rows[i:i + CHUNK])
            conn.commit()
            done += len(rows[i:i + CHUNK])
            if done % 200_000 < CHUNK or done == len(rows):
                print(f"    {done:,}/{len(rows):,}")


def swap(cur, name):
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (name,))
    if cur.fetchone()[0]:
        cur.execute(f"DROP TABLE IF EXISTS {name}_old")
        cur.execute(f"RENAME TABLE {name} TO {name}_old, {name}_new TO {name}")
        cur.execute(f"DROP TABLE {name}_old")
    else:
        cur.execute(f"RENAME TABLE {name}_new TO {name}")


# Columns added to hm_customers by this script (created on first run).
NEW_CUSTOMER_COLUMNS = {"segment": "VARCHAR(20)", "expected_90d_spend": "DOUBLE"}


def ensure_customer_columns(cur):
    for col, typ in NEW_CUSTOMER_COLUMNS.items():
        cur.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'hm_customers' AND COLUMN_NAME = %s", (col,))
        if not cur.fetchone()[0]:
            print(f"  adding hm_customers.{col}")
            cur.execute(f"ALTER TABLE hm_customers ADD COLUMN {col} {typ}")


def push():
    from db_config import get_connection

    check_dbt_build()
    start = time.time()
    duck = duckdb.connect(DUCK_PATH, read_only=True)
    n_tx = duck.execute("SELECT COUNT(*) FROM main_staging.stg_transactions").fetchone()[0]

    conn = get_connection(read_timeout=1800, write_timeout=1800, autocommit=False)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM hm_transactions")
    n_mysql = cur.fetchone()[0]
    if n_mysql != n_tx:
        sys.exit(f"MySQL hm_transactions has {n_mysql:,} rows but the dbt build has {n_tx:,}. "
                 "Finish the load first (resume_transactions_load.py). Nothing written.")
    print(f"hm_transactions complete ({n_mysql:,} rows). Pushing results...")

    tables = {
        "article_summary": ["article_id", "units_sold", "unique_customers", "revenue"],
        "monthly_revenue": ["month", "revenue"],
    }
    for name, cols in tables.items():
        print(f"  {name}...")
        rows = duck.execute(f"SELECT {', '.join(cols)} FROM main.{name}").fetchall()
        cur.execute(f"DROP TABLE IF EXISTS {name}_new")
        cur.execute(SUMMARY_DDL[name])
        insert_rows(conn, f"{name}_new", cols, rows)
        swap(cur, name)
        conn.commit()

    # hm_customers: bulk-load into a staging table, then one UPDATE ... JOIN.
    print("  hm_customers metrics...")
    ensure_customer_columns(cur)
    cols = ["customer_id", "customer_lifetime_value", "total_transactions", "first_purchase_date",
            "last_purchase_date", "days_since_last_purchase", "cohort_month",
            "segment", "expected_90d_spend"]
    rows = duck.execute("""
        SELECT m.customer_id, m.customer_lifetime_value, m.total_transactions,
               m.first_purchase_date, m.last_purchase_date, m.days_since_last_purchase,
               m.cohort_month, s.segment, s.expected_90d_spend
        FROM main.customer_metrics m
        LEFT JOIN main.customer_segments s USING (customer_id)   -- NULL for non-buyers
    """).fetchall()
    cur.execute("DROP TABLE IF EXISTS customer_metrics_stage")
    cur.execute("""
        CREATE TABLE customer_metrics_stage (
            customer_id VARCHAR(64) PRIMARY KEY,
            customer_lifetime_value DECIMAL(12,2),
            total_transactions INT,
            first_purchase_date DATE,
            last_purchase_date DATE,
            days_since_last_purchase INT,
            cohort_month VARCHAR(7),
            segment VARCHAR(20),
            expected_90d_spend DOUBLE
        ) ENGINE=InnoDB""")
    insert_rows(conn, "customer_metrics_stage", cols, rows)
    print("    applying UPDATE...")
    cur.execute("""
        UPDATE hm_customers c JOIN customer_metrics_stage s USING (customer_id)
        SET c.customer_lifetime_value  = s.customer_lifetime_value,
            c.total_transactions       = s.total_transactions,
            c.first_purchase_date      = s.first_purchase_date,
            c.last_purchase_date       = s.last_purchase_date,
            c.days_since_last_purchase = s.days_since_last_purchase,
            c.cohort_month             = s.cohort_month,
            c.segment                  = s.segment,
            c.expected_90d_spend       = s.expected_90d_spend
    """)
    print(f"    {cur.rowcount:,} customers updated")
    cur.execute("DROP TABLE customer_metrics_stage")
    conn.commit()

    conn.close()
    duck.close()
    print(f"\nOK: push done in {time.time() - start:.0f}s. "
          "Next: setup_relationships.sql, then python build_crm_customers.py")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(__doc__)
    push()
