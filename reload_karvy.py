"""
reload_karvy.py — Clean Karvy loader
Deletes all Karvy records and reloads from ONE canonical file per month,
filtered to that month's primary brokerage records only.
Run: python3 reload_karvy.py
"""

import os, struct, sqlite3, pyzipper, tempfile, shutil
import pandas as pd
from datetime import datetime
from pathlib import Path

DB_PATH   = "./data/records.db"
KARVY_DIR = "./data/karvy"
PASSWORD  = b'Money@123'

TRAIL_TYPES = {
    'LongTermTrailFee','Annualized','Addtrail','AddAnnual',
    'SIPAddtrail','SIPAddAnnual','SIPSTP AddIncentiveTrail',
    'SIPSTP AddIncentiveAnual','SIP Additional Trail'
}

# One entry per month. 'dbf' = read directly. 'csv' = read CSV file.
# 'month' = YYYYMM prefix to filter FROMDATE / From Date field.
MONTHS = [
    {'label':'Apr 2025','month':'202504','type':'dbf','file':'1 April/405a7d6f-a3f0-4f48-93f7-657a4ceef6aa.dbf'},
    {'label':'May 2025','month':'202505','type':'dbf','file':'2 May/W0B846.dbf'},
    {'label':'Jun 2025','month':'202506','type':'dbf','file':'3 June/MFSD205_WBBRR2206926_1220044018766RR220692666/W0B657.dbf'},
    {'label':'Jul 2025','month':'202507','type':'dbf','file':'4 July/MFSD205_WBBRR2236074_1005039528766RR223607466/W0B376.dbf'},
    {'label':'Aug 2025','month':'202508','type':'dbf','file':'5 Aug/W0B634.dbf'},
    {'label':'Sep 2025','month':'202509','type':'dbf','file':'6 Sept/176249fa-dff3-4d41-b627-f519ca28b708.dbf'},
    {'label':'Oct 2025','month':'202510','type':'dbf','file':'7 Oct/W0B377.dbf'},
    {'label':'Nov 2025','month':'202511','type':'dbf','file':'8 Nov/W0B123.dbf'},
    {'label':'Dec 2025','month':'202512','type':'dbf','file':'9 Dec/W0B128.dbf'},
    {'label':'Jan 2026','month':'202601','type':'csv','file':'10 Jan/MFSD205_WBBRR2612631_1259498.csv'},
    {'label':'Feb 2026','month':'202602','type':'csv','file':'11 Feb/MFSD205_WBBRR2665850_1132512_0.csv'},
]

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

def load_dbf_month(m, conn):
    path = os.path.join(KARVY_DIR, m['file'])
    if not os.path.exists(path):
        print(f"  [{m['label']}] MISSING: {path}")
        return 0

    df = read_dbf(path)
    if df.empty or 'BROKTYPE' not in df.columns:
        print(f"  [{m['label']}] Wrong format or empty")
        return 0

    # Filter to primary month and trail types only
    df = df[df['FROMDATE'].str.startswith(m['month'], na=False)].copy()
    df = df[df['BROKTYPE'].isin(TRAIL_TYPES)].copy()
    for c in ['PERCENTAGE','BROKERAGE','AVGASSETS']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df = df[df['PERCENTAGE'] >= 0].copy()

    if df.empty:
        print(f"  [{m['label']}] 0 records after filtering to {m['month']}")
        return 0

    out = pd.DataFrame({
        'registrar':     'Karvy',
        'source_file':   Path(m['file']).name,
        'amc_code':      df['FUND'].astype(str),
        'amc_name':      df['FUNDDESC'],
        'scheme_code':   df['SCHPLN'].astype(str),
        'scheme_name':   df['FUNDDESC'],
        'folio_no':      df['ACCOUNTNO'].astype(str),
        'investor_name': df['INVESTORN0'],
        'investor_pan':  df['INVPAN'].fillna('') if 'INVPAN' in df.columns else '',
        'brokerage_type':df['BROKTYPE'],
        'brokerage_head':df['BROKTYPE'],
        'rate':          df['PERCENTAGE'],
        'brokerage_amt': df['BROKERAGE'],
        'aum':           df['AVGASSETS'],
        'period_from':   df['FROMDATE'].apply(norm_date),
        'period_to':     df['TODATE'].apply(norm_date),
        'proc_date':     df['PROCESSDA3'].apply(norm_date),
        'is_negative':   0,
        'asset_type':    df['ASSETTYPE'].fillna('') if 'ASSETTYPE' in df.columns else '',
    })
    out.to_sql('brokerage_records', conn, if_exists='append', index=False)
    conn.commit()
    print(f"  [{m['label']}] ✓ {len(out):,} records | ₹{out['brokerage_amt'].sum()/100000:.2f}L")
    return len(out)

