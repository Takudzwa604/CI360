from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import contextmanager
import pymysql
import logging
import os
from dotenv import load_dotenv
from dbutils.pooled_db import PooledDB
import plotly.express as px
import pandas as pd
import html
from typing import Literal, Optional
from urllib.parse import urlencode

# -----------------------------------
# Load environment variables
# -----------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

# -----------------------------------
# Configure Logging
# -----------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("ci360")

# -----------------------------------
# Initialize FastAPI
# -----------------------------------
app = FastAPI(
    title="CI360 - Customer Intelligence Platform",
    description="Revenue analytics, customer segmentation, and churn prediction",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # The API uses no cookies or auth headers, so credentials are not needed.
    # Browsers reject wildcard origins combined with credentials anyway.
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

logger.info("Starting CI360 FastAPI application")


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# -----------------------------------
# Database Connection Pool
# -----------------------------------
# Each request borrows its own connection from a pool and returns it when done.
# This prevents concurrent requests from interfering with each other.
try:
    logger.info("Creating MySQL connection pool...")
    pool = PooledDB(
        creator=pymysql,
        maxconnections=10,
        mincached=2,
        maxcached=5,
        blocking=True,
        ping=1,  # verify/reconnect a pooled connection before handing it out
        host=_require_env("DB_HOST"),
        port=int(_require_env("DB_PORT")),
        user=_require_env("DB_USER"),
        passwd=_require_env("DB_PASSWORD"),
        db=_require_env("DB_NAME"),
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    logger.info("MySQL connection pool ready")
except Exception as e:
    logger.error(f"Database pool creation failed: {e}")
    raise


@contextmanager
def get_cursor():
    """Borrow a pooled connection for one request and always return it to the pool."""
    conn = pool.connection()
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()  # returns the connection to the pool; does not actually close it

# -----------------------------------
# Health Check
# -----------------------------------


@app.get("/health")
def health_check():
    """Health check endpoint"""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "healthy", "service": "CI360"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

# -----------------------------------
# Core Analytics Endpoints
# -----------------------------------


@app.get("/api/churn-risk")
def get_churn_risk(limit: int = Query(1000, ge=1, le=10000)):
    """Get top at-risk customers by churn probability"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT customer_id, churn_risk_score, customer_lifetime_value, days_since_last_purchase
                FROM hm_customers
                WHERE churn_risk_score IS NOT NULL
                ORDER BY churn_risk_score DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()
    except Exception as e:
        logger.error(f"/api/churn-risk failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load churn risk data.")


@app.get("/api/churn-risk/{customer_id}")
def get_customer_churn_risk(customer_id: str):
    """Get churn risk score for a specific customer"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT customer_id, churn_risk_score, customer_lifetime_value,
                       days_since_last_purchase, total_transactions
                FROM hm_customers
                WHERE customer_id = %s
            """, (customer_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"/api/churn-risk/{customer_id} failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load customer churn risk.")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Customer '{customer_id}' not found.")

    # Add risk level classification
    score = row.get('churn_risk_score')
    if score is None:
        # Customers with no transactions are never scored by score_churn.py
        row['risk_level'] = "NOT SCORED"
    elif score >= 0.8:
        row['risk_level'] = "VERY HIGH"
    elif score >= 0.6:
        row['risk_level'] = "HIGH"
    elif score >= 0.4:
        row['risk_level'] = "MEDIUM"
    elif score >= 0.2:
        row['risk_level'] = "LOW"
    else:
        row['risk_level'] = "VERY LOW"

    return row


@app.get("/api/top-customers")
def top_customers():
    """Get top 10 customers by spend (from the crm_customers table)"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT customer_id, total_transactions, customer_lifetime_value as total_spend
                FROM crm_customers
                ORDER BY customer_lifetime_value DESC
                LIMIT 10
            """)
            return cur.fetchall()
    except Exception as e:
        logger.error(f"/api/top-customers failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load top customers.")


@app.get("/api/top-articles")
def top_articles():
    """Get top 10 articles by revenue (from pre-aggregated article_summary table)"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT article_id, units_sold, unique_customers, revenue
                FROM article_summary
                ORDER BY revenue DESC
                LIMIT 10
            """)
            return cur.fetchall()
    except Exception as e:
        logger.error(f"/api/top-articles failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load top articles.")


@app.get("/api/revenue/monthly")
def monthly_revenue():
    """Get monthly revenue trend (from pre-aggregated monthly_revenue table)"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT month, revenue
                FROM monthly_revenue
                ORDER BY month ASC
            """)
            return cur.fetchall()
    except Exception as e:
        logger.error(f"/api/revenue/monthly failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load monthly revenue.")


@app.get("/api/revenue/summary")
def revenue_summary():
    """Get overall revenue summary statistics"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT
                    SUM(revenue) as total_revenue,
                    COUNT(*) as total_months,
                    AVG(revenue) as avg_monthly_revenue
                FROM monthly_revenue
            """)
            row = cur.fetchone()

            # Get additional stats from article_summary
            cur.execute("""
                SELECT
                    SUM(units_sold) as total_units_sold,
                    COUNT(*) as article_count
                FROM article_summary
            """)
            article_row = cur.fetchone()

            # Summing article_summary.unique_customers would count a customer once per
            # distinct article bought; crm_customers has one row per purchasing customer.
            cur.execute("SELECT COUNT(*) as unique_customers FROM crm_customers")
            customer_row = cur.fetchone()
    except Exception as e:
        logger.error(f"/api/revenue/summary failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load revenue summary.")

    return {
        "total_revenue": float(row.get('total_revenue') or 0),
        "total_months": int(row.get('total_months') or 0),
        "avg_monthly_revenue": float(row.get('avg_monthly_revenue') or 0),
        "total_unique_customers": int(customer_row.get('unique_customers') or 0),
        "total_units_sold": int(article_row.get('total_units_sold') or 0),
        "total_articles": int(article_row.get('article_count') or 0)
    }


@app.get("/api/customer-segments")
def customer_segments_summary():
    """Get customer segment distribution and statistics"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT segment, COUNT(*) as customer_count,
                       ROUND(AVG(customer_lifetime_value), 2) as avg_ltv,
                       ROUND(SUM(customer_lifetime_value), 2) as total_ltv,
                       ROUND(AVG(days_since_last_purchase), 0) as avg_days_since_purchase,
                       ROUND(AVG(churn_risk_score), 4) as avg_churn_risk,
                       ROUND(SUM(revenue_at_risk), 4) as revenue_at_risk
                FROM crm_customers
                GROUP BY segment
                ORDER BY total_ltv DESC
            """)
            return cur.fetchall()
    except Exception as e:
        logger.error(f"/api/customer-segments failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load customer segments.")


@app.get("/customer/{customer_id}")
def customer_360(customer_id: str):
    """Get complete customer profile from crm_customers"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT customer_id, segment, age, cohort_month,
                       first_purchase_date, last_purchase_date, days_since_last_purchase,
                       total_transactions, customer_lifetime_value, expected_90d_spend,
                       churn_risk_score, risk_level, revenue_at_risk
                FROM crm_customers
                WHERE customer_id = %s
            """, (customer_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"/customer/{customer_id} failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load customer profile.")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Customer '{customer_id}' not found.")
    return row


@app.get("/article/{article_id}")
def article_details(article_id: int):
    """Get article/product details from article_summary"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT article_id, units_sold, unique_customers, revenue
                FROM article_summary
                WHERE article_id = %s
            """, (article_id,))
            row = cur.fetchone()
    except Exception as e:
        logger.error(f"/article/{article_id} failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load article details.")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Article '{article_id}' not found.")
    return row


# -----------------------------------
# CRM Worklist
# -----------------------------------
# Customers ranked by revenue at risk (churn probability x expected 90-day spend),
# filterable by segment, risk level and lifetime value. Reads crm_customers,
# built by build_crm_customers.py.

RiskLevel = Literal["VERY HIGH", "HIGH", "MEDIUM", "LOW", "VERY LOW"]
WorklistSort = Literal["revenue_at_risk", "churn_risk_score", "customer_lifetime_value"]

# Whitelisted ORDER BY clauses (user input is never interpolated into SQL)
_WORKLIST_ORDER = {
    "revenue_at_risk": "revenue_at_risk DESC, customer_id",
    "churn_risk_score": "churn_risk_score DESC, customer_id",
    "customer_lifetime_value": "customer_lifetime_value DESC, customer_id",
}


def _query_worklist(segment, risk_level, min_ltv, sort, limit, offset):
    where = ["churn_risk_score IS NOT NULL"]
    params = []
    if segment:
        where.append("segment = %s")
        params.append(segment)
    if risk_level:
        where.append("risk_level = %s")
        params.append(risk_level)
    if min_ltv:
        where.append("customer_lifetime_value >= %s")
        params.append(min_ltv)
    where_sql = " AND ".join(where)

    with get_cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) AS total, SUM(revenue_at_risk) AS total_revenue_at_risk
            FROM crm_customers WHERE {where_sql}
        """, params)
        summary = cur.fetchone()
        cur.execute(f"""
            SELECT customer_id, segment, risk_level, churn_risk_score,
                   customer_lifetime_value, expected_90d_spend, revenue_at_risk,
                   days_since_last_purchase, last_purchase_date, total_transactions
            FROM crm_customers
            WHERE {where_sql}
            ORDER BY {_WORKLIST_ORDER[sort]}
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        items = cur.fetchall()

    return {
        "total": int(summary["total"] or 0),
        "total_revenue_at_risk": float(summary["total_revenue_at_risk"] or 0),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def _worklist_segments():
    with get_cursor() as cur:
        cur.execute("SELECT DISTINCT segment FROM crm_customers WHERE segment IS NOT NULL ORDER BY segment")
        return [r["segment"] for r in cur.fetchall()]


@app.get("/api/worklist")
def get_worklist(
    segment: Optional[str] = Query(None, max_length=20),
    risk_level: Optional[RiskLevel] = None,
    min_ltv: float = Query(0, ge=0),
    sort: WorklistSort = "revenue_at_risk",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100000),
):
    """At-risk customer worklist: who to contact first, ranked by revenue at risk."""
    try:
        return _query_worklist(segment, risk_level, min_ltv, sort, limit, offset)
    except Exception as e:
        logger.error(f"/api/worklist failed: {e}")
        raise HTTPException(status_code=500, detail="Could not load worklist.")


@app.get("/")
def api_index():
    """API documentation and available endpoints"""
    return {
        "name": "CI360 - Customer Intelligence Platform",
        "version": "1.0.0",
        "description": "Revenue analytics, customer segmentation, and churn prediction",
        "documentation": "/docs",
        "endpoints": {
            "health": "/health",
            "churn_risk": "/api/churn-risk",
            "churn_risk_customer": "/api/churn-risk/{customer_id}",
            "top_customers": "/api/top-customers",
            "top_articles": "/api/top-articles",
            "revenue_monthly": "/api/revenue/monthly",
            "revenue_summary": "/api/revenue/summary",
            "customer_segments": "/api/customer-segments",
            "customer_profile": "/customer/{customer_id}",
            "worklist": "/api/worklist",
            "worklist_page": "/viz/worklist",
            "article_details": "/article/{article_id}",
            "dashboard": "/dashboard"
        }
    }

# -----------------------------------
# Visualization Helpers
# -----------------------------------


def safe_plot(fig):
    """Safely render a Plotly figure to HTML"""
    if fig is None:
        return "<p>No data available.</p>"
    return fig.to_html(full_html=False, include_plotlyjs="cdn")

# -----------------------------------
# Visualization Endpoints
# -----------------------------------


@app.get("/viz/monthly-revenue", response_class=HTMLResponse)
def viz_monthly_revenue():
    """Visualize monthly revenue trend (from pre-aggregated table)"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT month, revenue
                FROM monthly_revenue
                ORDER BY month ASC
            """)
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"/viz/monthly-revenue failed: {e}")
        return "<h3>Could not load monthly revenue data.</h3>"

    if not rows:
        return "<h3>No monthly revenue data available.</h3>"

    fig = px.line(rows, x="month", y="revenue",
                  title="Monthly Revenue Trend",
                  labels={"month": "Month", "revenue": "Revenue ($)"})
    return safe_plot(fig)


@app.get("/viz/top-articles", response_class=HTMLResponse)
def viz_top_articles():
    """Visualize top articles by units sold (from pre-aggregated table)"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT article_id, units_sold, revenue
                FROM article_summary
                ORDER BY units_sold DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"/viz/top-articles failed: {e}")
        return "<h3>Could not load article data.</h3>"

    if not rows:
        return "<h3>No article data available.</h3>"

    # Convert article_id to string for better display
    df = pd.DataFrame(rows)
    df['article_id'] = df['article_id'].astype(str)

    fig = px.bar(df, x="units_sold", y="article_id",
                 orientation="h", title="Top 10 Articles by Units Sold",
                 labels={"units_sold": "Units Sold", "article_id": "Article ID"})
    return safe_plot(fig)


@app.get("/viz/top-customers", response_class=HTMLResponse)
def viz_top_customers():
    """Visualize top customers by spend (from pre-aggregated table)"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT customer_id, customer_lifetime_value as spend
                FROM crm_customers
                ORDER BY customer_lifetime_value DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"/viz/top-customers failed: {e}")
        return "<h3>Could not load customer data.</h3>"

    if not rows:
        return "<h3>No customer data available.</h3>"

    labels = [f"#{i + 1}" for i in range(len(rows))]
    fig = px.bar(rows, x=labels, y="spend", hover_name="customer_id",
                 title="Top 10 Customers by Lifetime Value",
                 labels={"x": "Rank", "spend": "Lifetime Value ($)"})
    return safe_plot(fig)


@app.get("/viz/churn-risk", response_class=HTMLResponse)
def viz_churn_risk():
    """Visualize churn risk distribution"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT
                    CASE
                        WHEN churn_risk_score >= 0.8 THEN '0.8-1.0 (VERY HIGH)'
                        WHEN churn_risk_score >= 0.6 THEN '0.6-0.8 (HIGH)'
                        WHEN churn_risk_score >= 0.4 THEN '0.4-0.6 (MEDIUM)'
                        WHEN churn_risk_score >= 0.2 THEN '0.2-0.4 (LOW)'
                        ELSE '0.0-0.2 (VERY LOW)'
                    END as risk_bucket,
                    COUNT(*) as customer_count
                FROM hm_customers
                WHERE churn_risk_score IS NOT NULL
                GROUP BY risk_bucket
                ORDER BY MIN(churn_risk_score) DESC
            """)
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"/viz/churn-risk failed: {e}")
        return "<h3>Could not load churn risk data.</h3>"

    if not rows:
        return "<h3>No churn risk data available.</h3>"

    fig = px.bar(rows, x="risk_bucket", y="customer_count",
                 title="Churn Risk Distribution",
                 labels={"risk_bucket": "Risk Score", "customer_count": "Customer Count"})
    return safe_plot(fig)


@app.get("/viz/customer-segments", response_class=HTMLResponse)
def viz_customer_segments():
    """Visualize customer segment distribution"""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT segment, COUNT(*) as customer_count,
                       ROUND(SUM(customer_lifetime_value), 2) as total_ltv
                FROM crm_customers
                GROUP BY segment
                ORDER BY total_ltv DESC
            """)
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"/viz/customer-segments failed: {e}")
        return "<h3>Could not load customer segment data.</h3>"

    if not rows:
        return "<h3>No customer segment data available.</h3>"

    fig = px.bar(rows, x="segment", y="customer_count",
                 title="Customer Distribution by Segment",
                 labels={"segment": "Segment", "customer_count": "Number of Customers"})
    return safe_plot(fig)

