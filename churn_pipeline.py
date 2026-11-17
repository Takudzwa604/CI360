"""
Shared churn pipeline used by churn_model.py (evaluation) and
Populate_Churn_score.py (scoring).

Churn definition (forward-looking, no label leakage):
    For a cutoff date C, features are computed ONLY from transactions on or
    before C. A customer is labelled churned (1) if they make NO purchase in
    the window (C, C + HORIZON_DAYS].

Because the label describes future behaviour, recency is a legitimate
feature: the model learns how past behaviour predicts whether a customer
comes back, instead of re-learning a threshold on one of its own inputs.

Timeline (T = last transaction date in hm_transactions, H = HORIZON_DAYS):
    train cutoff  = T - 2H   (labels from (T-2H, T-H])
    test cutoff   = T - H    (labels from (T-H, T])   -> out-of-time evaluation
    score as-of   = T        -> probability of no purchase in the next H days
"""
from datetime import timedelta

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

HORIZON_DAYS = 90

FEATURES = [
    "recency_days",
    "tenure_days",
    "n_items",
    "n_visits",
    "monetary",
    "items_last_90d",
    "online_share",
]

_FEATURE_SQL = """
SELECT
    customer_id,
    DATEDIFF(%(cutoff)s, MAX(t_dat))                             AS recency_days,
    DATEDIFF(%(cutoff)s, MIN(t_dat))                             AS tenure_days,
    COUNT(*)                                                     AS n_items,
    COUNT(DISTINCT t_dat)                                        AS n_visits,
    SUM(price)                                                   AS monetary,
    SUM(t_dat > DATE_SUB(%(cutoff)s, INTERVAL 90 DAY))           AS items_last_90d,
    AVG(sales_channel_id = 2)                                    AS online_share
FROM hm_transactions
WHERE t_dat <= %(cutoff)s
GROUP BY customer_id
"""

_ACTIVE_SQL = """
SELECT DISTINCT customer_id
FROM hm_transactions
WHERE t_dat > %(cutoff)s AND t_dat <= %(end)s
"""


def _read_chunked(sql, conn, params, label):
    chunks = []
    total = 0
    for chunk in pd.read_sql(sql, conn, params=params, chunksize=100000):
        chunks.append(chunk)
        total += len(chunk)
        print(f"  {label}: {total:,} rows...", end="\r")
    print()
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def get_date_range(conn):
    """Return (first_date, last_date) of hm_transactions."""
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(t_dat), MAX(t_dat) FROM hm_transactions")
        first, last = cur.fetchone()
    if first is None:
        raise RuntimeError("hm_transactions is empty")
    return first, last


def get_cutoffs(conn):
    """Return (train_cutoff, test_cutoff, score_date) per the timeline above."""
    first, last = get_date_range(conn)
    h = timedelta(days=HORIZON_DAYS)
    train_cutoff = last - 2 * h
    test_cutoff = last - h
    # Need at least one horizon of history before the train cutoff.
    if train_cutoff - h < first:
        raise RuntimeError(
            f"Not enough history ({first} to {last}) for a {HORIZON_DAYS}-day "
            f"horizon; need at least {3 * HORIZON_DAYS} days of transactions."
        )
    return train_cutoff, test_cutoff, last


def build_features(conn, cutoff):
    """Per-customer features using only transactions on or before `cutoff`."""
    df = _read_chunked(_FEATURE_SQL, conn, {"cutoff": cutoff}, f"features @ {cutoff}")
    df[FEATURES] = df[FEATURES].astype(float).fillna(0)
    return df


def build_labeled(conn, cutoff):
    """Features as of `cutoff` plus churned label from the following horizon."""
    df = build_features(conn, cutoff)
    end = cutoff + timedelta(days=HORIZON_DAYS)
    active = _read_chunked(
        _ACTIVE_SQL, conn, {"cutoff": cutoff, "end": end}, f"active in ({cutoff}, {end}]"
    )
    active_ids = set(active["customer_id"]) if len(active) else set()
    df["churned"] = (~df["customer_id"].isin(active_ids)).astype(int)
    if df["churned"].nunique() < 2:
        raise RuntimeError(f"Only one class present at cutoff {cutoff}; check data.")
    return df


def make_model():
    return RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=50,
        random_state=42,
        n_jobs=-1,
        class_weight="balanced",
    )


def train(df):
    model = make_model()
    model.fit(df[FEATURES], df["churned"])
    return model
