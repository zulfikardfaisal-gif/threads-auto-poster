import os
import json
import re
import base64
import random
import logging
from datetime import datetime
import pytz
import requests
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutoPilot_X")

TZ_JAKARTA = pytz.timezone("Asia/Jakarta")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# 5 Slot Waktu Posting Harian di Twitter/X
SCHEDULE_SLOTS = [
    {"time": "08:30", "type": "VIRAL"},
    {"time": "12:00", "type": "AFFILIATE"},
    {"time": "16:00", "type": "AFFILIATE"},
    {"time": "19:00", "type": "AFFILIATE"},
    {"time": "21:30", "type": "AFFILIATE"},
]

VIRAL_TOPICS = [
    "Dilema dunia kerja, lembur, dan overthinking karir usia 20-an",
    "Perdebatan belanja impulsif vs hemat yang selalu berakhir boncos",
    "Curhat realita tinggal di kota besar dan susahnya menabung",
    "Unpopular opinion seputar hubungan sosial dan pertemanan masa kini",
    "Humor linimasa soal tanggal tua dan godaan checkout marketplace",
    "Pilihan hidup karir stabil vs bangun bisnis sendiri yang serba spekulatif"
]

def clean_ascii(text):
    return "".join(c for c in str(text) if 32 <= ord(c) <= 126).strip() if text else ""

def is_valid_affiliate_url(url: str) -> bool:
    if not url:
        return False
    clean = str(url).strip()
    return clean.startswith(("http://", "https://")) and len(clean) >= 12 and "." in clean

def get_creds():
    b64_str = os.getenv("GCP_CREDS_BASE64", "").strip()
    if b64_str:
        return json.loads(base64.b64decode(b64_str).decode("utf-8"))
    if os.path.exists("credentials.json"):
        with open("credentials.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def get_gemini_active_models(api_key: str) -> list:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            models = res.json().get("models", [])
            valid = [m.get("name", "").replace("models/", "") for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
            flash = [m for m in valid if "flash" in m.lower()]
            return flash if flash else valid
    except Exception:
        pass
    return ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

def call_gemini(prompt: str, api_key: str) -> str:
    models = get_gemini_active_models(api_key)
    for m in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.9, "response_mime_type": "application/json"}
        }
        try:
            res = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=25)
            if res.status_code == 200:
                return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            continue
    raise Exception("Model Gemini gagal merespons.")

def parse_json_safely(raw_text: str, expected_count: int):
    try:
        data = json.loads(re.search(r"\{.*\}|\[.*\]", raw_text, re.DOTALL).group(0))
        items = data.get("variations", data.get("items", data)) if isinstance(data, dict) else data
        if isinstance(items, list) and len(items) > 0:
            return items
    except Exception:
        pass
    return [{"main_text": raw_text[:240], "reply_list": []}] * expected_count

def generate_viral_variations(account_count: int, api_key: str) -> list:
    topic = random.choice(VIRAL_TOPICS)
    prompt = (
        f"Kamu kreator Twitter/X Indonesia. Tulis {account_count} variasi tweet santai pemicu diskusi tentang: '{topic}'.\n"
        "SYARAT MUTLAK:\n"
        "- Tiap variasi memiliki hook dan gaya bahasa berbeda.\n"
        "- main_text: Tweet utama WAJIB DI BAWAH 240 KARAKTER, gaya bahasa natural Twitter.\n"
        "- reply_list: Array string berisi 0 sampai 1 balasan singkat lanjutan opini.\n"
        "- DILARANG menyertakan tautan atau jualan barang.\n\n"
        "Format Output WAJIB JSON murni:\n"
        "{\n"
        '  "variations": [\n'
        '    {"main_text": "Teks tweet hook", "reply_list": ["Opini lanjutan"]}\n'
        "  ]\n"
        "}"
    )
    res = call_gemini(prompt, api_key)
    raw_list = parse_json_safely(res, account_count)
    clean_results = []
    for item in raw_list[:account_count]:
        m = re.sub(r"https?://\S+", "", str(item.get("main_text", ""))).strip()[:240]
        r_raw = item.get("reply_list", [])
        r_list = [re.sub(r"https?://\S+", "", str(x)).strip()[:240] for x in r_raw if str(x).strip()] if isinstance(r_raw, list) else []
        clean_results.append({"main_text": m, "reply_text": " ||| ".join(r_list)})
    
    while len(clean_results) < account_count:
        clean_results.append(clean_results[0])
    return clean_results

def generate_affiliate_variations(name: str, notes: str, link: str, account_count: int, api_key: str) -> list:
    prompt = (
        f"Kamu affiliate marketer Twitter/X Indonesia. Tulis {account_count} variasi tweet rekomendasi produk:\n"
        f"- Produk: {name}\n"
        f"- Keunggulan: {notes if notes else 'Kualitas bagus banget dan worth it'}\n\n"
        "SYARAT MUTLAK:\n"
        "- Tiap variasi sudut pandangnya unik (ada review jujur, spill diskon, solusi harian).\n"
        "- main_text: WAJIB DI BAWAH 240 KARAKTER, gaya racun santai Twitter (bukan hard selling).\n"
        "- reply_list: Array string berisi 1 balasan ulasan pelengkap.\n"
        "- DILARANG menuliskan link di dalam teks prompt.\n\n"
        "Format Output WAJIB JSON murni:\n"
        "{\n"
        '  "variations": [\n'
        '    {"main_text": "Teks racun tweet utama", "reply_list": ["Ulasan tambahan"]}\n'
        "  ]\n"
        "}"
    )
    res = call_gemini(prompt, api_key)
    raw_list = parse_json_safely(res, account_count)
    clean_results = []
    clean_link = clean_ascii(link)

    for item in raw_list[:account_count]:
        m = re.sub(r"https?://\S+|s\.shopee\.co\.id/\S+", "", str(item.get("main_text", ""))).strip()[:240]
        r_raw = item.get("reply_list", [])
        r_list = [re.sub(r"https?://\S+", "", str(x)).strip()[:200] for x in r_raw if str(x).strip()] if isinstance(r_raw, list) else []
        
        if not r_list:
            r_list = ["Jujur ini worth it banget buat dicoba."]
            
        if is_valid_affiliate_url(clean_link):
            r_list[-1] = f"{r_list[-1]}\n\n👉 Link beli di sini: {clean_link}".strip()

        clean_results.append({"main_text": m, "reply_text": " ||| ".join(r_list)})

    while len(clean_results) < account_count:
        clean_results.append(clean_results[0])
    return clean_results