@app.get("/viz/worklist", response_class=HTMLResponse)
def viz_worklist(
    segment: Optional[str] = Query(None, max_length=20),
    risk_level: Optional[str] = None,
    min_ltv: Optional[str] = None,
    sort: str = "revenue_at_risk",
    offset: int = Query(0, ge=0, le=100000),
):
    """At-risk customer worklist page with filters and paging."""
    page_size = 50
    risk_levels = ["VERY HIGH", "HIGH", "MEDIUM", "LOW", "VERY LOW"]
    # The HTML form submits empty strings for "All"; normalise instead of rejecting.
    segment = segment or None
    risk_level = risk_level if risk_level in risk_levels else None
    sort = sort if sort in _WORKLIST_ORDER else "revenue_at_risk"
    try:
        min_ltv_value = max(float(min_ltv), 0.0) if min_ltv else 0.0
    except ValueError:
        min_ltv_value = 0.0

    try:
        data = _query_worklist(segment, risk_level, min_ltv_value, sort, page_size, offset)
        segments = _worklist_segments()
    except Exception as e:
        logger.error(f"/viz/worklist failed: {e}")
        return "<h3>Could not load the worklist. Has build_crm_customers.py been run?</h3>"

    esc = html.escape

    def options(values, selected, blank):
        out = [f'<option value="">{esc(blank)}</option>']
        for v in values:
            sel = " selected" if v == selected else ""
            out.append(f'<option value="{esc(v)}"{sel}>{esc(v)}</option>')
        return "".join(out)

    sort_labels = {
        "revenue_at_risk": "Revenue at risk",
        "churn_risk_score": "Churn risk",
        "customer_lifetime_value": "Lifetime value",
    }
    sort_opts = "".join(
        f'<option value="{k}"{" selected" if k == sort else ""}>{v}</option>'
        for k, v in sort_labels.items()
    )

    def fmt(v, digits=4):
        return "-" if v is None else f"{float(v):,.{digits}f}"

    rows_html = "".join(
        f"""<tr>
            <td><a href="/customer/{esc(r['customer_id'])}" target="_blank" title="{esc(r['customer_id'])}">{esc(r['customer_id'][:12])}&hellip;</a></td>
            <td>{esc(r['segment'] or '-')}</td>
            <td><span class="risk risk-{esc(r['risk_level'].replace(' ', '-').lower())}">{esc(r['risk_level'])}</span></td>
            <td class="num">{fmt(r['churn_risk_score'], 3)}</td>
            <td class="num">{fmt(r['customer_lifetime_value'], 2)}</td>
            <td class="num">{fmt(r['expected_90d_spend'])}</td>
            <td class="num"><b>{fmt(r['revenue_at_risk'])}</b></td>
            <td class="num">{r['days_since_last_purchase'] if r['days_since_last_purchase'] is not None else '-'}</td>
            <td class="num">{r['total_transactions'] or 0}</td>
        </tr>"""
        for r in data["items"]
    ) or '<tr><td colspan="9">No customers match these filters.</td></tr>'

    base = {k: v for k, v in {"segment": segment, "risk_level": risk_level,
                               "min_ltv": min_ltv_value or None, "sort": sort}.items() if v}
    prev_link = (f'<a href="?{esc(urlencode({**base, "offset": max(offset - page_size, 0)}))}">&larr; Previous</a>'
                 if offset > 0 else "")
    next_link = (f'<a href="?{esc(urlencode({**base, "offset": offset + page_size}))}">Next &rarr;</a>'
                 if offset + page_size < data["total"] else "")
    first, last = (offset + 1, min(offset + page_size, data["total"])) if data["total"] else (0, 0)

    return f"""
    <html><head><meta charset="utf-8"><style>
        body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; margin: 20px; color: #111827; background: #fff; }}
        h2 {{ margin: 0 0 4px; }}
        .sub {{ color: #6b7280; margin-bottom: 16px; font-size: 14px; }}
        form {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: end; margin-bottom: 16px; }}
        label {{ font-size: 12px; color: #374151; display: flex; flex-direction: column; gap: 4px; }}
        select, input {{ padding: 6px 8px; border: 1px solid #d1d5db; border-radius: 6px; }}
        button {{ padding: 7px 14px; background: #3b82f6; color: white; border: 0; border-radius: 6px; cursor: pointer; }}
        .stats {{ display: flex; gap: 24px; margin-bottom: 12px; font-size: 14px; color: #374151; }}
        .stats b {{ font-size: 18px; display: block; color: #111827; }}
        .tablewrap {{ overflow-x: auto; }}
        table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
        th, td {{ padding: 8px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; white-space: nowrap; }}
        th {{ background: #f9fafb; position: sticky; top: 0; }}
        td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
        .risk {{ padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
        .risk-very-high {{ background: #fee2e2; color: #991b1b; }}
        .risk-high {{ background: #ffedd5; color: #9a3412; }}
        .risk-medium {{ background: #fef9c3; color: #854d0e; }}
        .risk-low, .risk-very-low {{ background: #dcfce7; color: #166534; }}
        .pager {{ display: flex; gap: 16px; margin-top: 12px; font-size: 14px; }}
        a {{ color: #2563eb; }}
    </style></head><body>
        <h2>At-Risk Customer Worklist</h2>
        <div class="sub">Who to contact first. Revenue at risk = churn probability &times; expected 90-day spend.
            Money values are in the dataset's scaled price units.</div>
        <form method="get">
            <label>Segment<select name="segment">{options(segments, segment, "All segments")}</select></label>
            <label>Risk level<select name="risk_level">{options(risk_levels, risk_level, "All levels")}</select></label>
            <label>Min lifetime value<input type="number" name="min_ltv" step="0.01" min="0" value="{min_ltv_value or ''}"></label>
            <label>Sort by<select name="sort">{sort_opts}</select></label>
            <button type="submit">Apply</button>
        </form>
        <div class="stats">
            <div><b>{data['total']:,}</b>customers match</div>
            <div><b>{data['total_revenue_at_risk']:,.2f}</b>total revenue at risk</div>
        </div>
        <div class="tablewrap"><table>
            <tr><th>Customer</th><th>Segment</th><th>Risk</th><th class="num">Churn prob.</th>
                <th class="num">Lifetime value</th><th class="num">Exp. 90-day spend</th>
                <th class="num">Revenue at risk</th><th class="num">Days since purchase</th><th class="num">Items bought</th></tr>
            {rows_html}
        </table></div>
        <div class="pager">{prev_link}<span>Showing {first:,}&ndash;{last:,} of {data['total']:,}</span>{next_link}</div>
    </body></html>
    """