def load_csv_month(m, conn):
    path = os.path.join(KARVY_DIR, m['file'])
    if not os.path.exists(path):
        print(f"  [{m['label']}] MISSING: {path}")
        return 0

    df = pd.read_csv(path, encoding='latin-1', low_memory=False)
    if df.empty or 'Brokerage Head' not in df.columns:
        print(f"  [{m['label']}] Wrong format or empty")
        return 0

    # Normalise From Date to YYYYMMDD for filtering
    def to_yyyymm(val):
        d = norm_date(val)
        return d.replace('-','')[:6] if d else ''

    df['_ym'] = df['From Date'].apply(to_yyyymm)
    df = df[df['_ym'] == m['month']].copy()
    df = df[df['Brokerage Head'].isin(TRAIL_TYPES)].copy()
    df = df[~df['Brokerage Type'].str.startswith('Not Paid', na=False)].copy()
    for c in ['Percentage (%)','Brokerage (in Rs.)','Average Assets']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df = df[df['Percentage (%)'] >= 0].copy()

    if df.empty:
        print(f"  [{m['label']}] 0 records after filtering to {m['month']}")
        return 0

    out = pd.DataFrame({
        'registrar':     'Karvy',
        'source_file':   Path(m['file']).name,
        'amc_code':      df['Fund'].astype(str),
        'amc_name':      df['Fund Description'],
        'scheme_code':   df['Scheme Code'].astype(str),
        'scheme_name':   df['Fund Description'],
        'folio_no':      df['Account Number'].astype(str),
        'investor_name': df['Investor Name'],
        'investor_pan':  df.get('InvPAN', pd.Series([''] * len(df))).fillna(''),
        'brokerage_type':df['Brokerage Type'],
        'brokerage_head':df['Brokerage Head'],
        'rate':          df['Percentage (%)'],
        'brokerage_amt': df['Brokerage (in Rs.)'],
        'aum':           df['Average Assets'],
        'period_from':   df['From Date'].apply(norm_date),
        'period_to':     df['To Date'].apply(norm_date),
        'proc_date':     df['Process Date'].apply(norm_date),
        'is_negative':   0,
        'asset_type':    df.get('AssetType', pd.Series([''] * len(df))).fillna(''),
    })
    out.to_sql('brokerage_records', conn, if_exists='append', index=False)
    conn.commit()
    print(f"  [{m['label']}] ✓ {len(out):,} records | ₹{out['brokerage_amt'].sum()/100000:.2f}L")
    return len(out)

def main():
    conn = sqlite3.connect(DB_PATH)

    # Clean slate
    n = conn.execute("SELECT COUNT(*) FROM brokerage_records WHERE registrar='Karvy'").fetchone()[0]
    print(f"Deleting {n:,} existing Karvy records...")
    conn.execute("DELETE FROM brokerage_records WHERE registrar='Karvy'")
    conn.execute("DELETE FROM load_log WHERE registrar='Karvy'")
    conn.commit()
    print("Done. Loading fresh...\n")

    total = 0
    for m in MONTHS:
        if m['type'] == 'dbf':
            total += load_dbf_month(m, conn)
        elif m['type'] == 'csv':
            total += load_csv_month(m, conn)

    print(f"\n{'─'*50}")
    print(f"Total Karvy records loaded: {total:,}\n")

    print(pd.read_sql('''
        SELECT strftime('%Y-%m', period_from) as month,
               COUNT(*) as records,
               COUNT(DISTINCT folio_no) as investors,
               ROUND(SUM(brokerage_amt)/100000,2) as lakh
        FROM brokerage_records WHERE registrar='Karvy'
        GROUP BY month ORDER BY month
    ''', conn).to_string(index=False))

    conn.close()

if __name__ == '__main__':
    main()
