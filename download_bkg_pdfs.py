"""
download_bkg_pdfs.py — Gmail BKG-STRUCTURE PDF Downloader
Downloads all PDF attachments from BKG-STRUCTURE label to data/bkg-pdfs/
Organizes by AMC name, skips already downloaded files.

Run: python3 download_bkg_pdfs.py
First run opens browser for Google login (one-time only).
"""

import os, base64, re, json, time
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── CONFIG ────────────────────────────────────────────────────────────────────
CREDENTIALS_FILE = "./credentials.json"
TOKEN_FILE        = "./token.json"
OUTPUT_DIR        = "./data/bkg-pdfs"
LABEL_NAME        = "BKG STRUCTURE"
SCOPES            = ["https://www.googleapis.com/auth/gmail.readonly"]

# AMC name detection from subject/sender
AMC_PATTERNS = {
    'HDFC': ['hdfc', 'hdfcfund'],
    'SBI': ['sbi mutual', 'sbimf'],
    'ICICI_Prudential': ['icici prudential', 'icicipruamc'],
    'Aditya_Birla': ['aditya birla', 'adityabirla', 'birlasunlife'],
    'Kotak': ['kotak mahindra', 'kotak mutual', 'kmamc'],
    'DSP': ['dsp asset', 'dsp mutual', 'dspmf'],
    'Franklin': ['franklin templeton', 'franklintem'],
    'Axis': ['axis mutual', 'axis asset', 'axismf'],
    'Mirae': ['mirae asset', 'miraeasset'],
    'UTI': ['uti mutual', 'utimutual', 'kfintech'],
    'Nippon': ['nippon india', 'nipponind'],
    'Edelweiss': ['edelweiss', 'edelweissmf'],
    'Bandhan': ['bandhan mutual', 'bandhan'],
    'PPFAS': ['ppfas', 'parag parikh', 'ppfas.in'],
    'Helios': ['helios mutual', 'helioscapital'],
    'WhiteOak': ['whiteoak', 'white oak'],
    'HSBC': ['hsbc mutual', 'hsbc.co.in'],
    'Union': ['union mutual', 'unionmf'],
    '360ONE': ['360 one', '360one', 'brokerages@360'],
    'Sundaram': ['sundaram mutual', 'sundarammutual'],
    'Canara_Robeco': ['canara robeco', 'canararobeco'],
    'Baroda_BNP': ['baroda bnp', 'barodabnp'],
    'Invesco': ['invesco asset', 'invescoindia'],
    'Motilal_Oswal': ['motilal oswal', 'motilaloswal'],
    'Tata': ['tata asset', 'tatamf', 'tata mutual'],
    'Mahindra_Manulife': ['mahindra manulife'],
    'PGIM': ['pgim india', 'pgimindia'],
    'Quant': ['quant mutual', 'quant.in'],
    'JM_Financial': ['jm financial', 'jmfinancial'],
    'BOI': ['bank of india', 'boi mutual'],
    'ITI': ['iti mutual', 'iti long'],
    'Bajaj_Finserv': ['bajaj finserv', 'bajajfinserv'],
    'Samco': ['samco mutual'],
    'Quantum': ['quantum mutual'],
    'Trust': ['trust asset', 'trustgroup'],
}

def detect_amc(subject, sender):
    text = (subject + ' ' + sender).lower()
    for amc, patterns in AMC_PATTERNS.items():
        if any(p in text for p in patterns):
            return amc
    return 'Unknown'

def sanitize_filename(name):
    return re.sub(r'[^\w\-_\. ]', '_', name)[:100]

def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def get_label_id(service, label_name):
    labels = service.users().labels().list(userId='me').execute().get('labels', [])
    for label in labels:
        if label['name'].upper() == label_name.upper():
            return label['id']
    raise ValueError(f"Label '{label_name}' not found in Gmail")