# -----------------------------------
# Dashboard
# -----------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Interactive analytics dashboard"""
    html = """
    <html>
    <head>
        <title>CI360 Dashboard</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                   background-color: #f3f4f6; }
            .container { display: flex; height: 100vh; }
            .sidebar { width: 250px; background-color: #1f2937; color: white; padding: 30px 20px; box-sizing: border-box;
                      overflow-y: auto; box-shadow: 2px 0 8px rgba(0,0,0,0.1); }
            .sidebar h1 { font-size: 24px; margin-bottom: 30px; font-weight: 700; }
            .sidebar a { display: block; color: #d1d5db; text-decoration: none; padding: 12px 15px;
                        margin: 8px 0; border-radius: 6px; transition: all 0.2s; }
            .sidebar a:hover { background-color: #374151; color: #f3f4f6; }
            .sidebar a.active { background-color: #3b82f6; color: white; }
            .content { flex: 1; padding: 40px; overflow-y: auto; }
            .content h1 { margin-bottom: 20px; color: #111827; font-size: 28px; }
            .content p { color: #6b7280; margin-bottom: 20px; }
            iframe { border: none; width: 100%; height: 600px; background: white;
                    border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="sidebar">
                <h1>CI360</h1>
                <a href="#" onclick="loadChart('/viz/monthly-revenue'); return false;" class="nav-link">📊 Revenue Trend</a>
                <a href="#" onclick="loadChart('/viz/top-articles'); return false;" class="nav-link">🛍️ Top Articles</a>
                <a href="#" onclick="loadChart('/viz/top-customers'); return false;" class="nav-link">👥 Top Customers</a>
                <a href="#" onclick="loadChart('/viz/customer-segments'); return false;" class="nav-link">📈 Customer Segments</a>
                <a href="#" onclick="loadChart('/viz/churn-risk'); return false;" class="nav-link">⚠️ Churn Risk</a>
                <a href="#" onclick="loadChart('/viz/worklist'); return false;" class="nav-link">📋 At-Risk Worklist</a>
                <a href="/docs" target="_blank" class="nav-link">📖 API Docs</a>
            </div>
            <div class="content">
                <h1>Customer Intelligence Dashboard</h1>
                <p>Select a metric from the sidebar to view analytics</p>
                <iframe id="mainframe" name="mainframe" src="/viz/monthly-revenue"></iframe>
            </div>
        </div>
        <script>
            function loadChart(url) {
                document.getElementById('mainframe').src = url;
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
