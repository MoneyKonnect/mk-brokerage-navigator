"""
MoneyKonnect ETL Pipeline v1
Processes CAMS (DBF) + Karvy (CSV) brokerage files → SQLite

Usage:
  python3 etl_pipeline.py
  
Drop new month files into data/cams/ or data/karvy/ and re-run.
Already-loaded files are skipped automatically.
"""

import os, struct, sqlite3, glob, zipfile, tempfile, hashlib
import pyzipper
import pandas as pd
from pathlib import Path
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
CAMS_DIR  = "./data/cams"
KARVY_DIR = "./data/karvy"
DB_PATH   = "./data/records.db"

CAMS_KEEP_TYPES = {'TF'}

KARVY_TRAIL_HEADS = {
    'LongTermTrailFee', 'Annualized', 'Addtrail', 'AddAnnual',
    'SIPAddtrail', 'SIPAddAnnual', 'SIPSTP AddIncentiveTrail',
    'SIPSTP AddIncentiveAnual', 'SIP Additional Trail'
}

CAMS_AMC_MAP = {
    'B':'Aditya Birla Sun Life MF','G':'Bandhan MF','H':'HDFC MF',
    'IF':'360 ONE MF','L':'SBI MF','O':'HSBC MF','Y':'WhiteOak Capital MF',
    'D':'DSP','FTI':'Franklin Templeton','HLS':'Helios','K':'Kotak',
    'P':'ICICI Prudential','PP':'PPFAS','T':'Tata','UK':'Union',
    'BS':'Bandhan MF','IFS':'360 ONE MF','TS':'Tata','FTS':'Franklin Templeton','GS':'Bandhan MF'
}

# ── DB SETUP ──────────────────────────────────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS brokerage_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    registrar       TEXT,
    source_file     TEXT,
    amc_code        TEXT,
    amc_name        TEXT,
    scheme_code     TEXT,
    scheme_name     TEXT,
    folio_no        TEXT,
    investor_name   TEXT,
    investor_pan    TEXT,
    brokerage_type  TEXT,
    brokerage_head  TEXT,
    rate            REAL,
    brokerage_amt   REAL,
    aum             REAL,
    period_from     TEXT,
    period_to       TEXT,
    proc_date       TEXT,
    is_negative     INTEGER DEFAULT 0,
    asset_type      TEXT,
    loaded_at       TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS load_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash   TEXT UNIQUE,
    source_file TEXT,
    registrar   TEXT,
    records     INTEGER,
    loaded_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_amc      ON brokerage_records(amc_code);
