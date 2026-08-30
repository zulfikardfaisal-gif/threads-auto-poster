import os
import sys
import json
import time
import re
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
ACCOUNTS_FILE = os.path.join(os.path.dirname(__file__), "accounts.json")
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

def get_config_val(key: str, default: str = "") -> str:
    """Mengambil config dari Streamlit Secrets atau .env lokal"""
    if key in st.secrets:
        return str(st.secrets[key]).strip()
    return os.getenv(key, default).strip()

# ==========================================
# 2. HELPER DATA MULTI-AKUN (PERMANEN GOOGLE SHEETS)
# ==========================================
def get_accounts_worksheet():
    """Membuka atau membuat tab 'Accounts' di Google Sheets"""
    s_id = get_config_val("SPREADSHEET_ID")
    c_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")
    
    if not s_id:
        return None
        
    try:
        # 1. Cek Secrets Cloud
        if "GCP_SERVICE_ACCOUNT" in st.secrets:
            raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT"]
            creds_info = json.loads(raw_gcp) if isinstance(raw_gcp, str) else dict(raw_gcp)
            if "private_key" in creds_info:
                creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
            creds = Credentials.from_service_account_info(creds_info, scopes=SheetsManager.SCOPES)
        # 2. Cek File Lokal
        elif os.path.exists(c_json):
            creds = Credentials.from_service_account_file(c_json, scopes=SheetsManager.SCOPES)
        else:
            return None

        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(s_id)
        
        try:
            return spreadsheet.worksheet("Accounts")
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title="Accounts", rows=50, cols=5)
            ws.append_row(["name", "user_id", "access_token"])
            return ws
    except Exception as e:
        logger.error(f"Gagal konek ke tab Accounts: {e}")
        return None

def load_accounts() -> list:
    """Membaca akun permanen dari Google Sheets (tab Accounts)"""
    ws = get_accounts_worksheet()
    if ws:
        try:
            records = ws.get_all_records()
            return [r for r in records if str(r.get("user_id", "")).strip() != ""]
        except Exception:
            pass
    
    # Fallback ke Secrets jika ada
    if "ACCOUNTS_JSON" in st.secrets:
        try:
            val = st.secrets["ACCOUNTS_JSON"]
            return json.loads(val) if isinstance(val, str) else val
        except Exception:
            pass
    return []

def save_new_account_to_sheets(name: str, user_id: str, access_token: str):
    """Menyimpan akun baru secara permanen ke Google Sheets"""
    ws = get_accounts_worksheet()
    if ws:
        ws.append_row([str(name).strip(), str(user_id).strip(), str(access_token).strip()])
    else:
        raise Exception("Gagal terhubung ke Google Sheets untuk menyimpan akun.")

def delete_account_from_sheets(name: str):
    """Menghapus akun dari Google Sheets"""
    ws = get_accounts_worksheet()
    if ws:
        records = ws.get_all_records()
        for i, r in enumerate(records, start=2):
            if str(r.get("name", "")).strip() == name:
                ws.delete_rows(i)
                break
# ==========================================
# 3. AI GENERATOR ENGINE (AUTO-DISCOVERY REST API)
# ==========================================
STYLE_PROMPTS = {
    "🤖 Otomatis (AI Pintar Memilih)": "Analisis produk ini dan pilih gaya terbaik yang paling relevan.",
    "📖 Storytelling / Curhat Personal": "Gunakan sudut pandang orang pertama (pengalaman pribadi/curhat santai). Alur: masalah yang dialami -> momen nemu produk -> hasil nyata -> kepuasan.",
    "🔥 Spill Racun Diskon & FOMO": "Gaya bersemangat, racun shopee, fokus ke diskon, voucher terbatas, dan mendesak audiens segera checkout.",
    "🧐 Review Edukatif & Bedah Fitur": "Gaya objektif, bedah bahan/spesifikasi, perbandingan kualitas, tips cara pakai, dan alasan worth it.",
    "✨ Aesthetic & Lifestyle Vibe": "Gaya santai, estetik, hangat, fokus pada visual dan kenyamanan gaya hidup.",
    "🤣 Humor & Bahasa Gaul Santai": "Gaya santai khas linimasa Threads Indonesia, sedikit bercanda/relatable, dan memicu komentar netizen."
}