def download_all_pdfs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("Connecting to Gmail...")
    service = get_gmail_service()
    
    label_id = get_label_id(service, LABEL_NAME)
    print(f"Found label: {LABEL_NAME} ({label_id})")
    
    # Get all message IDs in label
    all_messages = []
    page_token = None
    while True:
        kwargs = {'userId': 'me', 'labelIds': [label_id], 'maxResults': 500}
        if page_token:
            kwargs['pageToken'] = page_token
        result = service.users().messages().list(**kwargs).execute()
        messages = result.get('messages', [])
        all_messages.extend(messages)
        page_token = result.get('nextPageToken')
        if not page_token:
            break
    
    print(f"Total emails found: {len(all_messages)}")
    
    downloaded = 0
    skipped = 0
    no_pdf = 0
    
    for i, msg_ref in enumerate(all_messages):
        try:
            msg = service.users().messages().get(
                userId='me', id=msg_ref['id'], format='full').execute()
            
            # Get subject and sender
            headers = {h['name']: h['value'] for h in msg['payload'].get('headers', [])}
            subject = headers.get('Subject', '')
            sender  = headers.get('From', '')
            date    = headers.get('Date', '')[:16]
            
            amc = detect_amc(subject, sender)
            amc_dir = os.path.join(OUTPUT_DIR, amc)
            os.makedirs(amc_dir, exist_ok=True)
            
            # Find PDF attachments
            def extract_parts(payload):
                parts = []
                if payload.get('mimeType', '').startswith('application/') and \
                   payload.get('filename', '').lower().endswith('.pdf'):
                    parts.append(payload)
                for part in payload.get('parts', []):
                    parts.extend(extract_parts(part))
                return parts
            
            pdf_parts = extract_parts(msg['payload'])
            
            if not pdf_parts:
                no_pdf += 1
                continue
            
            for part in pdf_parts:
                filename = sanitize_filename(part.get('filename', 'attachment.pdf'))
                if not filename.lower().endswith('.pdf'):
                    filename += '.pdf'
                
                # Add date prefix for sorting
                date_prefix = date.replace(' ', '_').replace(':', '').replace(',', '')
                out_path = os.path.join(amc_dir, f"{date_prefix}_{filename}")
                
                if os.path.exists(out_path):
                    skipped += 1
                    continue
                
                # Download attachment
                att_id = part.get('body', {}).get('attachmentId')
                if att_id:
                    att = service.users().messages().attachments().get(
                        userId='me', messageId=msg_ref['id'], id=att_id).execute()
                    data = base64.urlsafe_b64decode(att['data'])
                else:
                    data = base64.urlsafe_b64decode(part['body'].get('data', ''))
                
                with open(out_path, 'wb') as f:
                    f.write(data)
                downloaded += 1
            
            if (i + 1) % 20 == 0:
                print(f"  Processed {i+1}/{len(all_messages)} emails | "
                      f"Downloaded: {downloaded} | Skipped: {skipped} | No PDF: {no_pdf}")
            
            time.sleep(0.1)  # avoid rate limiting
            
        except Exception as e:
            print(f"  Error on message {msg_ref['id']}: {e}")
            continue
    
    print(f"\n{'─'*50}")
    print(f"Done!")
    print(f"PDFs downloaded : {downloaded}")
    print(f"Already existed : {skipped}")
    print(f"No PDF in email : {no_pdf}")
    print(f"\nFiles saved to: {OUTPUT_DIR}/")
    
    # Print summary by AMC
    print("\n=== PDFs by AMC ===")
    for amc_dir in sorted(os.listdir(OUTPUT_DIR)):
        full_path = os.path.join(OUTPUT_DIR, amc_dir)
        if os.path.isdir(full_path):
            count = len([f for f in os.listdir(full_path) if f.endswith('.pdf')])
            if count > 0:
                print(f"  {amc_dir}: {count} PDFs")

if __name__ == '__main__':
    download_all_pdfs()
