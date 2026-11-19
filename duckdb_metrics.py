"""
Compute the CI360 analytics tables locally with DuckDB, straight from the CSVs,
then push the (small) results to MySQL.

Replaces the slow MySQL steps in Data.ipynb (the per-customer correlated-subquery
UPDATEs and the CREATE TABLE ... AS aggregations over hm_transactions).

Usage:
    python duckdb_metrics.py compute   # local only, reads CSVs -> ci360_metrics.duckdb
    python duckdb_metrics.py push      # writes results to MySQL (needs a complete hm_transactions)

`compute` never touches MySQL, so it is safe to run while a MySQL load is running.
`push` refuses to run unless MySQL hm_transactions has the same row count as
transactions.csv, so the MySQL tables always describe the data the API queries.

What `push` writes (logic mirrors Data.ipynb):
    article_summary    article_id, units_sold, unique_customers, revenue
    monthly_revenue    month, revenue
    hm_customers       customer_lifetime_value, total_transactions,
                       first_purchase_date, last_purchase_date,
                       days_since_last_purchase, cohort_month
                       (churn_risk_score is left untouched)
Summary tables are built under *_new names and swapped in atomically, so the API
never sees a half-built table. The swap replaces article_summary, which drops its
foreign key, so re-run setup_relationships.sql afterwards.

Then run score_churn.py and build_crm_customers.py (which assigns segments).
"""
import functools
import sys
import time

import duckdb

print = functools.partial(print, flush=True)

DUCK_PATH = "ci360_metrics.duckdb"
CHUNK = 20_000

# Prices are stored as DECIMAL(10,5) in MySQL; cast the same way so sums match.
TRANSACTIONS = """
    read_csv('transactions.csv', header=true, columns={
        't_dat': 'DATE', 'customer_id': 'VARCHAR', 'article_id': 'BIGINT',
        'price': 'DECIMAL(10,5)', 'sales_channel_id': 'INTEGER'})
"""
CUSTOMERS = "read_csv('customers.csv', header=true, all_varchar=true)"


def compute():
    start = time.time()
    con = duckdb.connect(DUCK_PATH)

    print("Loading transactions.csv into DuckDB...")
    con.execute(f"CREATE OR REPLACE TABLE tx AS SELECT * FROM {TRANSACTIONS}")
    n_tx = con.execute("SELECT COUNT(*) FROM tx").fetchone()[0]
    print(f"  {n_tx:,} transactions ({time.time() - start:.0f}s)")

    print("article_summary...")
    con.execute("""
        CREATE OR REPLACE TABLE article_summary AS
        SELECT article_id,
               COUNT(*)                    AS units_sold,
               COUNT(DISTINCT customer_id) AS unique_customers,
               SUM(price)                  AS revenue
        FROM tx GROUP BY article_id
    """)

    print("monthly_revenue...")
    con.execute("""
        CREATE OR REPLACE TABLE monthly_revenue AS
        SELECT strftime(t_dat, '%Y-%m') AS month, SUM(price) AS revenue
        FROM tx GROUP BY month ORDER BY month
    """)

    print("customer metrics...")
    con.execute(f"""
        CREATE OR REPLACE TABLE customer_metrics AS
        WITH agg AS (
            SELECT customer_id,
                   ROUND(SUM(price), 2) AS customer_lifetime_value,
                   COUNT(*)             AS total_transactions,
                   MIN(t_dat)           AS first_purchase_date,
                   MAX(t_dat)           AS last_purchase_date
            FROM tx GROUP BY customer_id
        )
        SELECT c.customer_id,
               COALESCE(a.customer_lifetime_value, 0)          AS customer_lifetime_value,
               COALESCE(a.total_transactions, 0)               AS total_transactions,
               a.first_purchase_date,
               a.last_purchase_date,
               date_diff('day', a.last_purchase_date, current_date) AS days_since_last_purchase,
               strftime(a.first_purchase_date, '%Y-%m')        AS cohort_month
        FROM (SELECT DISTINCT customer_id FROM {CUSTOMERS}) c
        LEFT JOIN agg a USING (customer_id)
    """)

    print("\nResults:")
    for t in ("article_summary", "monthly_revenue", "customer_metrics"):
        print(f"  {t:18s} {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>10,} rows")
    first, last = con.execute("SELECT MIN(month), MAX(month) FROM monthly_revenue").fetchone()
    print(f"\nMonths covered: {first} .. {last}")
    print(f"OK: compute done in {time.time() - start:.0f}s -> {DUCK_PATH}")
    con.close()


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


def push():
    from db_config import get_connection

    start = time.time()
    duck = duckdb.connect(DUCK_PATH, read_only=True)
    n_tx = duck.execute("SELECT COUNT(*) FROM tx").fetchone()[0]

    conn = get_connection(read_timeout=1800, write_timeout=1800, autocommit=False)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM hm_transactions")
    n_mysql = cur.fetchone()[0]
    if n_mysql != n_tx:
        sys.exit(f"MySQL hm_transactions has {n_mysql:,} rows but transactions.csv has {n_tx:,}. "
                 "Finish the load first (resume_transactions_load.py). Nothing written.")
    print(f"hm_transactions complete ({n_mysql:,} rows). Pushing results...")

    tables = {
        "article_summary": ["article_id", "units_sold", "unique_customers", "revenue"],
        "monthly_revenue": ["month", "revenue"],
    }
    for name, cols in tables.items():
        print(f"  {name}...")
        rows = duck.execute(f"SELECT {', '.join(cols)} FROM {name}").fetchall()
        cur.execute(f"DROP TABLE IF EXISTS {name}_new")
        cur.execute(SUMMARY_DDL[name])
        insert_rows(conn, f"{name}_new", cols, rows)
        swap(cur, name)
        conn.commit()

    # hm_customers: bulk-load into a staging table, then one UPDATE ... JOIN.
    print("  hm_customers metrics...")
    cols = ["customer_id", "customer_lifetime_value", "total_transactions", "first_purchase_date",
            "last_purchase_date", "days_since_last_purchase", "cohort_month"]
    rows = duck.execute(f"SELECT {', '.join(cols)} FROM customer_metrics").fetchall()
    cur.execute("DROP TABLE IF EXISTS customer_metrics_stage")
    cur.execute("""
        CREATE TABLE customer_metrics_stage (
            customer_id VARCHAR(64) PRIMARY KEY,
            customer_lifetime_value DECIMAL(12,2),
            total_transactions INT,
            first_purchase_date DATE,
            last_purchase_date DATE,
            days_since_last_purchase INT,
            cohort_month VARCHAR(7)
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
            c.cohort_month             = s.cohort_month
    """)
    print(f"    {cur.rowcount:,} customers updated")
    cur.execute("DROP TABLE customer_metrics_stage")
    conn.commit()

    conn.close()
    duck.close()
    print(f"\nOK: push done in {time.time() - start:.0f}s. Next: python build_crm_customers.py")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("compute", "push"):
        sys.exit(__doc__)
    {"compute": compute, "push": push}[sys.argv[1]]()
