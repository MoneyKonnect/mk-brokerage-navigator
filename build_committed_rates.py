"""
build_committed_rates.py
Builds committed_rates table in summary.db from:
1. Inline email data (PPFAS, Helios, Axis)
2. Actual weighted average rates from brokerage data (all other AMCs)
   — used as baseline where committed rates aren't available

Run: python3 build_committed_rates.py
"""
import sqlite3, pandas as pd

SUMMARY_DB = "./data/summary.db"

conn = sqlite3.connect(SUMMARY_DB)

conn.executescript("""
DROP TABLE IF EXISTS committed_rates;
CREATE TABLE committed_rates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    registrar       TEXT,
    amc_code        TEXT,
    amc_name        TEXT,
    scheme_code     TEXT,    -- NULL = applies to all schemes of this AMC
    scheme_name     TEXT,
    category        TEXT,    -- equity/debt/hybrid/liquid/other
    committed_rate  REAL,    -- annualized %
    rate_type       TEXT,    -- 'confirmed' = from email, 'derived' = from actual avg
    valid_from      TEXT,
    valid_to        TEXT,
    source          TEXT,
    notes           TEXT
);
""")

# ── 1. PPFAS — from email body (March 2026, stable rates) ────────────────────
ppfas = [
    ('CAMS','PP','PPFAS MF','PPFC','Parag Parikh Flexi Cap Fund','equity',0.65,'confirmed','2025-04-01','2026-02-28','Email Mar 2026','One rate for all'),
    ('CAMS','PP','PPFAS MF','PPELSS','Parag Parikh ELSS Tax Saver Fund','equity',1.10,'confirmed','2025-04-01','2026-02-28','Email Mar 2026','One rate for all'),
    ('CAMS','PP','PPFAS MF','PPLIQ','Parag Parikh Liquid Fund','liquid',0.10,'confirmed','2025-04-01','2026-02-28','Email Mar 2026','One rate for all'),
    ('CAMS','PP','PPFAS MF','PPCH','Parag Parikh Conservative Hybrid Fund','hybrid',0.30,'confirmed','2025-04-01','2026-02-28','Email Mar 2026','One rate for all'),
    ('CAMS','PP','PPFAS MF','PPARB','Parag Parikh Arbitrage Fund','hybrid',0.35,'confirmed','2025-04-01','2026-02-28','Email Mar 2026','One rate for all'),
    ('CAMS','PP','PPFAS MF','PPDAA','Parag Parikh Dynamic Asset Allocation Fund','hybrid',0.30,'confirmed','2025-04-01','2026-02-28','Email Mar 2026','One rate for all'),
    ('CAMS','PP','PPFAS MF','PPLCF','Parag Parikh Large Cap Fund','equity',0.40,'confirmed','2025-04-01','2026-02-28','Email Mar 2026','One rate for all'),
]

# ── 2. Helios — from email body (Oct-Dec 2025, year-based) ───────────────────
# Using 4th year+ as steady-state committed rate (most representative for reconciliation)
helios = [
    ('CAMS','HLS','Helios MF','HLSFC','Helios Flexi Cap Fund','equity',1.10,'confirmed','2025-10-01','2025-12-31','Email Oct 2025','4th yr+ rate; 1.20% for yr1-3'),
    ('CAMS','HLS','Helios MF','HLSBAF','Helios Balanced Advantage Fund','hybrid',1.55,'confirmed','2025-10-01','2025-12-31','Email Oct 2025','4th yr+ rate; 1.70% for yr1-3'),
    ('CAMS','HLS','Helios MF','HLSFIN','Helios Financial Services Fund','equity',1.50,'confirmed','2025-10-01','2025-12-31','Email Oct 2025','4th yr+ rate; 1.60% for yr1-3'),
    ('CAMS','HLS','Helios MF','HLSLMC','Helios Large & Mid Cap Fund','equity',1.55,'confirmed','2025-10-01','2025-12-31','Email Oct 2025','4th yr+ rate; 1.65% for yr1-3'),
    ('CAMS','HLS','Helios MF','HLSMC','Helios Mid Cap Fund','equity',1.55,'confirmed','2025-10-01','2025-12-31','Email Oct 2025','4th yr+ rate; 1.65% for yr1-3'),
    ('CAMS','HLS','Helios MF','HLSON','Helios Overnight Fund','liquid',0.05,'confirmed','2025-10-01','2025-12-31','Email Oct 2025','Flat rate all years'),
]

