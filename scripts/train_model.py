"""
Train the CI360 hybrid recommender.

Pulls purchase counts per (customer, article) from hm_transactions, fits an
implicit-feedback ALS collaborative-filtering model on customers with enough
purchase history, and pickles a HybridRecommender (see api/recommender.py)
that falls back to popularity/category recommendations for everyone else.

Requires: pip install implicit scipy numpy pymysql python-dotenv

Run from the project root, using the same .env the API uses:
    python scripts/train_model.py
"""
import os

import numpy as np
import pymysql
from dotenv import load_dotenv
from scipy.sparse import coo_matrix
from implicit.als import AlternatingLeastSquares

from api.recommender import HybridRecommender

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, "api", ".env"))

MIN_INTERACTIONS = 3   # customers below this total purchase count use the fallback instead
N_FACTORS = 50
ITERATIONS = 15
REGULARIZATION = 0.05


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main():
    conn = pymysql.connect(
        host=require_env("DB_HOST"),
        port=int(require_env("DB_PORT")),
        user=require_env("DB_USER"),
        passwd=require_env("DB_PASSWORD"),
        db=require_env("DB_NAME"),
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = conn.cursor()

    print("Pulling purchase counts from hm_transactions ...")
    cur.execute("""
        SELECT customer_id, article_id, COUNT(*) AS cnt
        FROM hm_transactions
        GROUP BY customer_id, article_id
    """)
    rows = cur.fetchall()
    print(f"{len(rows):,} (customer, article) pairs")

    # Only model customers with enough total purchases to get a reliable signal;
    # everyone else is handled by the popularity/category fallback at request time.
    totals = {}
    for r in rows:
        totals[r["customer_id"]] = totals.get(r["customer_id"], 0) + r["cnt"]
    eligible_customers = {c for c, total in totals.items() if total >= MIN_INTERACTIONS}
    print(f"{len(eligible_customers):,} customers meet the {MIN_INTERACTIONS}+ purchase threshold")

    customer_index = {}
    article_index = {}
    item_ids, user_ids, counts = [], [], []

    for r in rows:
        cid = r["customer_id"]
        if cid not in eligible_customers:
            continue
        aid = r["article_id"]
        u = customer_index.setdefault(cid, len(customer_index))
        i = article_index.setdefault(aid, len(article_index))
        user_ids.append(u)
        item_ids.append(i)
        counts.append(r["cnt"])

    print(f"Building interaction matrix: {len(customer_index):,} customers x {len(article_index):,} articles")
    item_user = coo_matrix(
        (counts, (item_ids, user_ids)),
        shape=(len(article_index), len(customer_index)),
    ).tocsr()

    print(f"Training ALS model (factors={N_FACTORS}, iterations={ITERATIONS}) ...")
    als = AlternatingLeastSquares(
        factors=N_FACTORS, iterations=ITERATIONS, regularization=REGULARIZATION
    )
    als.fit(item_user)

    print("Pulling product names for lookup ...")
    cur.execute("SELECT article_id, prod_name FROM hm_articles")
    article_lookup = {r["article_id"]: {"prod_name": r["prod_name"]} for r in cur.fetchall()}

    model = HybridRecommender(
        user_factors=np.asarray(als.user_factors),
        item_factors=np.asarray(als.item_factors),
        customer_index=customer_index,
        article_index=article_index,
        article_lookup=article_lookup,
        min_interactions=MIN_INTERACTIONS,
        limit=10,
    )

    model_dir = os.path.join(BASE_DIR, "api", "model")
    os.makedirs(model_dir, exist_ok=True)
    model_path = os.path.join(model_dir, "model.pkl")

    import pickle
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    print(f"Saved hybrid model to {model_path}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
