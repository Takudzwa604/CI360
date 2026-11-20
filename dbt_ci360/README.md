# CI360 dbt project

The CI360 analytics layer, built as a [dbt](https://www.getdbt.com/) project on DuckDB:
staging models for the data cleaning, marts for the metrics and segments, and dbt tests
that validate every build.

This is the build step of CI360. It reads the CSVs from the project root into
`ci360_dbt.duckdb`; `../push_metrics.py` then copies the tested results to MySQL,
and refuses to unless the last `dbt build` passed every test.

## Lineage

```
CSVs (sources)          staging                 marts
transactions.csv  ──>  stg_transactions  ──┬──> article_summary ──┐
customers.csv     ──>  stg_customers    ──┤──> monthly_revenue ──┤
articles.csv      ──>  stg_articles     ──┘──> customer_metrics ─┼──> exposure: CI360 API
                                                   └─> customer_segments ┘
```

`dbt docs generate && dbt docs serve` shows the interactive version.

| Layer | Model | What it does |
|---|---|---|
| Staging | `stg_transactions` | Typed on read; price as DECIMAL(10,5) to match MySQL; duplicates kept (no quantity column) |
| | `stg_customers` | Blank / "NULL" / non-numeric ages and postal codes become NULL; placeholder postal code flagged |
| | `stg_articles` | Leading-zero `article_id` cast to BIGINT; empty descriptions become NULL |
| Marts | `article_summary` | Units, unique buyers and revenue per product |
| | `monthly_revenue` | Revenue per month |
| | `customer_metrics` | Lifetime value, counts, first/last purchase, recency, tenure, cohort |
| | `customer_segments` | Value + recency segment and expected 90-day spend per purchasing customer |

## Tests

`dbt build` runs every model and then its tests; a failing test stops downstream models.

- **Generic:** `unique` and `not_null` on every key; `relationships` from all 31.8M
  transactions to customers and articles (no orphans); `accepted_values` on sales
  channel and segment.
- **Custom** (`tests/`):
  - `assert_transactions_match_source`: row count equals transactions.csv (31,788,324)
  - `assert_revenue_reconciles`: transactions, article summary and monthly revenue agree exactly
  - `assert_every_purchaser_segmented`: every buyer has exactly one segment
  - `assert_no_future_purchases`: no negative recency or tenure

## Run it

```bash
cd dbt_ci360
py -3.13 -m venv .venv                # dbt does not support Python 3.14 yet
.venv\Scripts\activate
pip install dbt-duckdb
dbt build                             # models + tests, about a minute
dbt docs generate && dbt docs serve   # lineage graph (run dbt build again before pushing:
                                      # docs generate overwrites the results push checks)
```

## What stays outside dbt

- **Raw load into MySQL:** `resume_transactions_load.py` (row-by-row, resumable).
- **Push to MySQL:** `push_metrics.py` (checks `target/run_results.json` first).
- **Churn model:** `churn_pipeline.py`, `score_churn.py` (Python model training, not SQL).
- **`crm_customers`:** adds churn scores and revenue at risk to the pushed segments in MySQL (`build_crm_customers.py`).
