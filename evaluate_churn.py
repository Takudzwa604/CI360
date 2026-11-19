"""
Churn model evaluation (out-of-time) + top at-risk export.

See churn_pipeline.py for the churn definition. This script:
  1. trains on labels from the train cutoff (T - 2H),
  2. evaluates on a later, unseen period (T - H) -- an honest out-of-time test,
  3. compares against a recency-only baseline so you can see what the model adds,
  4. retrains on the most recent labelled period and prints the top at-risk
     customers as of T.

It writes nothing to the database. score_churn.py stores scores in
hm_customers.churn_risk_score, which is what the API reads.
"""
import sys

import pandas as pd
from sklearn.metrics import confusion_matrix, precision_score, recall_score, roc_auc_score

from churn_pipeline import FEATURES, HORIZON_DAYS, build_features, build_labeled, get_cutoffs, train
from db_config import get_connection

try:
    conn = get_connection(read_timeout=600, write_timeout=600, connect_timeout=30)
except Exception as e:
    print(f"Database connection failed: {e}")
    sys.exit(1)

print("=" * 70)
print(f"CHURN PREDICTION MODEL - NO PURCHASE IN NEXT {HORIZON_DAYS} DAYS")
print("=" * 70)

try:
    train_cutoff, test_cutoff, score_date = get_cutoffs(conn)
    print(f"Train cutoff: {train_cutoff} | Test cutoff: {test_cutoff} | Score as of: {score_date}\n")

    print("Building training set...")
    train_df = build_labeled(conn, train_cutoff)
    print("Building out-of-time test set...")
    test_df = build_labeled(conn, test_cutoff)
except Exception as e:
    print(f"Error building datasets: {e}")
    conn.close()
    sys.exit(1)

for name, d in (("Train", train_df), ("Test", test_df)):
    churn_rate = d["churned"].mean()
    print(f"{name}: {len(d):,} customers | churn rate {churn_rate:.1%}")

print("\nTraining Random Forest model...")
model = train(train_df)

y_test = test_df["churned"]
y_proba = model.predict_proba(test_df[FEATURES])[:, 1]
y_pred = (y_proba >= 0.5).astype(int)

precision = precision_score(y_test, y_pred)
recall = recall_score(y_test, y_pred)
auc = roc_auc_score(y_test, y_proba)
baseline_auc = roc_auc_score(y_test, test_df["recency_days"])  # higher recency -> more likely churn
cm = confusion_matrix(y_test, y_pred)

print("\n" + "=" * 70)
print(f"MODEL PERFORMANCE (OUT-OF-TIME TEST, cutoff {test_cutoff})")
print("=" * 70)
print(f"Precision: {precision:.2%}  (of predicted churners, how many did not return)")
print(f"Recall:    {recall:.2%}  (of actual churners, how many we flagged)")
print(f"AUC-ROC:   {auc:.4f}")
print(f"Baseline AUC (recency only): {baseline_auc:.4f}  -> model lift: {auc - baseline_auc:+.4f}")
print("\nConfusion Matrix:")
print(f"  True Negatives:  {cm[0,0]:,}  | False Positives: {cm[0,1]:,}")
print(f"  False Negatives: {cm[1,0]:,}  | True Positives:  {cm[1,1]:,}")

feature_importance = pd.DataFrame(
    {"feature": FEATURES, "importance": model.feature_importances_}
).sort_values("importance", ascending=False)

print("\n" + "=" * 70)
print("FEATURE IMPORTANCE")
print("=" * 70)
for _, row in feature_importance.iterrows():
    print(f"{row['feature']:30s} | {row['importance']:.4f} ({row['importance']*100:.1f}%)")

# Retrain on the most recent fully labelled period, then score as of the last date.
print(f"\nRetraining on cutoff {test_cutoff} and scoring as of {score_date}...")
final_model = train(test_df)
score_df = build_features(conn, score_date)
score_df["churn_risk_score"] = final_model.predict_proba(score_df[FEATURES])[:, 1]

df_at_risk = score_df.nlargest(1000, "churn_risk_score")[
    ["customer_id", "churn_risk_score", "monetary", "recency_days"]
]

print("\n" + "=" * 70)
print("TOP AT-RISK CUSTOMERS (for proactive retention)")
print("=" * 70)
print(df_at_risk.head(10).to_string(index=False))

print("\n" + "=" * 70)
print("AT-RISK CUSTOMER SUMMARY")
print("=" * 70)
print(f"Churn Risk Score Range: {score_df['churn_risk_score'].min():.4f} - {score_df['churn_risk_score'].max():.4f}")
print(f"Average Risk Score (top 1000): {df_at_risk['churn_risk_score'].mean():.4f}")
print(f"Revenue at Risk (top 1000): ${df_at_risk['monetary'].sum():,.2f}")
print(f"Model Accuracy on Out-of-Time Test: {(y_pred == y_test).mean():.2%}")

conn.close()
print("\n" + "=" * 70)
print("✓ CHURN MODEL COMPLETE!")
print("=" * 70)
