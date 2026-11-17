import pandas as pd
import os

# ============================================================================
# CI360 CSV Explorer - Understand data structure and relationships
# ============================================================================

print("=" * 80)
print("CI360 DATA EXPLORATION")
print("=" * 80)

# Define CSV file paths (adjust these to your actual file locations)
csv_files = {
    'transactions': 'transactions.csv',
    'customers': 'customers.csv',
    'articles': 'articles.csv'
}

# Load CSVs
dfs = {}
for name, filepath in csv_files.items():
    try:
        dfs[name] = pd.read_csv(filepath)
        print(f"\n✓ Loaded {name}: {filepath}")
    except FileNotFoundError:
        print(f"\n✗ File not found: {filepath}")
        print(f"  Please update the file path in the script.")
        exit(1)

# ============================================================================
# 1. TRANSACTIONS TABLE
# ============================================================================
print("\n" + "=" * 80)
print("1. HM_TRANSACTIONS (FACT TABLE)")
print("=" * 80)

trans = dfs['transactions']
print(f"\nShape: {trans.shape[0]:,} rows × {trans.shape[1]} columns")
print(f"\nColumn Names:")
for i, col in enumerate(trans.columns, 1):
    dtype = trans[col].dtype
    print(f"  {i:2}. {col:25} ({dtype})")

print(f"\nFirst 5 rows:")
print(trans.head())

print(f"\nData Info:")
print(trans.info())

# ============================================================================
# 2. CUSTOMERS TABLE
# ============================================================================
print("\n" + "=" * 80)
print("2. HM_CUSTOMERS (DIMENSION TABLE)")
print("=" * 80)

cust = dfs['customers']
print(f"\nShape: {cust.shape[0]:,} rows × {cust.shape[1]} columns")
print(f"\nColumn Names:")
for i, col in enumerate(cust.columns, 1):
    dtype = cust[col].dtype
    print(f"  {i:2}. {col:25} ({dtype})")

print(f"\nFirst 5 rows:")
print(cust.head())

# ============================================================================
# 3. ARTICLES TABLE
# ============================================================================
print("\n" + "=" * 80)
print("3. HM_ARTICLES (DIMENSION TABLE)")
print("=" * 80)

art = dfs['articles']
print(f"\nShape: {art.shape[0]:,} rows × {art.shape[1]} columns")
print(f"\nColumn Names:")
for i, col in enumerate(art.columns, 1):
    dtype = art[col].dtype
    print(f"  {i:2}. {col:25} ({dtype})")

print(f"\nFirst 5 rows:")
print(art.head())

# ============================================================================
# 4. RELATIONSHIPS & JOINS
# ============================================================================
print("\n" + "=" * 80)
print("4. TABLE RELATIONSHIPS (FOREIGN KEYS)")
print("=" * 80)

# Find common columns (potential join keys)
trans_cols = set(trans.columns)
cust_cols = set(cust.columns)
art_cols = set(art.columns)

print(f"\n→ TRANSACTIONS ↔ CUSTOMERS:")
common_trans_cust = trans_cols & cust_cols
print(f"  Common columns: {common_trans_cust}")

print(f"\n→ TRANSACTIONS ↔ ARTICLES:")
common_trans_art = trans_cols & art_cols
print(f"  Common columns: {common_trans_art}")

# ============================================================================
# 5. JOIN ANALYSIS
# ============================================================================
print("\n" + "=" * 80)
print("5. JOIN VALIDATION")
print("=" * 80)

# Transactions → Customers join
if 'customer_id' in trans.columns and 'customer_id' in cust.columns:
    print(f"\n▶ JOIN: hm_transactions → hm_customers (ON customer_id)")

    trans_customer_ids = set(trans['customer_id'].unique())
    cust_customer_ids = set(cust['customer_id'].unique())

    print(f"  - Transactions: {len(trans_customer_ids):,} unique customer_id values")
    print(f"  - Customers:    {len(cust_customer_ids):,} unique customer_id values")

    # Check referential integrity
    orphaned = trans_customer_ids - cust_customer_ids
    if orphaned:
        print(f"  ⚠ WARNING: {len(orphaned)} orphaned customer_ids in transactions")
        print(f"    Examples: {list(orphaned)[:5]}")
    else:
        print(f"  ✓ Referential integrity OK: all transaction customers exist in dimension")

    # Show sample join
    sample_cust_id = trans['customer_id'].iloc[0]
    print(f"\n  Sample JOIN on customer_id = {sample_cust_id}:")
    trans_sample = trans[trans['customer_id'] == sample_cust_id].head(3)
    cust_sample = cust[cust['customer_id'] == sample_cust_id]

    print(f"\n  Transaction rows for this customer:")
    print(trans_sample)
    print(f"\n  Customer dimension row:")
    print(cust_sample)

# Transactions → Articles join
if 'article_id' in trans.columns and 'article_id' in art.columns:
    print(f"\n▶ JOIN: hm_transactions → hm_articles (ON article_id)")

    trans_article_ids = set(trans['article_id'].unique())
    art_article_ids = set(art['article_id'].unique())

    print(f"  - Transactions: {len(trans_article_ids):,} unique article_id values")
    print(f"  - Articles:     {len(art_article_ids):,} unique article_id values")

    # Check referential integrity
    orphaned = trans_article_ids - art_article_ids
    if orphaned:
        print(f"  ⚠ WARNING: {len(orphaned)} orphaned article_ids in transactions")
        print(f"    Examples: {list(orphaned)[:5]}")
    else:
        print(f"  ✓ Referential integrity OK: all transaction articles exist in dimension")

    # Show sample join
    sample_art_id = trans['article_id'].iloc[0]
    print(f"\n  Sample JOIN on article_id = {sample_art_id}:")
    trans_sample = trans[trans['article_id'] == sample_art_id].head(3)
    art_sample = art[art['article_id'] == sample_art_id]

    print(f"\n  Transaction rows for this article:")
    print(trans_sample)
    print(f"\n  Article dimension row:")
    print(art_sample)

# ============================================================================
# 6. DATA QUALITY CHECKS
# ============================================================================
print("\n" + "=" * 80)
print("6. DATA QUALITY CHECKS")
print("=" * 80)

print(f"\n→ TRANSACTIONS:")
print(f"  - Null values per column:")
for col in trans.columns:
    null_count = trans[col].isnull().sum()
    if null_count > 0:
        print(f"    {col:20} {null_count:8,} nulls")

print(f"\n→ CUSTOMERS:")
print(f"  - Null values per column:")
for col in cust.columns:
    null_count = cust[col].isnull().sum()
    if null_count > 0:
        print(f"    {col:20} {null_count:8,} nulls")

print(f"\n→ ARTICLES:")
print(f"  - Null values per column:")
for col in art.columns:
    null_count = art[col].isnull().sum()
    if null_count > 0:
        print(f"    {col:20} {null_count:8,} nulls")

# ============================================================================
# 7. SUMMARY
# ============================================================================
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"""
Star Schema Structure:

  hm_transactions (FACT)
    {trans.shape[0]:,} rows × {trans.shape[1]} columns
    ↓ Foreign Key: customer_id
    ↓ Foreign Key: article_id

  hm_customers (DIMENSION)
    {cust.shape[0]:,} rows × {cust.shape[1]} columns

  hm_articles (DIMENSION)
    {art.shape[0]:,} rows × {art.shape[1]} columns

Join Grain:
  One row per (customer_id, article_id, date) combination
  (or per transaction event, depending on source data definition)
""")

print("=" * 80)
print("END OF EXPLORATION")
print("=" * 80)
