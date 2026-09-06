import os, json, base64, random, logging
from datetime import datetime
import pytz, requests, gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutoPilot_Threads")
TZ = pytz.timezone("Asia/Jakarta")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GCP_CREDS_BASE64 = os.environ.get("GCP_CREDS_BASE64")
AI_API_KEY = os.environ.get("AI_API_KEY")

SLOTS = ["08:15", "11:45", "15:30", "18:45", "21:15"]

VIRAL_PROMPTS = [
    "Dilema dunia kerja, lembur, dan overthinking karir usia 20-an",
    "Perdebatan belanja impulsif vs hemat yang selalu berakhir boncos",
    "Curhat realita tinggal di kota besar dan susahnya menabung",
    "Humor linimasa soal tanggal tua dan godaan checkout marketplace"
]

def get_sheets_client():
    creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def is_active_window(sh):
    try:
        cfg = dict(sh.worksheet("Config").get_all_values())
        start_str, end_str = cfg.get("start_datetime", "").strip(), cfg.get("end_datetime", "").strip()
        if not start_str or not end_str:
            return True
        now = datetime.now(TZ)
        start_dt = TZ.localize(datetime.strptime(start_str, "%Y-%m-%d %H:%M"))
        end_dt = TZ.localize(datetime.strptime(end_str, "%Y-%m-%d %H:%M"))
        if not (start_dt <= now <= end_dt):
            logger.info(f"Di luar jadwal aktif ({start_str} s/d {end_str}). Generator berhenti.")
            return False
        return True
    except Exception as e:
        logger.warning(f"Lewati cek Config: {e}")
        return True

def call_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={AI_API_KEY}"
    res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30).json()
    return res["candidates"][0]["content"]["parts"][0]["text"].strip()

def main():
    if not SPREADSHEET_ID or not GCP_CREDS_BASE64 or not AI_API_KEY:
        logger.error("Kredensial tidak lengkap.")
        return

    client = get_sheets_client()
    sh = client.open_by_key(SPREADSHEET_ID)

    if not is_active_window(sh):
        return

    accounts = sh.worksheet("Accounts").get_all_records()
    if not accounts:
        return
    target_account = accounts[0]["name"]

    products_ws = sh.worksheet("Products")
    prod_rows = products_ws.get_all_records()
    ready_prods = [p for p in prod_rows if str(p.get("status")).upper() == "READY"]

    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    data_ws = sh.worksheet("data")
    new_entries = []

    # Slot 1: Postingan Viral (08:15)
    viral_topic = random.choice(VIRAL_PROMPTS)
    prompt_v = f"Tulis 1 postingan Threads bahasa Indonesia gaya santai, relate, dan memancing komentar tentang: {viral_topic}. Maksimal 250 karakter. Jangan pakai hashtag."
    v_text = call_gemini(prompt_v)
    new_entries.append([today_str, SLOTS[0], target_account, v_text, "", "", "", "PENDING", "", "", ""])

    # Slot 2-5: Postingan Affiliate
    sampled = random.sample(ready_prods, min(4, len(ready_prods)))
    for i, prod in enumerate(sampled, start=1):
        prompt_aff = f"Buat hook teks Threads gaya curhat/solutif santai tanpa hard-selling untuk barang '{prod['product_name']}' ({prod.get('highlight', '')}). Maks 250 karakter."
        main_aff = call_gemini(prompt_aff)
        reply_aff = f"Yang mau samaan atau cek racunnya, belinya di sini ya:\n{prod['affiliate_link']}"
        new_entries.append([today_str, SLOTS[i], target_account, main_aff, "", reply_aff, prod["affiliate_link"], "PENDING", "", "", ""])

    for entry in new_entries:
        data_ws.append_row(entry)
    logger.info("Berhasil generate 5 antrean konten Threads.")

if __name__ == "__main__":
    main()
