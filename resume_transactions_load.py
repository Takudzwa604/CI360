"""
Resume loading transactions.csv into hm_transactions after an interrupted load.

The original notebook load inserts CSV rows strictly in file order, so the rows
already in the table are CSV rows 1..N (N = current COUNT(*)). This script:

  1. Reads N from the table.
  2. Verifies the boundary: transactions.csv is sorted by t_dat, so the table
     must contain exactly as many rows for the boundary date as CSV rows 1..N
     do, and no rows dated after it. If not, it aborts without inserting.
  3. Inserts CSV rows N+1..end in chunks, committing each chunk on its own.

Safe to re-run: every run starts from the table's current COUNT(*), and each
chunk commits atomically, so a crash never leaves a half-written chunk.
"""
import csv
import sys
import time

import pymysql

from db_config import get_connection

CSV_PATH = "transactions.csv"
CHUNK = 20_000
MAX_RETRIES = 5

INSERT_SQL = """
    INSERT INTO hm_transactions
    (t_dat, customer_id, article_id, price, sales_channel_id)
    VALUES (%s,%s,%s,%s,%s)
"""


def connect():
    return get_connection(read_timeout=1800, write_timeout=1800, autocommit=False)


def table_count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM hm_transactions")
        return cur.fetchone()[0]


def main():
    conn = connect()
    done = table_count(conn)
    print(f"Rows already in hm_transactions: {done:,}", flush=True)

    f = open(CSV_PATH, encoding="utf-8", newline="")
    reader = csv.reader(f)
    header = next(reader)
    assert header == ["t_dat", "customer_id", "article_id", "price", "sales_channel_id"], header

    # Pass over the rows already loaded, tracking the boundary date.
    print("Skipping loaded rows in CSV...", flush=True)
    boundary_date, boundary_count, prev_date = None, 0, ""
    for i in range(done):
        row = next(reader)
        d = row[0]
        if d < prev_date:
            sys.exit(f"CSV is not date-sorted at row {i + 1}; cannot verify boundary safely.")
        if d != boundary_date:
            boundary_date, boundary_count = d, 0
        boundary_count += 1
        prev_date = d

    # Verify the table matches CSV rows 1..done at the boundary.
    if done:
        print(f"Verifying boundary date {boundary_date}...", flush=True)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM hm_transactions WHERE t_dat = %s", (boundary_date,))
            db_at = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM hm_transactions WHERE t_dat > %s", (boundary_date,))
            db_after = cur.fetchone()[0]
        print(f"  CSV rows on {boundary_date}: {boundary_count:,} | table: {db_at:,} | table rows after: {db_after:,}", flush=True)
        if db_at != boundary_count or db_after != 0:
            sys.exit("Boundary check FAILED: table does not match CSV prefix. Nothing inserted.")
        print("  Boundary OK.", flush=True)

    total = done
    start = time.time()
    chunk = []

    def flush(chunk):
        nonlocal conn, total
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, chunk)
                conn.commit()
                total += len(chunk)
                return
            except pymysql.err.OperationalError as e:
                print(f"\n  Connection error ({e}); retry {attempt}/{MAX_RETRIES}", flush=True)
                try:
                    conn.close()
                except Exception:
                    pass
                # Reconnect with backoff; the server can be unreachable for minutes.
                while True:
                    time.sleep(30 * attempt)
                    try:
                        conn = connect()
                        now = table_count(conn)
                        break
                    except pymysql.err.OperationalError as e2:
                        attempt += 1
                        print(f"  Reconnect failed ({e2.args[0]}); attempt {attempt}/{MAX_RETRIES}", flush=True)
                        if attempt > MAX_RETRIES:
                            sys.exit("Server unreachable. Re-run the script later to resume.")
                # If the commit landed before the connection dropped, don't insert twice.
                if now == total + len(chunk):
                    total = now
                    return
                if now != total:
                    sys.exit(f"Unexpected row count {now:,} (expected {total:,}). Stopping; re-run to resume.")
        sys.exit("Too many connection failures. Re-run the script to resume.")

    for row in reader:
        chunk.append(row)
        if len(chunk) >= CHUNK:
            flush(chunk)
            chunk = []
            if total % 500_000 < CHUNK:
                rate = (total - done) / (time.time() - start)
                print(f"  {total:,} rows ({rate:,.0f}/s)", flush=True)
    if chunk:
        flush(chunk)

    final = table_count(conn)
    print(f"\nDone. hm_transactions now has {final:,} rows "
          f"({final - done:,} added in {time.time() - start:.0f}s).", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
