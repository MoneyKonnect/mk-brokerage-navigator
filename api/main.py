"""
MK Brokerage Navigator — FastAPI Backend
Endpoints for Dashboard, Reconciliation, AMC Intelligence,
Scheme Intelligence, Investor Intelligence, Rate Repository, Search
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="MK Brokerage Navigator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def q(sql, params=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def q1(sql, params=None):
    rows = q(sql, params)
    return rows[0] if rows else {}

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/")
def health():
    return {"status": "ok", "service": "MK Brokerage Navigator API"}

# ── Utility: available months ─────────────────────────────────────────────────
@app.get("/months")
def get_months():
    rows = q("SELECT DISTINCT month FROM amc_monthly ORDER BY month DESC")
    return [r["month"] for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 1 — DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/dashboard/kpis")
def dashboard_kpis(month: Optional[str] = None):
    """Top-level KPIs for the dashboard."""
    if not month:
        month = q1("SELECT MAX(month) as m FROM amc_monthly")["m"]

    kpis = q1("""
        SELECT
            ROUND(SUM(brokerage)::numeric, 2)            AS actual_brokerage,
            COUNT(DISTINCT amc_code)                      AS active_amcs,
            COUNT(DISTINCT registrar)                     AS registrars,
            ROUND(AVG(avg_rate)::numeric, 4)              AS avg_rate
        FROM amc_monthly
        WHERE month = %s
    """, [month])

    committed_row = q1("""
        SELECT
            ROUND(SUM(brokerage_amt)::numeric, 2) AS verified_actual,
            ROUND(SUM(brokerage_amt + shortfall_rupees)::numeric, 2) AS expected_brokerage,
            ROUND(SUM(shortfall_rupees)::numeric, 2) AS total_shortfall
        FROM reconciliation_verified_scheme_month
        WHERE month = %s
    """, [month])

    actual = float(committed_row.get("verified_actual") or 0)
    expected = float(committed_row.get("expected_brokerage") or actual)
    gap = float(committed_row.get("total_shortfall") or 0)
    gap_pct = round((gap / expected * 100) if expected else 0, 2)

    schemes = q1("SELECT COUNT(DISTINCT scheme_code) AS c FROM scheme_monthly WHERE month = %s", [month])
    investors = q1("SELECT COUNT(DISTINCT folio_no) AS c FROM investor_scheme_monthly WHERE month = %s", [month])

    ytd = q1("SELECT ROUND(SUM(brokerage)::numeric,2) AS ytd FROM amc_monthly WHERE month >= '2025-04'")

    return {
        "month": month,
        "actual_brokerage": actual,
        "expected_brokerage": expected,
        "gap_inr": gap,
        "gap_pct": gap_pct,
        "ytd_brokerage": float(ytd.get("ytd") or 0),
        "active_amcs": kpis.get("active_amcs"),
        "active_schemes": schemes.get("c"),
        "active_investors": investors.get("c"),
        "avg_rate": float(kpis.get("avg_rate") or 0),
    }

@app.get("/dashboard/monthly-trend")
def dashboard_monthly_trend():
    """11-month brokerage trend, split by registrar."""
    return q("""
        SELECT month,
               registrar,
               ROUND(SUM(brokerage)::numeric, 2) AS brokerage
        FROM amc_monthly
        WHERE month >= '2025-04'
        GROUP BY month, registrar
        ORDER BY month, registrar
    """)

@app.get("/dashboard/top-amcs")
def dashboard_top_amcs(month: Optional[str] = None, limit: int = 5):
    if not month:
        month = q1("SELECT MAX(month) as m FROM amc_monthly")["m"]
    return q("""
        SELECT amc_code, amc_name, registrar,
               ROUND(SUM(brokerage)::numeric, 2) AS brokerage,
               ROUND(AVG(avg_rate)::numeric, 4)  AS avg_rate
        FROM amc_monthly WHERE month = %s
        GROUP BY amc_code, amc_name, registrar
        ORDER BY brokerage DESC LIMIT %s
    """, [month, limit])

@app.get("/dashboard/top-schemes")
def dashboard_top_schemes(month: Optional[str] = None, limit: int = 5):
    if not month:
        month = q1("SELECT MAX(month) as m FROM scheme_monthly")["m"]
    return q("""
        SELECT scheme_code,
               COALESCE(NULLIF(scheme_name, ''), scheme_code || ' (' || amc_name || ')') AS scheme_name,
               amc_name,
               ROUND(SUM(brokerage)::numeric, 2)      AS brokerage,
               ROUND(AVG(weighted_rate)::numeric, 4)  AS avg_rate
        FROM scheme_monthly WHERE month = %s
        GROUP BY scheme_code, scheme_name, amc_name
        ORDER BY brokerage DESC LIMIT %s
    """, [month, limit])

@app.get("/dashboard/top-investors")
def dashboard_top_investors(month: Optional[str] = None, limit: int = 5):
    if not month:
        month = q1("SELECT MAX(month) as m FROM investor_scheme_monthly")["m"]
    return q("""
        SELECT folio_no, investor_name, investor_pan,
               ROUND(SUM(brokerage)::numeric, 2) AS brokerage
        FROM investor_scheme_monthly WHERE month = %s
        GROUP BY folio_no, investor_name, investor_pan
        ORDER BY brokerage DESC LIMIT %s
    """, [month, limit])

@app.get("/dashboard/gap-alerts")
def dashboard_gap_alerts(month: Optional[str] = None):
    """AMCs and schemes with largest rate gaps vs committed."""
    if not month:
        month = q1("SELECT MAX(month) as m FROM amc_monthly")["m"]

    amc_gaps = q("""
        SELECT am.amc_code, am.amc_name, am.registrar,
               ROUND(am.avg_rate::numeric, 4)          AS actual_rate,
               ROUND(cr.committed_rate::numeric, 4)    AS committed_rate,
               ROUND((cr.committed_rate - am.avg_rate)::numeric, 4) AS rate_gap,
               ROUND(((cr.committed_rate - am.avg_rate)/NULLIF(cr.committed_rate,0)*100)::numeric, 2) AS gap_pct,
               ROUND(am.brokerage::numeric, 2)         AS brokerage
        FROM (
            SELECT amc_code, amc_name, registrar,
                   SUM(brokerage) AS brokerage,
                   SUM(brokerage * avg_rate)/NULLIF(SUM(brokerage),0) AS avg_rate
            FROM amc_monthly WHERE month = %s
            GROUP BY amc_code, amc_name, registrar
        ) am
        JOIN committed_rates cr ON cr.amc_code = am.amc_code AND cr.scheme_code IS NULL
        WHERE cr.committed_rate > am.avg_rate
        ORDER BY rate_gap DESC LIMIT 5
    """, [month])

    return {"month": month, "amc_gaps": amc_gaps}

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 2 — RECONCILIATION
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/reconciliation/amc")
def reconciliation_amc(
    month: Optional[str] = None,
    registrar: Optional[str] = None,
    min_gap_pct: float = 0
):
    """AMC-level actual vs committed reconciliation.
    Rolled up from reconciliation_verified_scheme_month (raw records,
    year-band aware, correct split-payment handling)."""
    if not month:
        month = q1("SELECT MAX(month) as m FROM reconciliation_verified_scheme_month")["m"]

    filters = ["v.month = %s"]
    params = [month]
    if registrar:
        filters.append("v.registrar = %s")
        params.append(registrar)

    where = " AND ".join(filters)

    return q(f"""
        SELECT
            amc_code, amc_name, registrar, month,
            ROUND(wtd_actual_rate::numeric, 4)      AS actual_rate,
            ROUND(committed_rate_blend::numeric, 4) AS committed_rate,
            ROUND((wtd_actual_rate - committed_rate_blend)::numeric, 4) AS rate_gap,
            ROUND(((wtd_actual_rate - committed_rate_blend)/NULLIF(committed_rate_blend,0)*100)::numeric, 2) AS gap_pct,
            ROUND(brokerage::numeric, 2) AS actual_brokerage,
            ROUND(shortfall_total::numeric, 2) AS shortfall_rupees
        FROM (
            SELECT v.amc_code, v.amc_name, v.registrar, v.month,
                   SUM(v.brokerage_amt) AS brokerage,
                   SUM(v.brokerage_amt * v.wtd_actual_rate) / NULLIF(SUM(v.brokerage_amt),0) AS wtd_actual_rate,
                   SUM(v.brokerage_amt * v.committed_rate_blend) / NULLIF(SUM(v.brokerage_amt),0) AS committed_rate_blend,
                   SUM(v.shortfall_rupees) AS shortfall_total
            FROM reconciliation_verified_scheme_month v
            WHERE {where}
            GROUP BY v.amc_code, v.amc_name, v.registrar, v.month
        ) rolled
        WHERE ABS((wtd_actual_rate - COALESCE(committed_rate_blend,0))/NULLIF(COALESCE(committed_rate_blend,0),0)*100) >= %s
        ORDER BY shortfall_total DESC
    """, params + [min_gap_pct])

@app.get("/reconciliation/scheme")
def reconciliation_scheme(
    month: Optional[str] = None,
    amc_code: Optional[str] = None,
    min_gap_pct: float = 0
):
    """Scheme-level actual vs committed reconciliation.
    Reads from reconciliation_verified_scheme_month — built directly from
    raw brokerage_records with year-band-aware committed rates and correct
    split-payment handling, instead of the blended scheme_monthly table."""
    if not month:
        month = q1("SELECT MAX(month) as m FROM reconciliation_verified_scheme_month")["m"]

    filters = ["v.month = %s"]
    params = [month]
    if amc_code:
        filters.append("v.amc_code = %s")
        params.append(amc_code)

    where = " AND ".join(filters)

    return q(f"""
        SELECT
            v.scheme_code, v.scheme_name, v.amc_name, v.amc_code, v.month,
            ROUND(v.wtd_actual_rate::numeric, 4)      AS actual_rate,
            ROUND(v.committed_rate_blend::numeric, 4) AS committed_rate,
            ROUND(v.deviation_pct::numeric, 2)        AS gap_pct,
            ROUND(v.brokerage_amt::numeric, 2)        AS actual_brokerage,
            v.folios                                  AS investors,
            v.ok_count, v.bonus_count, v.investigate_count,
            ROUND(v.shortfall_rupees::numeric, 2)     AS shortfall_rupees
        FROM reconciliation_verified_scheme_month v
        WHERE {where}
          AND ABS(v.deviation_pct) >= %s
        ORDER BY v.shortfall_rupees DESC
        LIMIT 200
    """, params + [min_gap_pct])

@app.get("/reconciliation/period-summary")
def reconciliation_period_summary():
    """Month-by-month reconciliation summary for 11 months."""
    return q("""
        SELECT
            am.month,
            ROUND(SUM(am.brokerage)::numeric, 2)          AS actual_brokerage,
            COUNT(DISTINCT am.amc_code)                   AS amcs,
            ROUND(AVG(am.avg_rate)::numeric, 4)           AS avg_rate
        FROM amc_monthly am
        WHERE am.month >= '2025-04'
        GROUP BY am.month
        ORDER BY am.month
    """)

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 3 — AMC INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/amcs")
def list_amcs(month: Optional[str] = None, registrar: Optional[str] = None):
    """AMC list with current month performance."""
    if not month:
        month = q1("SELECT MAX(month) as m FROM amc_monthly")["m"]

    filters = ["am.month = %s"]
    params = [month]
    if registrar:
        filters.append("am.registrar = %s")
        params.append(registrar)

    where = " AND ".join(filters)

    return q(f"""
        SELECT
            am.amc_code, am.amc_name, am.registrar,
            ROUND(SUM(am.brokerage)::numeric, 2)          AS monthly_brokerage,
            ROUND((SUM(am.brokerage * am.avg_rate)/NULLIF(SUM(am.brokerage),0))::numeric, 4) AS avg_rate,
            ROUND(cr.committed_rate::numeric, 4)          AS committed_rate,
            cr.rate_type,
            ROUND(((SUM(am.brokerage * am.avg_rate)/NULLIF(SUM(am.brokerage),0)) - cr.committed_rate)::numeric, 4) AS rate_gap,
            SUM(am.investors)                             AS investors,
            SUM(am.schemes)                               AS schemes
        FROM amc_monthly am
        LEFT JOIN committed_rates cr ON cr.amc_code = am.amc_code AND cr.scheme_code IS NULL
        WHERE {where}
        GROUP BY am.amc_code, am.amc_name, am.registrar, cr.committed_rate, cr.rate_type
        ORDER BY monthly_brokerage DESC
    """, params)

@app.get("/amcs/{amc_code}")
def amc_detail(amc_code: str):
    """AMC detail: summary, monthly trend, top schemes, top investors."""
    info = q1("""
        SELECT amc_code, amc_name, registrar
        FROM amc_monthly WHERE amc_code = %s LIMIT 1
    """, [amc_code])

    trend = q("""
        SELECT month,
               ROUND(SUM(brokerage)::numeric, 2)                                    AS brokerage,
               ROUND((SUM(brokerage*avg_rate)/NULLIF(SUM(brokerage),0))::numeric, 4) AS avg_rate
        FROM amc_monthly WHERE amc_code = %s AND month >= '2025-04'
        GROUP BY month ORDER BY month
    """, [amc_code])

    ytd = q1("""
        SELECT ROUND(SUM(brokerage)::numeric, 2) AS ytd,
               COUNT(DISTINCT month) AS months_active
        FROM amc_monthly WHERE amc_code = %s AND month >= '2025-04'
    """, [amc_code])

    committed = q1("""
        SELECT committed_rate, rate_type, source
        FROM committed_rates WHERE amc_code = %s AND scheme_code IS NULL LIMIT 1
    """, [amc_code])

    schemes = q("""
        SELECT scheme_code,
               COALESCE(NULLIF(scheme_name, ''), scheme_code) AS scheme_name,
               ROUND(SUM(brokerage)::numeric, 2)                                        AS brokerage,
               ROUND((SUM(brokerage*weighted_rate)/NULLIF(SUM(brokerage),0))::numeric, 4) AS avg_rate,
               SUM(investors)                                                            AS investors
        FROM scheme_monthly WHERE amc_code = %s AND month >= '2025-04'
        GROUP BY scheme_code, scheme_name
        ORDER BY brokerage DESC
    """, [amc_code])

    top_investors = q("""
        SELECT folio_no, investor_name, investor_pan,
               ROUND(SUM(brokerage)::numeric, 2) AS brokerage
        FROM investor_scheme_monthly
        WHERE amc_code = %s AND month >= '2025-04'
        GROUP BY folio_no, investor_name, investor_pan
        ORDER BY brokerage DESC LIMIT 10
    """, [amc_code])

    return {
        "info": info,
        "ytd": ytd,
        "committed_rate": committed,
        "trend": trend,
        "schemes": schemes,
        "top_investors": top_investors,
    }

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 4 — SCHEME INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/schemes/{scheme_code}")
def scheme_detail(scheme_code: str):
    """Scheme detail with trend, investors, and reconciliation."""
    info = q1("""
        SELECT scheme_code, scheme_name, amc_code, amc_name
        FROM scheme_monthly WHERE scheme_code = %s LIMIT 1
    """, [scheme_code])

    trend = q("""
        SELECT month,
               ROUND(SUM(brokerage)::numeric, 2)                                        AS brokerage,
               ROUND((SUM(brokerage*weighted_rate)/NULLIF(SUM(brokerage),0))::numeric, 4) AS avg_rate,
               SUM(investors) AS investors
        FROM scheme_monthly WHERE scheme_code = %s AND month >= '2025-04'
        GROUP BY month ORDER BY month
    """, [scheme_code])

    committed = q1("""
        SELECT committed_rate, rate_type, source, category
        FROM committed_rates WHERE scheme_code = %s LIMIT 1
    """, [scheme_code])

    investors = q("""
        SELECT folio_no, investor_name, investor_pan,
               ROUND(SUM(brokerage)::numeric, 2)                                    AS brokerage,
               ROUND(SUM(aum_sum)::numeric, 2)                                      AS aum,
               ROUND((SUM(brokerage*weighted_rate)/NULLIF(SUM(brokerage),0))::numeric,4) AS avg_rate
        FROM investor_scheme_monthly
        WHERE scheme_code = %s AND month >= '2025-04'
        GROUP BY folio_no, investor_name, investor_pan
        ORDER BY brokerage DESC
        LIMIT 50
    """, [scheme_code])

    return {
        "info": info,
        "committed_rate": committed,
        "trend": trend,
        "investors": investors,
    }

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 5 — INVESTOR INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/investors/{folio_no}")
def investor_detail(folio_no: str):
    """Investor detail across all AMCs and schemes."""
    info = q1("""
        SELECT folio_no, investor_name, investor_pan
        FROM investor_scheme_monthly WHERE folio_no = %s LIMIT 1
    """, [folio_no])

    ytd = q1("""
        SELECT ROUND(SUM(brokerage)::numeric, 2)  AS ytd_brokerage,
               COUNT(DISTINCT amc_code)           AS amcs,
               COUNT(DISTINCT scheme_code)        AS schemes
        FROM investor_scheme_monthly
        WHERE folio_no = %s AND month >= '2025-04'
    """, [folio_no])

    by_scheme = q("""
        SELECT ism.scheme_code, ism.amc_code,
               sm.scheme_name, sm.amc_name,
               ROUND(SUM(ism.brokerage)::numeric, 2)                                        AS brokerage,
               ROUND(SUM(ism.aum_sum)::numeric, 2)                                          AS aum,
               ROUND((SUM(ism.brokerage*ism.weighted_rate)/NULLIF(SUM(ism.brokerage),0))::numeric,4) AS avg_rate
        FROM investor_scheme_monthly ism
        LEFT JOIN (
            SELECT DISTINCT ON (scheme_code) scheme_code, scheme_name, amc_name
            FROM scheme_monthly ORDER BY scheme_code, month DESC
        ) sm ON sm.scheme_code = ism.scheme_code
        WHERE ism.folio_no = %s AND ism.month >= '2025-04'
        GROUP BY ism.scheme_code, ism.amc_code, sm.scheme_name, sm.amc_name
        ORDER BY brokerage DESC
    """, [folio_no])

    trend = q("""
        SELECT month, ROUND(SUM(brokerage)::numeric, 2) AS brokerage
        FROM investor_scheme_monthly
        WHERE folio_no = %s AND month >= '2025-04'
        GROUP BY month ORDER BY month
    """, [folio_no])

    return {
        "info": info,
        "ytd": ytd,
        "by_scheme": by_scheme,
        "trend": trend,
    }

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 6 — RATE REPOSITORY
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/rates")
def rate_repository(
    amc_code: Optional[str] = None,
    rate_type: Optional[str] = None,
    category: Optional[str] = None
):
    filters = ["1=1"]
    params = []
    if amc_code:
        filters.append("amc_code = %s"); params.append(amc_code)
    if rate_type:
        filters.append("rate_type = %s"); params.append(rate_type)
    if category:
        filters.append("category = %s"); params.append(category)

    return q(f"""
        SELECT amc_code, amc_name, scheme_code, scheme_name,
               category, committed_rate, rate_type,
               valid_from, valid_to, source, notes
        FROM committed_rates
        WHERE {" AND ".join(filters)}
        ORDER BY amc_name, scheme_name
    """, params)

# ─────────────────────────────────────────────────────────────────────────────
# MODULE 7 — SEARCH
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/search")
def search(q_str: str = Query(..., alias="q"), limit: int = 20):
    """Search across investors, schemes, AMCs."""
    term = f"%{q_str.lower()}%"

    investors = q("""
        SELECT DISTINCT folio_no, investor_name, investor_pan,
               ROUND(SUM(brokerage)::numeric,2) AS ytd_brokerage
        FROM investor_scheme_monthly
        WHERE (LOWER(investor_name) LIKE %s OR LOWER(investor_pan) LIKE %s
               OR LOWER(folio_no) LIKE %s)
          AND month >= '2025-04'
        GROUP BY folio_no, investor_name, investor_pan
        ORDER BY ytd_brokerage DESC LIMIT %s
    """, [term, term, term, limit])

    schemes = q("""
        SELECT DISTINCT scheme_code, scheme_name, amc_name,
               ROUND(SUM(brokerage)::numeric,2) AS ytd_brokerage
        FROM scheme_monthly
        WHERE LOWER(scheme_name) LIKE %s AND month >= '2025-04'
        GROUP BY scheme_code, scheme_name, amc_name
        ORDER BY ytd_brokerage DESC LIMIT %s
    """, [term, limit])

    amcs = q("""
        SELECT DISTINCT amc_code, amc_name, registrar,
               ROUND(SUM(brokerage)::numeric,2) AS ytd_brokerage
        FROM amc_monthly
        WHERE LOWER(amc_name) LIKE %s AND month >= '2025-04'
        GROUP BY amc_code, amc_name, registrar
        ORDER BY ytd_brokerage DESC LIMIT %s
    """, [term, limit])

    return {
        "query": q_str,
        "investors": investors,
        "schemes": schemes,
        "amcs": amcs,
    }

