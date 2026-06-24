"""
migrate_to_supabase.py
Migrates all data from summary.db to Supabase PostgreSQL.
Creates tables, loads data, creates indexes, verifies counts.

Run: python3 migrate_to_supabase.py
"""

import sqlite3
import psycopg2
import pandas as pd
from psycopg2.extras import execute_values

SQLITE_PATH = "./data/summary.db"
PG_CONN_STR = "postgresql://postgres:won72YxOpyf052qS@db.wlbyhcrdtzxamduwzuss.supabase.co:5432/postgres"

def get_pg():
    return psycopg2.connect(PG_CONN_STR)

def get_sqlite():
    return sqlite3.connect(SQLITE_PATH)

# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA = """

DROP TABLE IF EXISTS committed_rates CASCADE;
DROP TABLE IF EXISTS clawbacks CASCADE;
DROP TABLE IF EXISTS investor_scheme_monthly CASCADE;
DROP TABLE IF EXISTS scheme_monthly CASCADE;
DROP TABLE IF EXISTS amc_monthly CASCADE;

CREATE TABLE amc_monthly (
    id              SERIAL PRIMARY KEY,
    registrar       TEXT,
    amc_code        TEXT,
    amc_name        TEXT,
    month           TEXT,
    brokerage_head  TEXT,
    records         INTEGER,
    investors       INTEGER,
    schemes         INTEGER,
    brokerage       NUMERIC(14,2),
    avg_rate        NUMERIC(8,4),
    min_rate        NUMERIC(8,4),
    max_rate        NUMERIC(8,4)
);

CREATE TABLE scheme_monthly (
    id              SERIAL PRIMARY KEY,
    registrar       TEXT,
    amc_code        TEXT,
    amc_name        TEXT,
    scheme_code     TEXT,
    scheme_name     TEXT,
    month           TEXT,
    investors       INTEGER,
    brokerage       NUMERIC(14,2),
    aum_sum         NUMERIC(18,2),
    weighted_rate   NUMERIC(8,4)
);

CREATE TABLE investor_scheme_monthly (
    id              SERIAL PRIMARY KEY,
    registrar       TEXT,
    amc_code        TEXT,
    scheme_code     TEXT,
    folio_no        TEXT,
    investor_name   TEXT,
    investor_pan    TEXT,
    month           TEXT,
    brokerage_head  TEXT,
    brokerage       NUMERIC(14,2),
    aum_sum         NUMERIC(18,2),
    weighted_rate   NUMERIC(8,4),
    records         INTEGER
);

CREATE TABLE clawbacks (
    id              SERIAL PRIMARY KEY,
    registrar       TEXT,
    amc_code        TEXT,
    folio_no        TEXT,
    investor_name   TEXT,
    scheme_code     TEXT,
    period_from     TEXT,
    period_to       TEXT,
    brokerage_amt   NUMERIC(14,2),
    rate            NUMERIC(8,4),
    aum             NUMERIC(18,2)
);

CREATE TABLE committed_rates (
    id              SERIAL PRIMARY KEY,
    registrar       TEXT,
    amc_code        TEXT,
    amc_name        TEXT,
    scheme_code     TEXT,
    scheme_name     TEXT,
    category        TEXT,
    committed_rate  NUMERIC(8,4),
    rate_type       TEXT,
    valid_from      TEXT,
    valid_to        TEXT,
    source          TEXT,
    notes           TEXT
);
"""

# ── Indexes ───────────────────────────────────────────────────────────────────
INDEXES = """
CREATE INDEX idx_amc_monthly_month ON amc_monthly(month);
CREATE INDEX idx_amc_monthly_amc ON amc_monthly(amc_code);
CREATE INDEX idx_amc_monthly_registrar ON amc_monthly(registrar);

CREATE INDEX idx_scheme_monthly_month ON scheme_monthly(month);
CREATE INDEX idx_scheme_monthly_amc ON scheme_monthly(amc_code);
CREATE INDEX idx_scheme_monthly_scheme ON scheme_monthly(scheme_code);

CREATE INDEX idx_ism_month ON investor_scheme_monthly(month);
CREATE INDEX idx_ism_amc ON investor_scheme_monthly(amc_code);
CREATE INDEX idx_ism_folio ON investor_scheme_monthly(folio_no);
CREATE INDEX idx_ism_pan ON investor_scheme_monthly(investor_pan);
CREATE INDEX idx_ism_investor ON investor_scheme_monthly(investor_name);

CREATE INDEX idx_committed_amc ON committed_rates(amc_code);
CREATE INDEX idx_committed_scheme ON committed_rates(scheme_code);
"""

def migrate_table(table, sqlite_conn, pg_conn, chunk_size=1000):
    print(f"  Loading {table}...", end=" ", flush=True)
    df = pd.read_sql(f"SELECT * FROM {table}", sqlite_conn)

    # Drop id column — postgres uses SERIAL
    if 'id' in df.columns:
        df = df.drop(columns=['id'])

    cols = list(df.columns)
    col_str = ", ".join(cols)
    placeholders = "%s"

    total = 0
    cur = pg_conn.cursor()

    for i in range(0, len(df), chunk_size):
        chunk = df.iloc[i:i+chunk_size]
        rows = [tuple(None if pd.isna(v) else v for v in row) for row in chunk.values]
        execute_values(
            cur,
            f"INSERT INTO {table} ({col_str}) VALUES %s",
            rows
        )
        total += len(rows)

    pg_conn.commit()
    cur.close()
    print(f"{total:,} rows ✓")
    return total

def main():
    print("Connecting to Supabase...")
    pg = get_pg()
    print("Connected ✓")

    print("\nCreating schema...")
    cur = pg.cursor()
    cur.execute(SCHEMA)
    pg.commit()
    cur.close()
    print("Schema created ✓")

    sqlite = get_sqlite()

    print("\nMigrating data:")
    totals = {}
    for table in ['amc_monthly', 'scheme_monthly', 'investor_scheme_monthly',
                  'clawbacks', 'committed_rates']:
        totals[table] = migrate_table(table, sqlite, pg)

    print("\nCreating indexes...")
    cur = pg.cursor()
    cur.execute(INDEXES)
    pg.commit()
    cur.close()
    print("Indexes created ✓")

    print("\nVerifying row counts:")
    cur = pg.cursor()
    for table, expected in totals.items():
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        actual = cur.fetchone()[0]
        status = "✓" if actual == expected else "✗ MISMATCH"
        print(f"  {table}: {actual:,} rows {status}")
    cur.close()

    sqlite.close()
    pg.close()
    print("\nMigration complete.")

if __name__ == "__main__":
    main()