def main():
    creds_dict = get_creds()
    sheet_id = clean_ascii(os.getenv("SPREADSHEET_ID", ""))
    ai_key = clean_ascii(os.getenv("AI_API_KEY", ""))

    if not all([creds_dict, sheet_id, ai_key]):
        logger.error("Kredensial atau API Key tidak lengkap.")
        return

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    # 1. Buka Tab Accounts_X
    try:
        ws_acc = sh.worksheet("Accounts_X")
        acc_rows = ws_acc.get_all_values()[1:]
        accounts = [str(r[0]).strip() for r in acc_rows if len(r) >= 5 and str(r[0]).strip() and str(r[1]).strip()]
    except Exception as e:
        logger.error(f"Gagal membaca tab Accounts_X: {e}")
        return

    if not accounts:
        accounts = ["ALL"]
    acc_count = len(accounts)

    # 2. Buka Tab Products_X & data_x
    try:
        ws_prod_x = sh.worksheet("Products_X")
        ws_data_x = sh.worksheet("data_x")
    except Exception as e:
        logger.error(f"Tab 'Products_X' atau 'data_x' tidak ditemukan di Google Sheets: {e}")
        return

    prod_rows = ws_prod_x.get_all_values()
    if len(prod_rows) <= 1:
        logger.warning("Tab Products_X masih kosong.")
        return

    # 3. Filter produk READY yang punya link valid
    ready_indices = []
    for idx, r in enumerate(prod_rows[1:], start=2):
        if len(r) >= 3 and r[0].strip():
            raw_link = r[2].strip() if len(r) > 2 else ""
            status_val = r[4].strip().upper() if len(r) > 4 else ""
            if is_valid_affiliate_url(raw_link) and status_val != "USED":
                ready_indices.append(idx)

    # Looping: Jika produk tersisa < 4, reset menjadi READY
    if len(ready_indices) < 4:
        logger.info("Produk READY di Products_X tersisa kurang dari 4. Me-reset status menjadi READY...")
        for i in range(2, len(prod_rows) + 1):
            r = prod_rows[i - 1]
            raw_l = r[2].strip() if len(r) > 2 else ""
            if is_valid_affiliate_url(raw_l):
                ws_prod_x.update_cell(i, 5, "READY")
        
        ready_indices = [
            i for i in range(2, len(prod_rows) + 1)
            if is_valid_affiliate_url(prod_rows[i - 1][2].strip() if len(prod_rows[i - 1]) > 2 else "")
        ]

    if len(ready_indices) == 0:
        logger.error("Tidak ada produk valid di tab Products_X.")
        return

    chosen_indices = ready_indices[:4]
    chosen_products = []
    for idx in chosen_indices:
        r = prod_rows[idx - 1]
        chosen_products.append({
            "row_idx": idx,
            "name": r[0].strip(),
            "notes": r[1].strip() if len(r) > 1 else "",
            "link": r[2].strip(),
            "img": r[3].strip() if len(r) > 3 else ""
        })

    # 4. Susun Konten Hari Ini
    now = datetime.now(TZ_JAKARTA)
    today_str = now.strftime("%Y-%m-%d")
    new_rows = []
    prod_idx = 0

    for slot in SCHEDULE_SLOTS:
        slot_time = slot["time"]
        if slot["type"] == "VIRAL":
            logger.info(f"Membuat tweet VIRAL jam {slot_time}...")
            viral_vars = generate_viral_variations(acc_count, ai_key)
            for i, acc_name in enumerate(accounts):
                new_rows.append([
                    today_str, slot_time, acc_name, viral_vars[i]["main_text"], "",
                    viral_vars[i]["reply_text"], "Viral Booster (Non-Affiliate)", "PENDING", "", "", ""
                ])
        else:
            if prod_idx >= len(chosen_products):
                break
            p = chosen_products[prod_idx]
            prod_idx += 1
            logger.info(f"Membuat tweet AFFILIATE untuk '{p['name']}' jam {slot_time}...")
            aff_vars = generate_affiliate_variations(p["name"], p["notes"], p["link"], acc_count, ai_key)
            for i, acc_name in enumerate(accounts):
                new_rows.append([
                    today_str, slot_time, acc_name, aff_vars[i]["main_text"], p["img"],
                    aff_vars[i]["reply_text"], p["link"], "PENDING", "", "", ""
                ])
            ws_prod_x.update_cell(p["row_idx"], 5, "USED")

    # 5. Simpan ke tab data_x
    ws_data_x.append_rows(new_rows)
    logger.info(f"Selesai! {len(new_rows)} antrean tweet berhasil dijadwalkan ke tab data_x!")

if __name__ == "__main__":
    main()