def get_available_gemini_models(api_key: str) -> list:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            data = res.json()
            valid_models = []
            for m in data.get("models", []):
                methods = m.get("supportedGenerationMethods", [])
                if "generateContent" in methods:
                    m_id = m.get("name", "").replace("models/", "")
                    if not any(skip in m_id.lower() for skip in ["embedding", "imagen", "aqa", "text-embedding"]):
                        valid_models.append(m_id)
            return valid_models
    except Exception as e:
        logger.warning(f"Gagal mengambil model: {e}")
    return []

def call_gemini_api_direct(prompt: str) -> str:
    api_key = get_config_val("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY belum disetel! Masukkan di Secrets atau tab Konfigurasi.")

    active_models = get_available_gemini_models(api_key)
    priority_order = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-flash-latest",
        "gemini-2.5-pro",
        "gemini-2.0-flash-exp",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash",
        "gemini-pro"
    ]
    
    ordered_models = []
    for p in priority_order:
        if p in active_models and p not in ordered_models:
            ordered_models.append(p)
    for m in active_models:
        if m not in ordered_models:
            ordered_models.append(m)

    if not ordered_models:
        ordered_models = priority_order

    last_error = ""
    for model_name in ordered_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2500}
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            else:
                last_error = f"Model '{model_name}' (HTTP {res.status_code}): {res.text}"
                continue
        except Exception as e:
            last_error = f"Model '{model_name}': {str(e)}"
            continue

    raise Exception(f"Gagal generate konten dengan Gemini. Detail: {last_error}")

def generate_single_thread(product_name: str, product_notes: str, affiliate_link: str, style_choice: str) -> dict:
    style_instruction = STYLE_PROMPTS.get(style_choice, STYLE_PROMPTS["🤖 Otomatis (AI Pintar Memilih)"])
    prompt = f"""
    Bertindaklah sebagai Copywriter Top Tier spesialis Threads Indonesia & Shopee Affiliate.
    Buatkan 1 Utas (Thread) bersambung yang terdiri dari 1 Post Utama dan 4 Balasan Rantai yang saling menyambung.

    Informasi Produk:
    - Nama Produk: {product_name}
    - Catatan/Spesifikasi: {product_notes if product_notes else "Produk viral, berkualitas, banyak dibeli"}
    - Link Affiliate: {affiliate_link}

    Gaya Penulisan: {style_instruction}

    STRUKTUR UTAS WAJIB:
    - "main_text": Hook pembuka menarik (Maks 450 karakter).
    - "reply_1": Poin pembuka/kelanjutan hook (Maks 450 karakter).
    - "reply_2": Detail spesifikasi/pengalaman nyata (Maks 450 karakter).
    - "reply_3": Tips varian/alasan wajib punya (Maks 450 karakter).
    - "reply_4": Info urgensi promo/penutup sebelum link (Maks 450 karakter).

    Format Output WAJIB JSON murni:
    {{
      "main_text": "...",
      "reply_1": "...",
      "reply_2": "...",
      "reply_3": "...",
      "reply_4": "..."
    }}
    """
    raw_text = call_gemini_api_direct(prompt)
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    return json.loads(match.group(0) if match else raw_text)

def generate_bulk_threads(product_name: str, product_notes: str, affiliate_link: str, count: int = 5) -> list:
    prompt = f"""
    Bertindaklah sebagai Copywriter Top Tier & Strategist Shopee Affiliate di Threads Indonesia.
    Buatkan {count} buah konten Utas (Thread) yang BERBEDA TOTAL GAYA & SUDUT PANDANG untuk produk:

    Data Produk:
    - Nama Produk: {product_name}
    - Catatan/Spesifikasi: {product_notes if product_notes else "Produk viral, kualitas terbaik, terlaris"}
    - Link Affiliate: {affiliate_link}

    Format Output WAJIB JSON murni List of Objects:
    [
      {{
        "style_name": "Gaya Konten (misal: Storytelling)",
        "main_text": "...",
        "reply_1": "...",
        "reply_2": "...",
        "reply_3": "...",
        "reply_4": "..."
      }}
    ]
    """
    raw_text = call_gemini_api_direct(prompt)
    match = re.search(r"\[.*\]", raw_text, re.DOTALL)
    return json.loads(match.group(0) if match else raw_text)

