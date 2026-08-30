import os
import sys
import json
import time
import re
import base64
import logging
from datetime import datetime, timedelta
import pytz
import pandas as pd
import streamlit as st
import requests
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv, set_key
from apscheduler.schedulers.background import BackgroundScheduler

# ==========================================
# 1. KONFIGURASI LOGGING & TIMEZONE
# ==========================================
LOG_FILE = "app_activity.log"
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("ThreadsHub")

load_dotenv(ENV_PATH, override=True)
TZ_JAKARTA = pytz.timezone("Asia/Jakarta")

def clean_ascii_str(text: str) -> str:
    """Membersihkan seluruh whitespace tersembunyi / non-ascii"""
    if not text:
        return ""
    return re.sub(r"[^\x20-\x7E]", "", str(text)).strip()

def get_config_val(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return clean_ascii_str(str(st.secrets[key]))
    except Exception:
        pass
    return clean_ascii_str(os.getenv(key, default))

def get_gcp_credentials_dict():
    """Parser Kredensial GCP (Base64, Direct File, String JSON, atau TOML Table)"""
    # 1. Prioritas Utama: Base64 1 Baris
    try:
        if "GCP_CREDS_BASE64" in st.secrets:
            b64_str = str(st.secrets["GCP_CREDS_BASE64"]).strip()
            decoded_json = base64.b64decode(b64_str).decode("utf-8")
            return json.loads(decoded_json)
    except Exception as e:
        logger.warning(f"Parser Base64: {e}")

    # 2. File credentials.json lokal
    if os.path.exists("credentials.json"):
        try:
            with open("credentials.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Parser credentials.json: {e}")

    # 3. String JSON GCP_SERVICE_ACCOUNT
    try:
        if "GCP_SERVICE_ACCOUNT" in st.secrets:
            raw = st.secrets["GCP_SERVICE_ACCOUNT"]
            if isinstance(raw, str):
                raw_str = raw.strip()
                if raw_str.startswith("{") and raw_str.endswith("}"):
                    cd = json.loads(raw_str)
                    if "private_key" in cd:
                        cd["private_key"] = cd["private_key"].replace("\\n", "\n")
                    return cd
            elif isinstance(raw, dict):
                cd = dict(raw)
                if "private_key" in cd:
                    cd["private_key"] = cd["private_key"].replace("\\n", "\n")
                return cd
    except Exception as e:
        logger.warning(f"Parser GCP_SERVICE_ACCOUNT: {e}")

    # 4. Format Table [gcp_service_account]
    try:
        if "gcp_service_account" in st.secrets:
            cd = dict(st.secrets["gcp_service_account"])
            if "private_key" in cd:
                cd["private_key"] = cd["private_key"].replace("\\n", "\n")
            return cd
    except Exception as e:
        logger.warning(f"Parser gcp_service_account: {e}")

    return None

def extract_and_parse_json(raw_str: str, default_count: int = 3):
    if not raw_str or not str(raw_str).strip():
        return [{"angle": f"Variasi #{i+1}", "main_text": "Rekomendasi produk terbaik untukmu!", "replies": []} for i in range(default_count)]
    
    text = str(raw_str).strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    
    bt3 = chr(96) * 3
    if bt3 in text:
        text = text.replace(bt3 + "json", "").replace(bt3 + "JSON", "").replace(bt3, "")
    text = text.strip()
    
    f_bracket, l_bracket = text.find('['), text.rfind(']')
    f_brace, l_brace = text.find('{'), text.rfind('}')
    
    candidate = text
    if f_bracket != -1 and l_bracket != -1 and (f_brace == -1 or f_bracket < f_brace):
        candidate = text[f_bracket:l_bracket+1]
    elif f_brace != -1 and l_brace != -1:
        candidate = text[f_brace:l_brace+1]
        
    try:
        data = json.loads(candidate, strict=False)
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                return data["items"]
            elif "threads" in data and isinstance(data["threads"], list):
                return data["threads"]
            return [data]
        elif isinstance(data, list):
            return data
    except Exception:
        pass

    blocks = [b.strip() for b in text.split("\n\n") if len(b.strip()) > 20]
    fallback_res = []
    for idx, b in enumerate(blocks[:default_count]):
        fallback_res.append({
            "angle": f"Variasi #{idx+1}",
            "main_text": b[:400],
            "replies": []
        })
    return fallback_res if fallback_res else [{"angle": "Konten Utama", "main_text": text[:400], "replies": []}]

# ==========================================
# 2. HELPER DATA MULTI-AKUN (GOOGLE SHEETS)
# ==========================================
def get_accounts_worksheet():
    try:
        s_id = clean_ascii_str(get_config_val("SPREADSHEET_ID"))
        creds_dict = get_gcp_credentials_dict()
        if not s_id or not creds_dict:
            return None
        creds = Credentials.from_service_account_info(creds_dict, scopes=SheetsManager.SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(s_id)
        try:
            return spreadsheet.worksheet("Accounts")
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title="Accounts", rows=50, cols=5)
            ws.append_row(["name", "user_id", "access_token"])
            return ws
    except Exception as e:
        logger.warning(f"Akses tab Accounts: {e}")
        return None

def load_accounts() -> list:
    try:
        ws = get_accounts_worksheet()
        if ws:
            records = ws.get_all_records()
            return [r for r in records if str(r.get("user_id", "")).strip() != ""]
    except Exception:
        pass
            
    try:
        if "ACCOUNTS_JSON" in st.secrets:
            val = st.secrets["ACCOUNTS_JSON"]
            return json.loads(val) if isinstance(val, str) else val
    except Exception:
        pass
    return []

def save_new_account_to_sheets(name: str, user_id: str, access_token: str):
    ws = get_accounts_worksheet()
    if ws:
        ws.append_row([clean_ascii_str(name), clean_ascii_str(user_id), clean_ascii_str(access_token)])
    else:
        raise Exception("Gagal terhubung ke Google Sheets. Pastikan Service Account sudah dijadikan Editor.")

def delete_account_from_sheets(name: str):
    ws = get_accounts_worksheet()
    if ws:
        records = ws.get_all_records()
        for i, r in enumerate(records, start=2):
            if str(r.get("name", "")).strip() == name:
                ws.delete_rows(i)
                break

# ==========================================
# 3. UNIVERSAL AI ENGINE (LIVE MODEL DISCOVERY)
# ==========================================
STYLE_PROMPTS = {
    "🤖 Otomatis (AI Pintar Memilih)": "Pilihkan sudut pandang dan tone paling persuasif untuk memicu klik dan konversi affiliate.",
    "📖 Storytelling / Curhat Personal": "Gunakan sudut pandang orang pertama (pengalaman pribadi/curhat santai).",
    "🔥 Spill Racun Diskon & FOMO": "Gaya bersemangat, racun Shopee, fokus ke voucher diskon, harga miring, dan stok terbatas.",
    "🧐 Review Edukatif & Bedah Fitur": "Gaya objektif, bedah spesifikasi bahan/material, dan alasan kenapa produk ini sangat worth it.",
    "✨ Aesthetic & Lifestyle Vibe": "Gaya santai, estetik, hangat, fokus pada visual kenyamanan gaya hidup.",
    "🤣 Humor & Bahasa Gaul Santai": "Gaya santai linimasa Threads Indonesia, sedikit bercanda dan mengundang interaksi netizen."
}

LENGTH_CONSTRAINTS = {
    "Pendek (Punchy / 100-180 Karakter)": "Maksimal 180 karakter per post/reply, padat dan to the point.",
    "Sedang (Standar / 200-350 Karakter)": "Antara 200 hingga 350 karakter per post/reply, penjelasan mengalir jelas.",
    "Panjang (Storytelling / 400-480 Karakter)": "Antara 400 hingga 480 karakter per post/reply (Maks 500 batas Threads), deskriptif dan mendalam."
}

def get_groq_active_models(clean_key: str) -> list:
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {clean_key}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json().get("data", [])
            valid_models = [
                m["id"] for m in data 
                if m.get("active", True) and not any(x in m["id"].lower() for x in ["whisper", "guard", "vision", "audio", "embed", "tts", "moderation"])
            ]
            if valid_models:
                return valid_models
    except Exception as e:
        logger.warning(f"Gagal mengambil model dinamis Groq: {e}")
    return []

def call_groq_api(prompt: str, api_key: str) -> str:
    clean_key = clean_ascii_str(api_key)
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {clean_key}",
        "Content-Type": "application/json"
    }
    
    models = get_groq_active_models(clean_key)
    if not models:
        models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen-2.5-32b", "gemma2-9b-it"]

    err_list = []
    
    for m in models:
        payload = {
            "model": m,
            "messages": [
                {"role": "system", "content": "You are a top Indonesian social media affiliate copywriter. Output valid JSON strictly adhering to instructions."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                data = res.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content:
                    return content
            err_list.append(f"[{m}]: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            err_list.append(f"[{m}]: {str(e)}")
            continue

    for m in models:
        try:
            payload_raw = {
                "model": m,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7
            }
            res = requests.post(url, headers=headers, json=payload_raw, timeout=25)
            if res.status_code == 200:
                data = res.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content:
                    return content
        except Exception:
            continue

    raise Exception(f"Gagal memanggil Groq AI: {' | '.join(err_list)}")

def call_gemini_rest(prompt: str, api_key: str) -> str:
    clean_key = clean_ascii_str(api_key)
    models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
    err_list = []
    for m in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={clean_key}"
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.7}}
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            err_list.append(f"[{m}]: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            err_list.append(f"[{m}]: {str(e)}")
            continue
    raise Exception(f"Gagal memanggil Gemini: {' | '.join(err_list)}")

def call_ai_engine(prompt: str, key_override: str = None) -> str:
    raw_key = key_override.strip() if key_override else get_config_val("AI_API_KEY", get_config_val("GROQ_API_KEY", get_config_val("GEMINI_API_KEY")))
    key = clean_ascii_str(raw_key)
    if not key:
        raise ValueError("API Key belum disetel!")

    if key.startswith("gsk_"):
        return call_groq_api(prompt, key)
    elif key.startswith("AIzaSy"):
        return call_gemini_rest(prompt, key)
    else:
        try:
            return call_groq_api(prompt, key)
        except Exception:
            return call_gemini_rest(prompt, key)

def generate_bulk_single_product_threads(product_name: str, product_notes: str, affiliate_link: str, style_choice: str, length_choice: str, reply_count: int, count: int = 3) -> list:
    style_inst = STYLE_PROMPTS.get(style_choice, "")
    len_inst = LENGTH_CONSTRAINTS.get(length_choice, "Maksimal 350 karakter.")
    
    prompt = (
        "Bertindaklah sebagai Copywriter Top Tier spesialis Threads Indonesia & Shopee Affiliate.\n"
        f"Buatkan {count} buah Utas (Thread) yang BERBEDA SUDUT PANDANG & HOOK untuk produk:\n"
        f"- Nama Produk: {product_name}\n"
        f"- Catatan/Spesifikasi: {product_notes if product_notes else 'Produk viral terlaris, kualitas terjamin'}\n"
        f"- Gaya Penulisan: {style_inst}\n"
        f"- Batasan Panjang Teks: {len_inst}\n"
        f"- Jumlah Balasan (Reply) per Post: {reply_count} balasan (di luar post utama).\n\n"
        "Format JSON wajib memiliki root 'items':\n"
        "{\n"
        '  "items": [\n'
        "    {\n"
        '      "angle": "Sudut Pandang / Variasi",\n'
        f'      "main_text": "Teks post utama hook ({len_inst})",\n'
        f'      "replies": ["Teks balasan 1 ({len_inst})"]\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    raw_text = call_ai_engine(prompt)
    items = extract_and_parse_json(raw_text, default_count=count)
    
    processed_items = []
    for item in items:
        main_txt = item.get("main_text", "")
        reps = item.get("replies", [])[:reply_count]
        
        if reply_count == 0:
            if affiliate_link:
                main_txt = f"{main_txt}\n\n👉 Beli di Shopee: {affiliate_link}"
            reps = []
        else:
            if affiliate_link:
                cta = f"👉 Beli di Shopee: {affiliate_link}"
                if len(reps) > 0:
                    reps[-1] = f"{reps[-1]}\n\n{cta}"
                else:
                    reps.append(cta)
        
        processed_items.append({
            "angle": item.get("angle", "Variasi Konten"),
            "main_text": main_txt,
            "replies": reps
        })
        
    return processed_items

def generate_curated_listicle_thread(curation_topic: str, items: list, length_choice: str) -> dict:
    len_inst = LENGTH_CONSTRAINTS.get(length_choice, "Maksimal 350 karakter.")
    items_text = "\n".join([f"- Item #{i+1}: {it['name']} | Catatan: {it['desc']} | Link: {it['link']}" for i, it in enumerate(items)])
    
    prompt = (
        "Bertindaklah sebagai Copywriter Top Tier spesialis Threads Indonesia.\n"
        f"Buatkan 1 Utas Kurasi Rekomendasi/Top List bertema: \"{curation_topic}\".\n\n"
        f"Daftar Produk:\n{items_text}\n\n"
        "Instruksi:\n"
        f"- main_text: Hook pembuka rekomendasi ({len_inst}).\n"
        f"- replies: Array di mana setiap elemen HANYA membahas 1 Item secara runtut ({len_inst}), diakhiri link Shopee masing-masing.\n\n"
        "Format JSON wajib:\n"
        "{\n"
        '  "main_text": "...",\n'
        '  "replies": [\n'
        '    "Ulasan Item 1...\\n\\nLink Shopee: ...",\n'
        '    "Ulasan Item 2...\\n\\nLink Shopee: ..."\n'
        "  ]\n"
        "}"
    )
    raw_text = call_ai_engine(prompt)
    res = extract_and_parse_json(raw_text, default_count=1)
    return res[0] if isinstance(res, list) and len(res) > 0 else res

# ==========================================
# 4. CLIENT MODULE: THREADS API
# ==========================================
class ThreadsAPI:
    BASE_URL = "https://graph.threads.net/v1.0"

    def __init__(self, user_id: str, access_token: str):
        self.user_id = clean_ascii_str(user_id)
        self.access_token = clean_ascii_str(access_token)

    def test_connection(self) -> dict:
        url = f"{self.BASE_URL}/me"
        params = {"fields": "id,username,name", "access_token": self.access_token}
        res = requests.get(url, params=params, timeout=15)
        return res.json()

    def _wait_for_container(self, container_id: str, max_retries: int = 10, delay: int = 3) -> bool:
        url = f"{self.BASE_URL}/{container_id}"
        params = {"fields": "status,error_message", "access_token": self.access_token}
        for _ in range(max_retries):
            try:
                res = requests.get(url, params=params, timeout=15)
                data = res.json()
                if data.get("status") == "FINISHED":
                    return True
                elif data.get("status") == "ERROR":
                    raise Exception(f"Container Error: {data.get('error_message')}")
                time.sleep(delay)
            except Exception as e:
                if "Container Error" in str(e):
                    raise e
                time.sleep(delay)
        return True

    def create_container(self, text: str = "", image_url: str = None, reply_to_id: str = None) -> str:
        url = f"{self.BASE_URL}/{self.user_id}/threads"
        payload = {"access_token": self.access_token}

        if text:
            payload["text"] = text[:500]

        if image_url and str(image_url).strip().startswith(("http://", "https://")):
            payload["media_type"] = "IMAGE"
            payload["image_url"] = str(image_url).strip()
        else:
            payload["media_type"] = "TEXT"

        if reply_to_id:
            payload["reply_to_id"] = clean_ascii_str(reply_to_id)

        res = requests.post(url, data=payload, timeout=20)
        res_data = res.json()
        if "id" in res_data:
            return res_data["id"]
        else:
            error_msg = res_data.get("error", {}).get("message", str(res_data))
            raise Exception(f"Gagal create container: {error_msg}")

    def publish_container(self, container_id: str) -> str:
        self._wait_for_container(container_id)
        url = f"{self.BASE_URL}/{self.user_id}/threads_publish"
        payload = {"creation_id": container_id, "access_token": self.access_token}
        res = requests.post(url, data=payload, timeout=20)
        res_data = res.json()
        if "id" in res_data:
            return res_data["id"]
        else:
            error_msg = res_data.get("error", {}).get("message", str(res_data))
            raise Exception(f"Gagal publish container: {error_msg}")

    def post_thread_cascade(self, main_text: str, image_url: str = None, replies: list = None) -> list:
        published_ids = []
        main_cid = self.create_container(text=main_text, image_url=image_url)
        time.sleep(2)
        current_parent_id = self.publish_container(main_cid)
        published_ids.append(current_parent_id)

        if replies:
            for r_text in replies:
                if not str(r_text).strip():
                    continue
                time.sleep(3)
                r_cid = self.create_container(text=r_text.strip(), reply_to_id=current_parent_id)
                time.sleep(2)
                r_id = self.publish_container(r_cid)
                published_ids.append(r_id)
                current_parent_id = r_id

        return published_ids

def broadcast_post(all_registered_accounts: list, target_account_str: str, main_text: str, image_url: str = None, replies: list = None) -> list:
    target_str = clean_ascii_str(target_account_str)
    if target_str == "ALL" or not target_str:
        selected_accounts = all_registered_accounts
    else:
        target_names = [t.strip() for t in target_str.split(",") if t.strip()]
        selected_accounts = [a for a in all_registered_accounts if a.get("name") in target_names]

    if not selected_accounts:
        return [{"name": target_str, "success": False, "post_ids": [], "error": "Akun target tidak ditemukan"}]

    results = []
    for acc in selected_accounts:
        name = acc.get("name", "Unknown")
        uid = acc.get("user_id")
        tok = acc.get("access_token")
        try:
            client = ThreadsAPI(uid, tok)
            post_ids = client.post_thread_cascade(main_text, image_url, replies)
            results.append({"name": name, "success": True, "post_ids": post_ids, "error": ""})
            logger.info(f"✅ Post terbit di '{name}' (IDs: {post_ids})")
        except Exception as e:
            results.append({"name": name, "success": False, "post_ids": [], "error": str(e)})
            logger.error(f"❌ Post gagal di '{name}': {e}")
    return results

# ==========================================
# 5. CLIENT MODULE: GOOGLE SHEETS
# ==========================================
class SheetsManager:
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    HEADERS = [
        "schedule_date", "schedule_time", "target_accounts", "main_text", "main_image_url",
        "reply_text", "affiliate_link", "status", "posted_at", "threads_post_id", "error_log"
    ]

    def __init__(self, creds_path: str, spreadsheet_id: str, sheet_name: str = "Sheet1"):
        self.creds_path = creds_path
        self.spreadsheet_id = clean_ascii_str(spreadsheet_id)
        self.sheet_name = clean_ascii_str(sheet_name)
        self.sheet = self._connect()

    def _connect(self):
        creds_dict = get_gcp_credentials_dict()
        if creds_dict:
            creds = Credentials.from_service_account_info(creds_dict, scopes=self.SCOPES)
        elif os.path.exists(self.creds_path):
            creds = Credentials.from_service_account_file(self.creds_path, scopes=self.SCOPES)
        else:
            raise FileNotFoundError("Kredensial GCP tidak ditemukan.")

        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(self.spreadsheet_id)
        try:
            ws = spreadsheet.worksheet(self.sheet_name)
            row1 = ws.row_values(1)
            if "target_accounts" not in row1:
                ws.insert_cols([["target_accounts"]], col=3)
            return ws
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=self.sheet_name, rows=100, cols=15)
            ws.append_row(self.HEADERS)
            return ws

    def get_all_rows(self) -> pd.DataFrame:
        data = self.sheet.get_all_records()
        if not data:
            return pd.DataFrame(columns=self.HEADERS)
        df = pd.DataFrame(data)
        for col in self.HEADERS:
            if col not in df.columns:
                df[col] = ""
        df["_row_number"] = range(2, len(df) + 2)
        return df

    def append_row(self, row_data: dict):
        ordered_vals = [
            row_data.get("schedule_date", ""),
            row_data.get("schedule_time", ""),
            row_data.get("target_accounts", "ALL"),
            row_data.get("main_text", ""),
            row_data.get("main_image_url", ""),
            row_data.get("reply_text", ""),
            row_data.get("affiliate_link", ""),
            row_data.get("status", "PENDING"),
            row_data.get("posted_at", ""),
            row_data.get("threads_post_id", ""),
            row_data.get("error_log", "")
        ]
        self.sheet.append_row(ordered_vals)

    def append_rows_batch(self, rows_data_list: list):
        rows_to_insert = []
        for r in rows_data_list:
            rows_to_insert.append([
                r.get("schedule_date", ""),
                r.get("schedule_time", ""),
                r.get("target_accounts", "ALL"),
                r.get("main_text", ""),
                r.get("main_image_url", ""),
                r.get("reply_text", ""),
                r.get("affiliate_link", ""),
                r.get("status", "PENDING"),
                r.get("posted_at", ""),
                r.get("threads_post_id", ""),
                r.get("error_log", "")
            ])
        if rows_to_insert:
            self.sheet.append_rows(rows_to_insert)

    def update_cell_value(self, row_num: int, col_name: str, value: str):
        col_idx = self.HEADERS.index(col_name) + 1
        self.sheet.update_cell(row_num, col_idx, str(value))

    def delete_row(self, row_num: int):
        self.sheet.delete_rows(row_num)

# ==========================================
# 6. BACKGROUND SCHEDULER ENGINE
# ==========================================
def run_scheduler_job():
    accounts = load_accounts()
    sheet_id = get_config_val("SPREADSHEET_ID")
    sheet_name = get_config_val("SHEET_NAME", "Sheet1")
    creds_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")

    if not accounts or not sheet_id:
        return

    try:
        sheets = SheetsManager(creds_json, sheet_id, sheet_name)
        df = sheets.get_all_rows()
        now = datetime.now(TZ_JAKARTA)

        pending_items = df[df["status"].astype(str).str.upper() == "PENDING"]
        if pending_items.empty:
            return

        for _, row in pending_items.iterrows():
            row_num = int(row["_row_number"])
            d_str = str(row["schedule_date"]).strip()
            t_str = str(row["schedule_time"]).strip()

            try:
                sched_dt = TZ_JAKARTA.localize(datetime.strptime(f"{d_str} {t_str}", "%Y-%m-%d %H:%M"))
            except Exception as e:
                sheets.update_cell_value(row_num, "status", "FAILED")
                sheets.update_cell_value(row_num, "error_log", f"Format Tanggal Salah: {e}")
                continue

            if now >= sched_dt:
                logger.info(f"[SCHEDULER] ⏳ Mengeksekusi baris #{row_num}...")
                main_txt = str(row["main_text"]).strip()
                img_url = str(row["main_image_url"]).strip()
                raw_reply = str(row["reply_text"]).strip()
                target_acc = str(row.get("target_accounts", "ALL")).strip()

                replies = [r.strip() for r in raw_reply.split("|||") if r.strip()]

                broadcast_res = broadcast_post(accounts, target_acc, main_txt, img_url if img_url else None, replies)
                success_list = [f"{r['name']}: {','.join(r['post_ids'])}" for r in broadcast_res if r["success"]]
                failed_list = [f"{r['name']}: {r['error']}" for r in broadcast_res if not r["success"]]

                if success_list:
                    new_status = "POSTED" if not failed_list else "PARTIAL"
                    sheets.update_cell_value(row_num, "status", new_status)
                    sheets.update_cell_value(row_num, "posted_at", now.strftime("%Y-%m-%d %H:%M:%S"))
                    sheets.update_cell_value(row_num, "threads_post_id", " | ".join(success_list))
                    sheets.update_cell_value(row_num, "error_log", " | ".join(failed_list) if failed_list else "")
                else:
                    sheets.update_cell_value(row_num, "status", "FAILED")
                    sheets.update_cell_value(row_num, "error_log", " | ".join(failed_list))

    except Exception as e:
        logger.error(f"[SCHEDULER ERROR] {e}")

@st.cache_resource
def initialize_background_scheduler():
    scheduler = BackgroundScheduler(timezone=TZ_JAKARTA)
    scheduler.add_job(run_scheduler_job, "interval", minutes=2, id="threads_broadcaster", replace_existing=True)
    scheduler.start()
    return scheduler

initialize_background_scheduler()

# ==========================================
# 7. STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Threads Shopee Auto-Poster Hub", layout="wide", page_icon="🚀")

st.title("🚀 Threads Multi-Account & AI Studio")
st.markdown("Otomasi Shopee Affiliate: Batch Single Produk, Targeted Niche, Multi-Link Listicle, dan Penjadwalan Terdistribusi.")

active_accounts = load_accounts()
account_names_list = [a["name"] for a in active_accounts]

with st.sidebar:
    st.header("🤖 Engine Status")
    st.success("🟢 Scheduler Aktif (Interval: 2 Menit)")
    st.info(f"👥 Akun Terdaftar: **{len(active_accounts)} Akun**")
    server_time = datetime.now(TZ_JAKARTA).strftime("%Y-%m-%d %H:%M:%S WIB")
    st.write(f"⏰ **Waktu Server:**\n`{server_time}`")
    st.divider()
    if st.button("🔄 Eksekusi Scheduler Sekarang", use_container_width=True):
        with st.spinner("Memproses antrean..."):
            run_scheduler_job()
            st.toast("Antrean berhasil diproses!", icon="✅")
            st.rerun()

tab_studio, tab_queue, tab_accounts, tab_settings, tab_logs = st.tabs([
    "📝 Content Studio", "📊 Queue & Sheets", "👥 Multi-Account", "⚙️ Konfigurasi & AI Key", "📋 System Logs"
])

# ----------------------------------------------------
# TAB 1: CONTENT STUDIO
# ----------------------------------------------------
with tab_studio:
    if not active_accounts:
        st.warning("⚠️ Belum ada akun Threads terdaftar. Silakan tambahkan di tab **👥 Multi-Account**.")
    
    subtab_single_prod, subtab_curation = st.tabs([
        "🛍️ Single Produk (Multi-Konten & Auto-Jadwal)", 
        "🏆 Kurasi Multi-Produk (Top 3/5 Rekomendasi + Multi-Link)"
    ])

    with subtab_single_prod:
        st.subheader("Otomasi 1 Produk Menjadi Banyak Konten Berbeda Sudut Pandang")
        
        c_tgt1, c_tgt2, c_tgt3 = st.columns([1.5, 1, 1])
        with c_tgt1:
            target_scope = st.selectbox(
                "🎯 Target Akun Publikasi:",
                options=["ALL (Cross-Post Semua Akun)"] + account_names_list,
                help="Pilih apakah ingin diposting ke semua akun atau akun niche tertentu."
            )
            selected_target_str = "ALL" if target_scope.startswith("ALL") else target_scope
        with c_tgt2:
            len_choice = st.selectbox("📏 Panjang Postingan:", list(LENGTH_CONSTRAINTS.keys()), index=1)
        with c_tgt3:
            rep_choice = st.selectbox("🧵 Jumlah Reply per Post:", options=[0, 1, 2, 3, 4, 5], index=1)

        col_p1, col_p2 = st.columns([1.2, 1.8])
        with col_p1:
            p_name = st.text_input("Nama Produk", placeholder="Contoh: ESQA Minimalist Blurring Serum Skin Tint")
            p_link = st.text_input("Link Shopee Affiliate", placeholder="https://s.shopee.co.id/xxxx")
            p_img = st.text_input("URL Gambar (Opsional)", placeholder="https://domain.com/foto.jpg")
            p_style = st.selectbox("Gaya Penulisan AI:", list(STYLE_PROMPTS.keys()))
        with col_p2:
            p_notes = st.text_area("Catatan / Keunggulan Produk:", placeholder="Ringan, SPF 35, menyamarkan pori...", height=90)
            
            c_opt1, c_opt2, c_opt3, c_opt4 = st.columns([1, 1.2, 1.2, 1.2])
            with c_opt1:
                p_qty = st.number_input("Jumlah Konten:", min_value=1, max_value=15, value=3, step=1)
            with c_opt2:
                p_interval = st.selectbox("Jeda Antar Post:", options=[1, 2, 3, 4, 6, 8, 12, 24], index=1, format_func=lambda x: f"Setiap {x} Jam" if x < 24 else "Setiap 1 Hari")
            with c_opt3:
                p_start_date = st.date_input("Mulai Tanggal:", value=datetime.now(TZ_JAKARTA).date())
            with c_opt4:
                p_start_time = st.time_input("Mulai Jam:", value=datetime.now(TZ_JAKARTA).time())

        if st.button(f"🪄 Generate {p_qty} Konten Variatif & Siapkan Jadwal", use_container_width=True, type="primary"):
            if not p_name.strip():
                st.error("Nama produk wajib diisi!")
            else:
                with st.spinner(f"AI sedang meracik {p_qty} variasi postingan..."):
                    try:
                        batch_res = generate_bulk_single_product_threads(p_name, p_notes, p_link, p_style, len_choice, rep_choice, p_qty)
                        
                        start_base_dt = TZ_JAKARTA.localize(datetime.combine(p_start_date, p_start_time))
                        scheduled_batch = []
                        
                        for idx, item in enumerate(batch_res):
                            post_dt = start_base_dt + timedelta(hours=(idx * p_interval))
                            scheduled_batch.append({
                                "schedule_date": post_dt.strftime("%Y-%m-%d"),
                                "schedule_time": post_dt.strftime("%H:%M"),
                                "target_accounts": selected_target_str,
                                "angle": item.get("angle", f"Konten #{idx+1}"),
                                "main_text": item.get("main_text", ""),
                                "main_image_url": p_img,
                                "reply_text": " ||| ".join(item.get("replies", [])),
                                "affiliate_link": p_link,
                                "status": "PENDING"
                            })
                        
                        st.session_state["single_batch_list"] = scheduled_batch
                        st.success(f"✅ Berhasil membuat {len(scheduled_batch)} variasi postingan!")
                    except Exception as e:
                        st.error(f"Gagal generate: {e}")

        if "single_batch_list" in st.session_state and st.session_state["single_batch_list"]:
            st.divider()
            st.markdown(f"#### 📋 Pratinjau & Edit Jadwal ({len(st.session_state['single_batch_list'])} Konten Siap Terbit)")
            
            for i, p_item in enumerate(st.session_state["single_batch_list"]):
                with st.expander(f"📌 Post #{i+1} | Jadwal: {p_item['schedule_date']} {p_item['schedule_time']} WIB | ({p_item['angle']})", expanded=(i == 0)):
                    p_item["main_text"] = st.text_area(f"Post Utama #{i+1}", value=p_item["main_text"], key=f"b_main_{i}", height=80)
                    if p_item["reply_text"]:
                        p_item["reply_text"] = st.text_input(f"Balasan Rantai (Pisahkan dengan |||)", value=p_item["reply_text"], key=f"b_rep_{i}")

            col_sb1, col_sb2 = st.columns(2)
            with col_sb1:
                if st.button(f"📥 Masukkan Semua ({len(st.session_state['single_batch_list'])} Post) ke Google Sheets", use_container_width=True, type="primary"):
                    try:
                        s_id = get_config_val("SPREADSHEET_ID")
                        s_name = get_config_val("SHEET_NAME", "Sheet1")
                        c_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")
                        sheets = SheetsManager(c_json, s_id, s_name)
                        
                        sheets.append_rows_batch(st.session_state["single_batch_list"])
                        st.success("🎉 Seluruh jadwal konten berhasil disimpan ke Google Sheets!")
                        del st.session_state["single_batch_list"]
                        time.sleep(1.2)
                        st.rerun()
                    except Exception as ex:
                        st.error(f"Gagal simpan ke Google Sheets: {ex}")

            with col_sb2:
                if st.button("⚡ Post Konten Pertama Sekarang (Direct)", use_container_width=True, disabled=(len(active_accounts) == 0)):
                    first_item = st.session_state["single_batch_list"][0]
                    with st.spinner("Memposting..."):
                        rep_arr = [r.strip() for r in first_item["reply_text"].split("|||") if r.strip()]
                        b_res = broadcast_post(active_accounts, selected_target_str, first_item["main_text"], first_item["main_image_url"], rep_arr)
                        for r in b_res:
                            if r["success"]:
                                st.success(f"✅ Akun **{r['name']}**: Berhasil diposting!")
                            else:
                                st.error(f"❌ Akun **{r['name']}**: Gagal ({r['error']})")

    with subtab_curation:
        st.subheader("🏆 Buat Utas Kurasi / Rekomendasi (Multi-Link Shopee)")
        
        c_k1, c_k2, c_k3 = st.columns([1.5, 1, 1])
        with c_k1:
            cur_target_scope = st.selectbox("🎯 Target Akun Kurasi:", options=["ALL (Cross-Post Semua Akun)"] + account_names_list, key="cur_tgt_scope")
            cur_selected_target = "ALL" if cur_target_scope.startswith("ALL") else cur_target_scope
        with c_k2:
            cur_len_choice = st.selectbox("📏 Panjang Ulasan:", list(LENGTH_CONSTRAINTS.keys()), index=1, key="cur_len")
        with c_k3:
            num_items = st.selectbox("📦 Jumlah Produk:", options=[2, 3, 4, 5], index=1)

        cur_topic = st.text_input("Topik / Judul Kurasi:", placeholder="Contoh: Top 3 Parfum Pria Wangi Mewah Tahan Seharian")
        cur_img = st.text_input("URL Gambar Utama Utas (Opsional):", placeholder="https://domain.com/foto_parfum.jpg")

        st.markdown("#### 🛍️ Rincian Produk:")
        items_data = []
        for i in range(num_items):
            with st.expander(f"📌 Produk #{i+1}", expanded=True):
                col_i1, col_i2, col_i3 = st.columns([1.5, 1.5, 2])
                with col_i1:
                    it_n = st.text_input(f"Nama Produk #{i+1}", key=f"it_name_{i}", placeholder="Misal: HMNS Farhampton")
                with col_i2:
                    it_l = st.text_input(f"Link Shopee Produk #{i+1}", key=f"it_link_{i}", placeholder="https://shope.ee/xxx1")
                with col_i3:
                    it_d = st.text_input(f"Kelebihan Singkat #{i+1}", key=f"it_desc_{i}", placeholder="Aroma warm spicy berkelas")
                items_data.append({"name": it_n, "link": it_l, "desc": it_d})

        c_dt1, c_dt2 = st.columns(2)
        with c_dt1:
            cur_date = st.date_input("Mulai Tanggal:", value=datetime.now(TZ_JAKARTA).date(), key="cur_date")
        with c_dt2:
            cur_time = st.time_input("Mulai Jam:", value=datetime.now(TZ_JAKARTA).time(), key="cur_time")

        if st.button("🪄 Generate Utas Kurasi dengan AI", use_container_width=True, type="primary"):
            if not cur_topic.strip() or any(not it["name"].strip() for it in items_data):
                st.error("Topik dan semua nama produk wajib diisi!")
            else:
                with st.spinner("AI sedang meracik hook dan ulasan per produk..."):
                    try:
                        cur_res = generate_curated_listicle_thread(cur_topic, items_data, cur_len_choice)
                        st.session_state["cur_main_txt"] = cur_res.get("main_text", "")
                        st.session_state["cur_replies"] = cur_res.get("replies", [])
                        st.success("✅ Utas Kurasi Berhasil Dibuat!")
                    except Exception as e:
                        st.error(f"Gagal: {e}")

        if "cur_main_txt" in st.session_state:
            st.divider()
            edit_cur_main = st.text_area("Post Utama Kurasi (Hook):", value=st.session_state.get("cur_main_txt", ""), height=90)
            
            edit_cur_replies = []
            for idx, r_val in enumerate(st.session_state.get("cur_replies", [])):
                e_r = st.text_area(f"Reply #{idx+1} (Ulasan + Link Produk #{idx+1}):", value=r_val, height=80, key=f"cur_r_{idx}")
                edit_cur_replies.append(e_r)

            if st.button("📥 Jadwalkan Utas Kurasi ke Google Sheets", use_container_width=True, type="primary"):
                try:
                    s_id = get_config_val("SPREADSHEET_ID")
                    s_name = get_config_val("SHEET_NAME", "Sheet1")
                    c_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")
                    sheets = SheetsManager(c_json, s_id, s_name)
                    
                    sheets.append_row({
                        "schedule_date": cur_date.strftime("%Y-%m-%d"),
                        "schedule_time": cur_time.strftime("%H:%M"),
                        "target_accounts": cur_selected_target,
                        "main_text": edit_cur_main,
                        "main_image_url": cur_img,
                        "reply_text": " ||| ".join(edit_cur_replies),
                        "affiliate_link": "Multi-Link Curation",
                        "status": "PENDING"
                    })
                    st.success("🎉 Seluruh utas kurasi berhasil dijadwalkan ke Google Sheets!")
                    time.sleep(1)
                    st.rerun()
                except Exception as ex:
                    st.error(f"Gagal simpan: {ex}")

# ----------------------------------------------------
# TAB 2: QUEUE & SHEETS
# ----------------------------------------------------
with tab_queue:
    st.subheader("Data Antrean & Riwayat Google Sheets")
    s_id = get_config_val("SPREADSHEET_ID")
    c_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")
    s_name = get_config_val("SHEET_NAME", "Sheet1")

    if not s_id:
        st.warning("⚠️ SPREADSHEET_ID belum terisi di Secrets.")
    else:
        try:
            sheets_client = SheetsManager(c_json, s_id, s_name)
            df_rows = sheets_client.get_all_rows()

            col_f1, col_f2 = st.columns([1, 2])
            with col_f1:
                f_status = st.radio("Filter Status:", ["ALL", "PENDING", "POSTED", "PARTIAL", "FAILED"], horizontal=True)
            with col_f2:
                target_filter = st.selectbox("Filter Target Akun:", ["Semua Target"] + account_names_list + ["ALL"])

            df_view = df_rows.copy()
            if f_status != "ALL":
                df_view = df_view[df_view["status"].astype(str).str.upper() == f_status]
            if target_filter != "Semua Target":
                df_view = df_view[df_view["target_accounts"].astype(str).str.contains(target_filter, na=False)]

            st.dataframe(df_view, use_container_width=True, hide_index=True)

            st.divider()
            st.write("#### 🛠️ Manajemen Baris")
            row_opts = df_rows["_row_number"].tolist() if not df_rows.empty else []
            if row_opts:
                c_act1, c_act2, c_act3 = st.columns([1.5, 1, 1])
                with c_act1:
                    sel_row = st.selectbox("Pilih Nomor Baris:", options=row_opts)
                with c_act2:
                    if st.button("🔁 Reset ke PENDING", use_container_width=True):
                        sheets_client.update_cell_value(sel_row, "status", "PENDING")
                        sheets_client.update_cell_value(sel_row, "error_log", "")
                        st.toast(f"Baris #{sel_row} diset ke PENDING", icon="✅")
                        time.sleep(1)
                        st.rerun()
                with c_act3:
                    if st.button("🗑️ Hapus Baris", use_container_width=True):
                        sheets_client.delete_row(sel_row)
                        st.toast(f"Baris #{sel_row} dihapus", icon="🗑️")
                        time.sleep(1)
                        st.rerun()
        except Exception as e:
            st.error(f"Gagal memuat Google Sheets: {e}")

# ----------------------------------------------------
# TAB 3: MULTI-ACCOUNT MANAGEMENT
# ----------------------------------------------------
with tab_accounts:
    st.subheader("Daftar Akun Threads Terhubung (Google Sheets)")
    
    if active_accounts:
        df_acc = pd.DataFrame(active_accounts)
        st.dataframe(df_acc[["name", "user_id"]], use_container_width=True, hide_index=True)

        col_t_all, col_del = st.columns([1.5, 1.5])
        with col_t_all:
            if st.button("🔍 Uji Koneksi Semua Akun", use_container_width=True):
                for acc in active_accounts:
                    try:
                        th = ThreadsAPI(acc["user_id"], acc["access_token"])
                        res = th.test_connection()
                        if "id" in res:
                            st.success(f"✅ `{acc['name']}`: Terhubung sebagai **{res.get('username', res.get('name'))}**")
                        else:
                            st.error(f"❌ `{acc['name']}`: Gagal ({res})")
                    except Exception as ex:
                        st.error(f"❌ `{acc['name']}`: Error ({ex})")

        with col_del:
            acc_names = [a["name"] for a in active_accounts]
            del_target = st.selectbox("Pilih Akun untuk Dihapus:", options=acc_names)
            if st.button("🗑️ Hapus Akun Terpilih", use_container_width=True):
                delete_account_from_sheets(del_target)
                st.success(f"Akun '{del_target}' berhasil dihapus.")
                time.sleep(1)
                st.rerun()
    else:
        st.info("Belum ada akun Threads terdaftar di Google Sheets.")

    st.divider()
    st.write("#### ➕ Tambah Akun Threads Baru")
    with st.form("add_account_form"):
        new_acc_name = st.text_input("Label Akun / Niche", placeholder="Misal: fortune.wish (Niche Gadget)")
        new_acc_uid = st.text_input("Threads User ID", placeholder="17841400000000000")
        new_acc_token = st.text_area("Long-Lived Access Token Threads", placeholder="THAAV...")
        
        if st.form_submit_button("💾 Simpan Akun Baru", use_container_width=True):
            if not all([new_acc_name.strip(), new_acc_uid.strip(), new_acc_token.strip()]):
                st.error("Semua kolom wajib diisi!")
            else:
                try:
                    save_new_account_to_sheets(new_acc_name, new_acc_uid, new_acc_token)
                    st.success(f"✅ Akun '{new_acc_name}' berhasil disimpan permanen ke Google Sheets!")
                    time.sleep(1)
                    st.rerun()
                except Exception as ex:
                    st.error(f"Gagal menyimpan akun: {ex}")

# ----------------------------------------------------
# TAB 4: SETTINGS & API KEYS
# ----------------------------------------------------
with tab_settings:
    st.subheader("Konfigurasi API & AI Generator")
    
    with st.form("config_form"):
        curr_ai_key = get_config_val("AI_API_KEY", get_config_val("GROQ_API_KEY", get_config_val("GEMINI_API_KEY")))
        curr_s_id = get_config_val("SPREADSHEET_ID")
        curr_s_name = get_config_val("SHEET_NAME", "Sheet1")

        val_ai_key = st.text_input("AI API Key (Groq `gsk_...` atau Gemini `AIzaSy...`)", value=curr_ai_key, type="password")
        val_s_id = st.text_input("Google Spreadsheet ID", value=curr_s_id)
        val_s_name = st.text_input("Nama Worksheet / Tab Jadwal", value=curr_s_name)

        if st.form_submit_button("💾 Simpan Konfigurasi ke .env (Lokal)", use_container_width=True):
            if not os.path.exists(ENV_PATH):
                open(ENV_PATH, "w").close()
            set_key(ENV_PATH, "AI_API_KEY", val_ai_key)
            set_key(ENV_PATH, "GROQ_API_KEY", val_ai_key)
            set_key(ENV_PATH, "GEMINI_API_KEY", val_ai_key)
            set_key(ENV_PATH, "SPREADSHEET_ID", val_s_id)
            set_key(ENV_PATH, "SHEET_NAME", val_s_name)
            set_key(ENV_PATH, "GOOGLE_CREDS_JSON", "credentials.json")
            load_dotenv(ENV_PATH, override=True)
            st.success("✅ Konfigurasi disimpan!")
            st.rerun()

    st.divider()
    st.write("#### 🧪 Uji Koneksi Generator AI")
    if st.button("🔍 Uji Generator AI Sekarang", use_container_width=True):
        with st.spinner("Menguji respon AI Engine..."):
            try:
                target_key = val_ai_key.strip() if val_ai_key else None
                test_resp = call_ai_engine("Halo, buatkan 1 kalimat motivasi affiliate pendek.", key_override=target_key)
                st.success(f"✅ AI Engine Aktif & Merespons: \"{test_resp}\"")
            except Exception as e_test:
                st.error(f"❌ Uji Gagal: {e_test}")

# ----------------------------------------------------
# TAB 5: SYSTEM LOGS
# ----------------------------------------------------
with tab_logs:
    st.subheader("System Activity Logs")
    if st.button("🔄 Refresh Log", use_container_width=True):
        st.rerun()

    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            log_data = "".join(f.readlines()[-120:])
            st.code(log_data if log_data else "Log masih kosong.", language="log")
    else:
        st.info("Belum ada log.")
