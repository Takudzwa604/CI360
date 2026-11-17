import numpy as np


class PopularityRecommender:
    """
    A simple popularity-based recommender system.
    Recommends the most frequently purchased items.

    Kept as-is for two reasons: it's the cold-start fallback used internally
    by HybridRecommender below, and it's what any previously pickled model.pkl
    was built from, so old model files can still be unpickled.
    """

    def __init__(self, limit=10):
        self.limit = limit

    def recommend(self, cur):
        """
        Returns the top-N most popular items overall.
        """
        query = """
            SELECT
                a.article_id,
                b.prod_name,
                a.units_sold,
                a.revenue
            FROM article_summary a
            JOIN hm_articles b
                ON a.article_id = b.article_id
            ORDER BY a.units_sold DESC
            LIMIT %s;
        """
        cur.execute(query, (self.limit,))
        return cur.fetchall()

    def recommend_for_customer(self, cur, customer_id):
        """
        Returns recommendations based on the customer's favorite product type.
        Falls back to global popularity if customer has no history.
        """

        # find customer's favorite product type
        fav_query = """
            SELECT
                a.product_type_name,
                COUNT(*) AS cnt
            FROM hm_transactions t
            JOIN hm_articles a
                ON t.article_id = a.article_id
            WHERE t.customer_id = %s
            GROUP BY a.product_type_name
            ORDER BY cnt DESC
            LIMIT 1;
        """
        cur.execute(fav_query, (customer_id,))
        row = cur.fetchone()

        if not row or row["product_type_name"] is None:
            return self.recommend(cur)

        fav_type = row["product_type_name"]

        # recommend top items in that category
        rec_query = """
            SELECT
                a.article_id,
                a.prod_name,
                s.units_sold
            FROM hm_articles a
            JOIN article_summary s
                ON a.article_id = s.article_id
            WHERE a.product_type_name = %s
            ORDER BY s.units_sold DESC
            LIMIT %s;
        """
        cur.execute(rec_query, (fav_type, self.limit))
        results = cur.fetchall()

        # If category has no items → fallback
        if not results:
            return self.recommend(cur)

        return results


class HybridRecommender:
    """
    Hybrid recommender.

    For customers with enough purchase history, ranks items using
    implicit-feedback collaborative filtering (ALS factors trained offline
    by scripts/train_model.py on hm_transactions). Customers who weren't in
    the training set (too new, or too few purchases) fall back to the
    popularity / favorite-category logic in PopularityRecommender - this is
    the actual "hybrid" the README describes: a real ML model for customers
    it has enough signal on, plus a rule-based fallback for everyone else.

    This class only holds plain numpy arrays and dicts (no dependency on the
    `implicit` library at import time), so the FastAPI app doesn't need
    `implicit`/`scipy` installed just to serve requests - only the training
    script does.
    """

    def __init__(self, user_factors, item_factors, customer_index, article_index,
                 article_lookup, min_interactions=3, limit=10):
        self.user_factors = user_factors        # np.ndarray [n_customers, k]
        self.item_factors = item_factors        # np.ndarray [n_articles, k]
        self.customer_index = customer_index    # dict: customer_id -> row index
        self.article_index = article_index      # dict: article_id -> row index
        self.index_to_article = {v: k for k, v in article_index.items()}
        self.article_lookup = article_lookup    # dict: article_id -> {"prod_name": ...}
        self.min_interactions = min_interactions
        self.limit = limit
        self._fallback = PopularityRecommender(limit=limit)

    def recommend(self, cur):
        """No single customer to personalize for - use global popularity."""
        return self._fallback.recommend(cur)

    def recommend_for_customer(self, cur, customer_id):
        idx = self.customer_index.get(customer_id)

        if idx is None:
            # Not in the training set (cold-start / too little history).
            return self._fallback.recommend_for_customer(cur, customer_id)

        scores = self.item_factors @ self.user_factors[idx]
        top_idx = np.argsort(-scores)[: self.limit]

        results = []
        for i in top_idx:
            article_id = self.index_to_article.get(int(i))
            info = self.article_lookup.get(article_id, {})
            results.append({
                "article_id": article_id,
                "prod_name": info.get("prod_name"),
                "score": round(float(scores[i]), 4),
            })

        if not results:
            return self._fallback.recommend_for_customer(cur, customer_id)
        return results