# ==========================================
# 4. CLIENT MODULE: THREADS API
# ==========================================
class ThreadsAPI:
    BASE_URL = "https://graph.threads.net/v1.0"

    def __init__(self, user_id: str, access_token: str):
        self.user_id = str(user_id).strip()
        self.access_token = str(access_token).strip()

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
                status = data.get("status")
                if status == "FINISHED":
                    return True
                elif status == "ERROR":
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
            if len(text) > 500:
                raise ValueError("Teks Threads melebihi batas 500 karakter!")
            payload["text"] = text

        if image_url and str(image_url).strip().startswith(("http://", "https://")):
            payload["media_type"] = "IMAGE"
            payload["image_url"] = str(image_url).strip()
        else:
            payload["media_type"] = "TEXT"

        if reply_to_id:
            payload["reply_to_id"] = reply_to_id

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
            valid_replies = [r.strip() for r in replies if r.strip()][:5]
            for r_text in valid_replies:
                time.sleep(3)
                r_cid = self.create_container(text=r_text, reply_to_id=current_parent_id)
                time.sleep(2)
                r_id = self.publish_container(r_cid)
                published_ids.append(r_id)
                current_parent_id = r_id

        return published_ids

def broadcast_post(accounts: list, main_text: str, image_url: str = None, replies: list = None) -> list:
    results = []
    for acc in accounts:
        name = acc.get("name", "Unknown")
        uid = acc.get("user_id")
        tok = acc.get("access_token")
        try:
            client = ThreadsAPI(uid, tok)
            post_ids = client.post_thread_cascade(main_text, image_url, replies)
            results.append({"name": name, "success": True, "post_ids": post_ids, "error": ""})
            logger.info(f"✅ Post berhasil ke '{name}' (IDs: {post_ids})")
        except Exception as e:
            results.append({"name": name, "success": False, "post_ids": [], "error": str(e)})
            logger.error(f"❌ Post gagal ke '{name}': {e}")
    return results

# ==========================================
# 5. CLIENT MODULE: GOOGLE SHEETS
# ==========================================
class SheetsManager:
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    HEADERS = [
        "schedule_date", "schedule_time", "main_text", "main_image_url",
        "reply_text", "affiliate_link", "status", "posted_at", "threads_post_id", "error_log"
    ]

    def __init__(self, creds_path: str, spreadsheet_id: str, sheet_name: str = "Sheet1"):
        self.creds_path = creds_path
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.sheet = self._connect()

    def _connect(self):
        # 1. Cek Secrets Cloud
        if "GCP_SERVICE_ACCOUNT" in st.secrets:
            raw_gcp = st.secrets["GCP_SERVICE_ACCOUNT"]
            creds_info = json.loads(raw_gcp) if isinstance(raw_gcp, str) else raw_gcp
            creds = Credentials.from_service_account_info(creds_info, scopes=self.SCOPES)
        # 2. Cek File Lokal
        elif os.path.exists(self.creds_path):
            creds = Credentials.from_service_account_file(self.creds_path, scopes=self.SCOPES)
        else:
            raise FileNotFoundError("Kredensial GCP tidak ditemukan di Secrets maupun file lokal.")

        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(self.spreadsheet_id)
        try:
            return spreadsheet.worksheet(self.sheet_name)
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
    load_dotenv(ENV_PATH, override=True)
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
                sheets.update_cell_value(row_num, "error_log", f"Format Salah: {e}")
                continue

            if now >= sched_dt:
                logger.info(f"[SCHEDULER] ⏳ Memproses baris #{row_num}...")
                main_txt = str(row["main_text"]).strip()
                img_url = str(row["main_image_url"]).strip()
                raw_reply = str(row["reply_text"]).strip()
                aff_link = str(row["affiliate_link"]).strip()

                replies = [r.strip() for r in raw_reply.split("|||") if r.strip()]
                if aff_link:
                    cta_link = f"👉 Beli di Shopee: {aff_link}"
                    if len(replies) < 5:
                        replies.append(cta_link)
                    else:
                        replies[4] = f"{replies[4]}\n\n{cta_link}"

                broadcast_res = broadcast_post(accounts, main_txt, img_url if img_url else None, replies)
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

