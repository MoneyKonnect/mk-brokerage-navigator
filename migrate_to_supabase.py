"""
migrate_to_supabase.py — Migrate records.db + summary.db (SQLite) to Supabase Postgres

Run: python3 migrate_to_supabase.py

Requires: pip install psycopg2-binary --break-system-packages
"""

import sqlite3
import psycopg2
from psycopg2.extras import execute_values
import sys

SUPABASE_URL = "postgresql://postgres.wlbyhcrdtzxamduwzuss:XcLLfBEaMorD415g@aws-1-ap-south-1.pooler.supabase.com:6543/postgres"

SQLITE_RECORDS_DB = "data/records.db"
SQLITE_SUMMARY_DB = "data/summary.db"

BATCH_SIZE = 5000


def get_sqlite_schema(conn, table_name):
    """Get column names and types from SQLite table."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    return cur.fetchall()  # (cid, name, type, notnull, dflt_value, pk)


def sqlite_type_to_pg(sqlite_type):
    """Map SQLite column type to Postgres type."""
    t = (sqlite_type or "").upper()
    if "INT" in t:
        return "BIGINT"
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return "DOUBLE PRECISION"
    if "TEXT" in t or "CHAR" in t or "CLOB" in t:
        return "TEXT"
    if "BLOB" in t:
        return "BYTEA"
    return "TEXT"


def create_pg_table(pg_conn, table_name, columns):
    """Create table in Postgres matching SQLite schema."""
    col_defs = []
    for cid, name, ctype, notnull, dflt, pk in columns:
        pg_type = sqlite_type_to_pg(ctype)
        col_defs.append(f'"{name}" {pg_type}')
    col_sql = ",\n    ".join(col_defs)

    cur = pg_conn.cursor()
    cur.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
    cur.execute(f'CREATE TABLE "{table_name}" (\n    {col_sql}\n)')
    pg_conn.commit()
    print(f"  Created table: {table_name}")


def migrate_table(sqlite_conn, pg_conn, table_name):
    """Copy all rows from SQLite table to Postgres table in batches."""
    columns = get_sqlite_schema(sqlite_conn, table_name)
    if not columns:
        print(f"  Skipping {table_name} — no columns found")
        return 0

    col_names = [c[1] for c in columns]
    create_pg_table(pg_conn, table_name, columns)

    sqlite_cur = sqlite_conn.cursor()
    sqlite_cur.execute(f'SELECT {", ".join(col_names)} FROM "{table_name}"')

    total_rows = 0
    pg_cur = pg_conn.cursor()
    quoted_cols = ", ".join(f'"{c}"' for c in col_names)
    insert_sql = f'INSERT INTO "{table_name}" ({quoted_cols}) VALUES %s'

    while True:
        rows = sqlite_cur.fetchmany(BATCH_SIZE)
        if not rows:
            break
        execute_values(pg_cur, insert_sql, rows)
        pg_conn.commit()
        total_rows += len(rows)
        print(f"    {table_name}: {total_rows:,} rows migrated", end="\r")

    print(f"    {table_name}: {total_rows:,} rows migrated — done")
    return total_rows


def get_all_tables(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return [row[0] for row in cur.fetchall()]


def migrate_db(sqlite_path, pg_conn, label):
    print(f"\n=== Migrating {label} ({sqlite_path}) ===")
    try:
        sqlite_conn = sqlite3.connect(sqlite_path)
    except Exception as e:
        print(f"  ERROR: could not open {sqlite_path}: {e}")
        return

    tables = get_all_tables(sqlite_conn)
    print(f"  Found tables: {tables}")

    for table in tables:
        try:
            migrate_table(sqlite_conn, pg_conn, table)
        except Exception as e:
            print(f"  ERROR migrating {table}: {e}")
            pg_conn.rollback()

    sqlite_conn.close()


def main():
    print("Connecting to Supabase...")
    try:
        pg_conn = psycopg2.connect(SUPABASE_URL)
    except Exception as e:
        print(f"FATAL: could not connect to Supabase: {e}")
        sys.exit(1)
    print("Connected.")

    migrate_db(SQLITE_RECORDS_DB, pg_conn, "records.db")
    migrate_db(SQLITE_SUMMARY_DB, pg_conn, "summary.db")

    print("\n=== Verification ===")
    pg_cur = pg_conn.cursor()
    pg_cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = [r[0] for r in pg_cur.fetchall()]
    print(f"Tables now in Supabase: {tables}")

    for t in tables:
        pg_cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        count = pg_cur.fetchone()[0]
        print(f"  {t}: {count:,} rows")

    pg_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