# Apply same Helios rates for other quarters (Apr-Sep 2025, Jan-Feb 2026)
# since they were stable before Apr 2026 regulatory changes
helios_all = []
for rec in helios:
    # Apr-Sep 2025 (slight adjustment for newer fund rates - use 1st year rate)
    r = list(rec); r[7] = rec[6] + 0.10 if rec[5] == 'equity' else rec[6]; r[8]='2025-04-01'; r[9]='2025-09-30'; r[10]='Email (extrapolated)'
    helios_all.append(tuple(r))
    # Oct-Dec 2025 (confirmed)
    helios_all.append(rec)
    # Jan-Feb 2026 (same structure)
    r2 = list(rec); r2[8]='2026-01-01'; r2[9]='2026-02-28'; r2[10]='Email Oct 2025 (applied)'
    helios_all.append(tuple(r2))

# ── 3. Axis — from email body (JFM 2026 tie-up rates) ────────────────────────
# Karvy AMC, using fund code 128
axis = [
    ('Karvy','128','Axis MF','AXMC','Axis Multicap Fund','equity',1.35,'confirmed','2026-01-01','2026-03-31','Email Jan 2026','Card 1.15% + Add 0.20%'),
    ('Karvy','128','Axis MF','AXVF','Axis Value Fund','equity',1.60,'confirmed','2026-01-01','2026-03-31','Email Jan 2026','Card 1.40% + Add 0.20%'),
    ('Karvy','128','Axis MF','AXCHF','Axis Childrens Fund','equity',1.38,'confirmed','2026-01-01','2026-03-31','Email Jan 2026','Card 1.35% + Add 0.03%'),
    ('Karvy','128','Axis MF','AXFOF','Axis Global Equity Alpha FoF','equity',1.05,'confirmed','2026-01-01','2026-03-31','Email Jan 2026','Card 1.00% + Add 0.05%'),
    ('Karvy','128','Axis MF','AXBAF','Axis Balance Advantage Fund','hybrid',1.45,'confirmed','2026-01-01','2026-03-31','Email Jan 2026','Card 1.29% + Add 0.16%'),
]

# ── 4. Derived rates — AMC-level averages from actual brokerage data ──────────
# For all other AMCs, use weighted average actual rate as baseline
# This gives us a benchmark even without committed rate data
derived_q = """
    SELECT registrar, amc_code, amc_name,
           ROUND(SUM(brokerage * avg_rate) / NULLIF(SUM(brokerage), 0), 4) as avg_committed
    FROM (
        SELECT registrar, amc_code, amc_name,
               brokerage, avg_rate
        FROM amc_monthly
        WHERE month >= '2025-04' AND avg_rate > 0
    )
    GROUP BY registrar, amc_code, amc_name
    HAVING avg_committed > 0
    ORDER BY registrar, amc_code
"""
derived = pd.read_sql(derived_q, conn)
print(f"Derived AMC baselines: {len(derived)}")
print(derived.to_string(index=False))

# ── Insert all confirmed rates ────────────────────────────────────────────────
confirmed_records = ppfas + helios_all + axis
for r in confirmed_records:
    conn.execute("""INSERT INTO committed_rates
        (registrar,amc_code,amc_name,scheme_code,scheme_name,category,
         committed_rate,rate_type,valid_from,valid_to,source,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", r)

# Insert derived AMC-level rates (NULL scheme_code = applies to all schemes)
for _, row in derived.iterrows():
    # Skip if already have confirmed rates for this AMC
    existing = conn.execute(
        "SELECT COUNT(*) FROM committed_rates WHERE amc_code=? AND rate_type='confirmed'",
        (row['amc_code'],)).fetchone()[0]
    if existing == 0:
        conn.execute("""INSERT INTO committed_rates
            (registrar,amc_code,amc_name,scheme_code,scheme_name,category,
             committed_rate,rate_type,valid_from,valid_to,source,notes)
            VALUES (?,?,?,NULL,'ALL SCHEMES','mixed',?,?,?,?,?,?)""",
            (row['registrar'],row['amc_code'],row['amc_name'],
             row['avg_committed'],'derived','2025-04-01','2026-02-28',
             'Computed from actual brokerage data','Average of actual weighted rates'))

conn.commit()

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n=== Committed Rate Master ===")
summary = pd.read_sql("""
    SELECT registrar, amc_code, amc_name,
           COUNT(*) as rate_records,
           MIN(committed_rate) as min_rate,
           MAX(committed_rate) as max_rate,
           rate_type
    FROM committed_rates
    GROUP BY registrar, amc_code, rate_type
    ORDER BY registrar, amc_code
""", conn)
print(summary.to_string(index=False))
print(f"\nTotal rate records: {conn.execute('SELECT COUNT(*) FROM committed_rates').fetchone()[0]}")
conn.close()
print("\nDone. Run reconciliation engine next.")