st.title("🚀 Threads Multi-Account & AI Batch Generator")
st.markdown("Otomasi Shopee Affiliate: Batch generator 1 produk ke banyak konten bergaya unik, cascading replies, dan penjadwalan terdistribusi.")

active_accounts = load_accounts()

# Sidebar
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
        st.warning("⚠️ Belum ada akun Threads yang terdeteksi. Silakan atur di Secrets Cloud atau tab **👥 Multi-Account**.")
    else:
        st.caption(f"📢 Target Distribusi: **{len(active_accounts)} Akun** (" + ", ".join([f"`{a['name']}`" for a in active_accounts]) + ")")

    subtab_bulk, subtab_single = st.tabs(["🚀 1 Produk -> Batch Multi-Konten (Auto-Jadwal)", "✍️ Single Post Studio"])

    with subtab_bulk:
        st.subheader("Otomasi 1 Produk Menjadi Banyak Konten Berbeda Gaya")
        
        col_b1, col_b2 = st.columns([1.2, 1.8])
        with col_b1:
            bulk_prod_name = st.text_input("Nama Produk (Bulk)", placeholder="Contoh: Celana Sweatpants Loose Pria")
            bulk_aff_link = st.text_input("Link Shopee Affiliate (Bulk)", placeholder="https://shope.ee/xxxxx")
            bulk_img_url = st.text_input("URL Gambar (Opsional, dipakai untuk batch ini)", placeholder="https://domain.com/gambar.jpg")
            
        with col_b2:
            bulk_notes = st.text_area("Catatan / Keunggulan Unik Produk:", placeholder="Misal: Bahan katun fleece adem, potongan loose casual, diskon 40% hari ini...", height=110)
            
            col_b_opt1, col_b_opt2, col_b_opt3 = st.columns(3)
            with col_b_opt1:
                bulk_count = st.number_input("Jumlah Konten:", min_value=1, max_value=15, value=5, step=1)
            with col_b_opt2:
                bulk_interval_hours = st.selectbox(
                    "Jeda Waktu Antar Post:",
                    options=[2, 3, 4, 6, 8, 12, 24],
                    index=2,
                    format_func=lambda x: f"Setiap {x} Jam" if x < 24 else "Setiap 1 Hari (24 Jam)"
                )
            with col_b_opt3:
                bulk_start_date = st.date_input("Mulai Tanggal:", value=datetime.now(TZ_JAKARTA).date())

        if st.button(f"🪄 Generate {bulk_count} Konten Variatif & Siapkan Jadwal", use_container_width=True, type="primary"):
            if not bulk_prod_name.strip():
                st.error("Nama produk wajib diisi!")
            else:
                with st.spinner(f"AI sedang meracik {bulk_count} variasi utas berbeda gaya..."):
                    try:
                        batch_results = generate_bulk_threads(
                            product_name=bulk_prod_name,
                            product_notes=bulk_notes,
                            affiliate_link=bulk_aff_link,
                            count=bulk_count
                        )
                        
                        scheduled_items = []
                        start_base_dt = TZ_JAKARTA.localize(datetime.combine(bulk_start_date, datetime.now(TZ_JAKARTA).time()))
                        
                        for i, item in enumerate(batch_results):
                            post_dt = start_base_dt + timedelta(hours=(i * bulk_interval_hours))
                            raw_replies = [item.get("reply_1", ""), item.get("reply_2", ""), item.get("reply_3", ""), item.get("reply_4", "")]
                            clean_replies = [r.strip() for r in raw_replies if r.strip()]
                            
                            scheduled_items.append({
                                "schedule_date": post_dt.strftime("%Y-%m-%d"),
                                "schedule_time": post_dt.strftime("%H:%M"),
                                "style_name": item.get("style_name", f"Gaya #{i+1}"),
                                "main_text": item.get("main_text", ""),
                                "main_image_url": bulk_img_url,
                                "reply_text": " ||| ".join(clean_replies),
                                "affiliate_link": bulk_aff_link,
                                "status": "PENDING"
                            })
                        
                        st.session_state["generated_bulk_list"] = scheduled_items
                        st.success(f"✅ Berhasil membuat {len(scheduled_items)} konten dengan gaya berbeda!")
                    except Exception as e:
                        st.error(f"Gagal generate bulk konten: {e}")

        if "generated_bulk_list" in st.session_state and st.session_state["generated_bulk_list"]:
            st.divider()
            st.markdown(f"#### 📋 Pratinjau {len(st.session_state['generated_bulk_list'])} Konten yang Dihasilkan:")
            df_preview = pd.DataFrame(st.session_state["generated_bulk_list"])
            st.dataframe(df_preview[["schedule_date", "schedule_time", "style_name", "main_text", "reply_text"]], use_container_width=True, hide_index=True)
            
            if st.button(f"📥 Masukkan Semua ({len(st.session_state['generated_bulk_list'])} Konten) ke Google Sheets", use_container_width=True, type="primary"):
                try:
                    s_id = get_config_val("SPREADSHEET_ID")
                    s_name = get_config_val("SHEET_NAME", "Sheet1")
                    c_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")
                    sheets = SheetsManager(c_json, s_id, s_name)
                    
                    sheets.append_rows_batch(st.session_state["generated_bulk_list"])
                    st.success("🎉 Seluruh konten batch berhasil dimasukkan ke antrean Google Sheets!")
                    del st.session_state["generated_bulk_list"]
                    time.sleep(1.5)
                    st.rerun()
                except Exception as ex:
                    st.error(f"Gagal menyimpan ke Google Sheets: {ex}")

    with subtab_single:
        st.subheader("Buat / Edit 1 Postingan Spesifik")
        
        with st.expander("✨ AI Single Generator (Pilih 1 Gaya)", expanded=False):
            col_ai1, col_ai2 = st.columns([1.2, 1.8])
            with col_ai1:
                ai_prod_name = st.text_input("Nama Produk", placeholder="Celana Sweatpants Loose Pria")
                ai_aff_link = st.text_input("Link Shopee Affiliate", placeholder="https://shope.ee/xxxxx")
                ai_style_select = st.selectbox("Pilih Gaya / Style:", list(STYLE_PROMPTS.keys()))
            with col_ai2:
                ai_notes = st.text_area("Catatan Tambahan:", placeholder="Bahan adem, diskon 50% hari ini...", height=110)

            if st.button("🪄 Generate 1 Utas dengan AI", use_container_width=True):
                if not ai_prod_name.strip():
                    st.error("Nama produk wajib diisi!")
                else:
                    with st.spinner("Membuat utas..."):
                        try:
                            res_ai = generate_single_thread(ai_prod_name, ai_notes, ai_aff_link, ai_style_select)
                            st.session_state["f_main_text"] = res_ai.get("main_text", "")
                            st.session_state["f_rep1"] = res_ai.get("reply_1", "")
                            st.session_state["f_rep2"] = res_ai.get("reply_2", "")
                            st.session_state["f_rep3"] = res_ai.get("reply_3", "")
                            st.session_state["f_rep4"] = res_ai.get("reply_4", "")
                            st.session_state["f_shopee_link"] = ai_aff_link
                            st.success("✅ Konten terisi di form!")
                        except Exception as e:
                            st.error(f"Gagal: {e}")

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            post_date = st.date_input("Tanggal Publikasi (Single)", value=datetime.now(TZ_JAKARTA).date())
        with col_s2:
            post_time = st.time_input("Waktu Publikasi (Single)", value=datetime.now(TZ_JAKARTA).time())

        col_input, col_preview = st.columns([1.2, 0.8])
        with col_input:
            main_content = st.text_area("Teks Postingan Utama (Hook)", value=st.session_state.get("f_main_text", ""), height=110)
            c_count = len(main_content)
            st.caption(f"Karakter: {c_count}/500")

            img_url = st.text_input("URL Gambar", placeholder="https://domain.com/gambar.jpg")
            shopee_url = st.text_input("Link Shopee Affiliate (Single)", value=st.session_state.get("f_shopee_link", ""), placeholder="https://shope.ee/xxxxx")

        with col_preview:
            st.write("**Pratinjau Gambar:**")
            if img_url and img_url.strip().startswith(("http://", "https://")):
                try:
                    st.image(img_url.strip(), use_container_width=True)
                except Exception:
                    st.warning("⚠️ URL gambar tidak valid.")
            else:
                st.info("Preview gambar akan tampil di sini.")

        st.markdown("#### 🧵 Rantai Balasan (Maksimal 5)")
        col_r1, col_r2 = st.columns(2)
        with col_r1:
            rep_1 = st.text_input("Balasan 1", value=st.session_state.get("f_rep1", ""))
            rep_2 = st.text_input("Balasan 2", value=st.session_state.get("f_rep2", ""))
            rep_3 = st.text_input("Balasan 3", value=st.session_state.get("f_rep3", ""))
        with col_r2:
            rep_4 = st.text_input("Balasan 4", value=st.session_state.get("f_rep4", ""))
            rep_5 = st.text_input("Balasan 5 (Opsional)", placeholder="Penutup tambahan...")

        entered_replies = [r.strip() for r in [rep_1, rep_2, rep_3, rep_4, rep_5] if r.strip()]
        raw_replies_joined = " ||| ".join(entered_replies)

        st.divider()
        btn_c1, btn_c2 = st.columns(2)
        
        with btn_c1:
            if st.button("📥 Jadwalkan 1 Post ke Google Sheets", use_container_width=True, type="primary", disabled=(len(active_accounts) == 0)):
                if not main_content.strip():
                    st.error("Teks postingan utama wajib diisi!")
                elif c_count > 500:
                    st.error("Teks melebihi 500 karakter!")
                else:
                    try:
                        s_id = get_config_val("SPREADSHEET_ID")
                        s_name = get_config_val("SHEET_NAME", "Sheet1")
                        c_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")
                        sheets = SheetsManager(c_json, s_id, s_name)
                        
                        sheets.append_row({
                            "schedule_date": post_date.strftime("%Y-%m-%d"),
                            "schedule_time": post_time.strftime("%H:%M"),
                            "main_text": main_content,
                            "main_image_url": img_url,
                            "reply_text": raw_replies_joined,
                            "affiliate_link": shopee_url,
                            "status": "PENDING"
                        })
                        st.success("✅ Berhasil dijadwalkan ke Google Sheets!")
                        time.sleep(1)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Gagal: {e}")

        with btn_c2:
            if st.button("⚡ Cross-Post Sekarang (Direct)", use_container_width=True, disabled=(len(active_accounts) == 0)):
                if not main_content.strip():
                    st.error("Teks postingan utama wajib diisi!")
                elif c_count > 500:
                    st.error("Teks melebihi 500 karakter!")
                else:
                    with st.spinner("Memposting..."):
                        replies_direct = list(entered_replies)
                        if shopee_url:
                            cta_direct = f"👉 Beli di Shopee: {shopee_url}"
                            if len(replies_direct) < 5:
                                replies_direct.append(cta_direct)
                            else:
                                replies_direct[4] = f"{replies_direct[4]}\n\n{cta_direct}"

                        res_broadcast = broadcast_post(active_accounts, main_content, img_url if img_url else None, replies_direct)
                        for r in res_broadcast:
                            if r["success"]:
                                st.success(f"✅ Akun **{r['name']}**: Berhasil ({len(r['post_ids'])} post terbit)")
                            else:
                                st.error(f"❌ Akun **{r['name']}**: Gagal ({r['error']})")