CREATE INDEX IF NOT EXISTS idx_scheme   ON brokerage_records(scheme_code);
CREATE INDEX IF NOT EXISTS idx_folio    ON brokerage_records(folio_no);
CREATE INDEX IF NOT EXISTS idx_period   ON brokerage_records(period_from);
CREATE INDEX IF NOT EXISTS idx_reg      ON brokerage_records(registrar);
CREATE INDEX IF NOT EXISTS idx_pan      ON brokerage_records(investor_pan);
"""

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DDL)
    conn.commit()
    return conn

def already_loaded(conn, file_hash):
    r = conn.execute("SELECT 1 FROM load_log WHERE file_hash=?", (file_hash,)).fetchone()
    return r is not None

def file_hash(path):
    h = hashlib.md5()
    with open(path,'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''): h.update(chunk)
    return h.hexdigest()

# ── UTILS ─────────────────────────────────────────────────────────────────────
def norm_date(val):
    if not val or str(val).strip() in ('','nan'): return None
    val = str(val).strip()
    for fmt in ('%Y%m%d','%d/%m/%Y','%m/%d/%Y','%Y-%m-%d'):
        try: return datetime.strptime(val, fmt).strftime('%Y-%m-%d')
        except: pass
    return None

def read_dbf(path):
    with open(path,'rb') as f:
        h=f.read(32)
        num=struct.unpack('<I',h[4:8])[0]
        hsize=struct.unpack('<H',h[8:10])[0]
        rsize=struct.unpack('<H',h[10:12])[0]
        fields=[]
        while True:
            fd=f.read(32)
            if not fd or fd[0]==0x0D: break
            fields.append((fd[:11].replace(b'\x00',b'').decode('latin-1'),chr(fd[11]),fd[16]))
        f.seek(hsize); rows=[]
        for _ in range(num):
            rec=f.read(rsize)
            if not rec or rec[0]==0x1A: break
            row={}; pos=1
            for name,ft,l in fields:
                row[name]=rec[pos:pos+l].decode('latin-1',errors='replace').strip(); pos+=l
            rows.append(row)
    return pd.DataFrame(rows)

# ── CAMS PROCESSOR ────────────────────────────────────────────────────────────
def process_cams_dbf(path, source_file, conn):
    fhash = file_hash(path)
    if already_loaded(conn, fhash):
        print(f"  SKIP (already loaded): {Path(path).name}")
        return 0
    print(f"  Loading CAMS DBF: {Path(path).name}")
    df = read_dbf(path)
    if df.empty: return 0
    df = df[df['BRKAGE_TYP'].isin(CAMS_KEEP_TYPES)].copy()
    if df.empty: return 0
    for c in ['BRKAGE_RAT','BRKAGE_AMT','AVG_ASSETS']:
        df[c]=pd.to_numeric(df[c],errors='coerce').fillna(0)
    out = pd.DataFrame({
        'registrar':      'CAMS',
        'source_file':    Path(source_file).name,
        'amc_code':       df['AMC_CODE'],
        'amc_name':       df['AMC_CODE'].map(CAMS_AMC_MAP).fillna(df['AMC_CODE']),
        'scheme_code':    df['SCHEME_COD'].str.lstrip('0'),
        'scheme_name':    '',
        'folio_no':       df['FOLIO_NO'],
        'investor_name':  df['INV_NAME'],
        'investor_pan':   '',
        'brokerage_type': df['BRKAGE_TYP'],
        'brokerage_head': df['BRKAGE_TYP'],
        'rate':           df['BRKAGE_RAT'],
        'brokerage_amt':  df['BRKAGE_AMT'],
        'aum':            df['AVG_ASSETS'],
        'period_from':    df['BRKAGE_FRO'].apply(norm_date),
        'period_to':      df['BRKAGE_TO'].apply(norm_date),
        'proc_date':      df['PROC_DATE'].apply(norm_date),
        'is_negative':    (df['BRKAGE_RAT']<0).astype(int),
        'asset_type':     df.get('BROK_CATEG','').fillna('') if 'BROK_CATEG' in df.columns else '',
    })
    out.to_sql('brokerage_records', conn, if_exists='append', index=False)
    conn.execute("INSERT INTO load_log (file_hash,source_file,registrar,records) VALUES (?,?,?,?)",
                 (fhash, Path(source_file).name, 'CAMS', len(out)))
    conn.commit()
    print(f"    → {len(out):,} records loaded")
    return len(out)

# ── KARVY PROCESSOR ───────────────────────────────────────────────────────────
def process_karvy_csv(path, source_file, conn):
    fhash = file_hash(path)
    if already_loaded(conn, fhash):
        print(f"  SKIP (already loaded): {Path(path).name}")
        return 0
    print(f"  Loading Karvy CSV: {Path(path).name}")
    df = pd.read_csv(path, encoding='latin-1', low_memory=False)
    if df.empty: return 0
    # Validate expected columns - skip files with different schema
    required = {'Brokerage Type', 'Brokerage Head', 'Percentage (%)', 'Brokerage (in Rs.)', 'Average Assets'}
    if not required.issubset(set(df.columns)):
        print(f"    SKIP: unexpected columns: {list(df.columns)[:5]}...")
        return 0
    df = df[~df['Brokerage Type'].str.startswith('Not Paid',na=False)].copy()
    df = df[df['Brokerage Head'].isin(KARVY_TRAIL_HEADS)].copy()
    if df.empty: return 0
    for c in ['Percentage (%)','Brokerage (in Rs.)','Average Assets']:
        df[c]=pd.to_numeric(df[c],errors='coerce').fillna(0)
    out = pd.DataFrame({
        'registrar':      'Karvy',
        'source_file':    Path(source_file).name,
        'amc_code':       df['Fund'].astype(str),
        'amc_name':       df['Fund Description'],
        'scheme_code':    df['Scheme Code'].astype(str),
        'scheme_name':    df['Fund Description'],
        'folio_no':       df['Account Number'].astype(str),
        'investor_name':  df['Investor Name'],
        'investor_pan':   df.get('InvPAN','').fillna(''),
        'brokerage_type': df['Brokerage Type'],
        'brokerage_head': df['Brokerage Head'],
        'rate':           df['Percentage (%)'],
        'brokerage_amt':  df['Brokerage (in Rs.)'],
        'aum':            df['Average Assets'],
        'period_from':    df['From Date'].apply(norm_date),
        'period_to':      df['To Date'].apply(norm_date),
        'proc_date':      df['Process Date'].apply(norm_date),
        'is_negative':    (df['Percentage (%)']<0).astype(int),
        'asset_type':     df.get('AssetType','').fillna(''),
    })
    out.to_sql('brokerage_records', conn, if_exists='append', index=False)
    conn.execute("INSERT INTO load_log (file_hash,source_file,registrar,records) VALUES (?,?,?,?)",
                 (fhash, Path(source_file).name, 'Karvy', len(out)))
    conn.commit()
    print(f"    → {len(out):,} records loaded")
    return len(out)


# ── KARVY W0B DBF PROCESSOR ───────────────────────────────────────────────────
KARVY_TRAIL_TYPES = {
    'LongTermTrailFee', 'Annualized', 'Addtrail', 'AddAnnual',
    'SIPAddtrail', 'SIPAddAnnual', 'SIPSTP AddIncentiveTrail',
    'SIPSTP AddIncentiveAnual', 'SIP Additional Trail'
}

def process_karvy_wob_dbf(path, source_file, conn):
    fhash = file_hash(path)
    if already_loaded(conn, fhash):
        print(f"  SKIP (already loaded): {Path(path).name}")
        return 0
    print(f"  Loading Karvy W0B DBF: {Path(path).name}")
    df = read_dbf(path)
    if df.empty: return 0

    # Validate it's a W0B file
    if 'BROKTYPE' not in df.columns or 'ACCOUNTNO' not in df.columns:
        print(f"    SKIP: not a W0B DBF")
        return 0

    # Filter to trail types only, exclude Not Paid
    df = df[df['BROKTYPE'].isin(KARVY_TRAIL_TYPES)].copy()
    if df.empty: return 0

    for c in ['PERCENTAGE', 'BROKERAGE', 'AVGASSETS']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

    out = pd.DataFrame({
        'registrar':      'Karvy',
        'source_file':    Path(source_file).name,
        'amc_code':       df['FUND'].astype(str),
        'amc_name':       df['FUNDDESC'],
        'scheme_code':    df['SCHPLN'].astype(str),
        'scheme_name':    df['FUNDDESC'],
        'folio_no':       df['ACCOUNTNO'].astype(str),
        'investor_name':  df['INVESTORN0'],
        'investor_pan':   pd.Series(df['INVPAN'] if 'INVPAN' in df.columns else '').fillna(''),
        'brokerage_type': df['BROKTYPE'],
        'brokerage_head': df['BROKTYPE'],
        'rate':           df['PERCENTAGE'],
        'brokerage_amt':  df['BROKERAGE'],
        'aum':            df['AVGASSETS'],
        'period_from':    df['FROMDATE'].apply(norm_date),
        'period_to':      df['TODATE'].apply(norm_date),
        'proc_date':      df['PROCESSDA3'].apply(norm_date),
        'is_negative':    (df['PERCENTAGE'] < 0).astype(int),
        'asset_type':     (df['ASSETTYPE'] if 'ASSETTYPE' in df.columns else pd.Series([''] * len(df))).fillna(''),
    })
    out.to_sql('brokerage_records', conn, if_exists='append', index=False)
    conn.execute("INSERT INTO load_log (file_hash,source_file,registrar,records) VALUES (?,?,?,?)",
                 (fhash, Path(source_file).name, 'Karvy', len(out)))
    conn.commit()
    print(f"    → {len(out):,} records loaded")
    return len(out)

# ── ZIP HANDLER ───────────────────────────────────────────────────────────────
def handle_zip(zip_path, kind, conn):
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        with pyzipper.AESZipFile(zip_path,'r') as z:
                z.setpassword(b'Money@123')
                z.extractall(tmp)
        for ext in ['*.dbf','*.DBF','*.csv','*.CSV']:
            for fp in glob.glob(f"{tmp}/**/{ext}", recursive=True):
                name = Path(fp).name.lower()
                if kind=='cams' and name.endswith('.dbf'):
                    total += process_cams_dbf(fp, zip_path, conn)
                elif kind=='karvy' and (name.endswith('.csv') or name.endswith('.dbf')):
                    if name.endswith('.dbf'):
                        total += process_karvy_wob_dbf(fp, zip_path, conn)
                    elif name.endswith('.csv'):
                        total += process_karvy_csv(fp, zip_path, conn)
        for nested in glob.glob(f"{tmp}/**/*.zip", recursive=True):
            try:
                total += handle_zip(nested, kind, conn)
            except Exception as e:
                print(f"    Skipping nested zip: {e}")
    return total

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(CAMS_DIR, exist_ok=True)
    os.makedirs(KARVY_DIR, exist_ok=True)
    conn = get_conn()
    total = 0

    print("=== CAMS ===")
    for fp in sorted(glob.glob(f"{CAMS_DIR}/**/*.zip", recursive=True) +
                     glob.glob(f"{CAMS_DIR}/*.zip")):
        total += handle_zip(fp, 'cams', conn)
    for fp in sorted(glob.glob(f"{CAMS_DIR}/**/*.dbf", recursive=True) +
                     glob.glob(f"{CAMS_DIR}/*.dbf") +
                     glob.glob(f"{CAMS_DIR}/**/*.DBF", recursive=True)):
        total += process_cams_dbf(fp, fp, conn)

    print("\n=== Karvy ===")
    for fp in sorted(glob.glob(f"{KARVY_DIR}/**/*.zip", recursive=True) +
                     glob.glob(f"{KARVY_DIR}/*.zip")):
        total += handle_zip(fp, 'karvy', conn)
    for fp in sorted(glob.glob(f"{KARVY_DIR}/**/*.dbf", recursive=True) +
                     glob.glob(f"{KARVY_DIR}/**/*.DBF", recursive=True)):
        total += process_karvy_wob_dbf(fp, fp, conn)
    for fp in sorted(glob.glob(f"{KARVY_DIR}/**/*.csv", recursive=True) +
                     glob.glob(f"{KARVY_DIR}/*.csv") +
                     glob.glob(f"{KARVY_DIR}/**/*.CSV", recursive=True)):
        total += process_karvy_csv(fp, fp, conn)

    print(f"\n=== Done: {total:,} new records loaded ===")
    print("\nDatabase summary:")
    print(pd.read_sql("""
        SELECT registrar,
               COUNT(*) as records,
               COUNT(DISTINCT amc_code) as amcs,
               COUNT(DISTINCT folio_no) as investors,
               ROUND(SUM(brokerage_amt)/100000,2) as brokerage_lakh,
               ROUND(SUM(aum)/10000000,2) as aum_cr
        FROM brokerage_records
        GROUP BY registrar
    """, conn).to_string(index=False))
    print(f"\nDB size: {os.path.getsize(DB_PATH)/1024/1024:.1f} MB")
    conn.close()

if __name__ == '__main__':
    main()