# ----------------------------------------------------
# TAB 2: QUEUE & SHEETS
# ----------------------------------------------------
with tab_queue:
    st.subheader("Data Antrean & Riwayat Google Sheets")
    
    s_id = get_config_val("SPREADSHEET_ID")
    c_json = get_config_val("GOOGLE_CREDS_JSON", "credentials.json")
    s_name = get_config_val("SHEET_NAME", "Sheet1")

    if not s_id:
        st.warning("⚠️ Konfigurasi Google Sheets belum lengkap.")
    else:
        try:
            sheets_client = SheetsManager(c_json, s_id, s_name)
            df_rows = sheets_client.get_all_rows()

            f_status = st.radio("Filter Status:", ["ALL", "PENDING", "POSTED", "PARTIAL", "FAILED"], horizontal=True)
            if f_status != "ALL":
                df_view = df_rows[df_rows["status"].astype(str).str.upper() == f_status]
            else:
                df_view = df_rows

            st.dataframe(df_view, use_container_width=True, hide_index=True)

            st.divider()
            st.write("#### 🛠️ Manajemen Baris")
            row_opts = df_rows["_row_number"].tolist() if not df_rows.empty else []
            
            if row_opts:
                c_act1, c_act2, c_act3 = st.columns([1.5, 1, 1])
                with c_act1:
                    sel_row = st.selectbox("Pilih Baris:", options=row_opts)
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
            st.error(f"Gagal memuat data dari Google Sheets: {e}")

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
                st.success(f"Akun '{del_target}' berhasil dihapus dari Google Sheets.")
                time.sleep(1)
                st.rerun()
    else:
        st.info("Belum ada akun Threads terdaftar di Google Sheets.")

    st.divider()
    st.write("#### ➕ Tambah Akun Threads Baru")
    with st.form("add_account_form"):
        new_acc_name = st.text_input("Label Akun", placeholder="Misal: Akun Fashion / Akun 2")
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
    st.subheader("Konfigurasi API, AI & Google Sheets")
    
    with st.form("config_form"):
        curr_gemini = get_config_val("GEMINI_API_KEY")
        curr_s_id = get_config_val("SPREADSHEET_ID")
        curr_s_name = get_config_val("SHEET_NAME", "Sheet1")

        val_gemini = st.text_input("Google Gemini API Key", value=curr_gemini, type="password")
        val_s_id = st.text_input("Google Spreadsheet ID", value=curr_s_id)
        val_s_name = st.text_input("Nama Worksheet / Tab", value=curr_s_name)

        if st.form_submit_button("💾 Simpan Konfigurasi ke .env (Lokal)", use_container_width=True):
            if not os.path.exists(ENV_PATH):
                open(ENV_PATH, "w").close()
            set_key(ENV_PATH, "GEMINI_API_KEY", val_gemini)
            set_key(ENV_PATH, "SPREADSHEET_ID", val_s_id)
            set_key(ENV_PATH, "SHEET_NAME", val_s_name)
            set_key(ENV_PATH, "GOOGLE_CREDS_JSON", "credentials.json")
            load_dotenv(ENV_PATH, override=True)
            st.success("✅ Seluruh konfigurasi berhasil disimpan!")
            st.rerun()

    st.divider()
    st.write("#### 🧪 Uji Koneksi AI Gemini")
    if st.button("🔍 Uji Generator Gemini API", use_container_width=True):
        with st.spinner("Mendeteksi model aktif di akun Google Anda..."):
            try:
                curr_k = get_config_val("GEMINI_API_KEY")
                models_found = get_available_gemini_models(curr_k)
                if models_found:
                    st.info(f"📋 Model aktif terdeteksi: `{', '.join(models_found[:4])}`")
                
                test_resp = call_gemini_api_direct("Buatkan 1 kalimat sapaan pendek untuk affiliate marketer.")
                st.success(f"✅ Gemini AI Aktif & Merespons: \"{test_resp}\"")
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
        st.info("Belum ada log tercatat.")
