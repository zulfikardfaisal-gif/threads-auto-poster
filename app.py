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
    if key in st.secrets:
        return str(st.secrets[key]).strip()
    return os.getenv(key, default).strip()

def clean_private_key(raw_key: str) -> str:
    """Membersihkan dan menyusun ulang struktur PEM Private Key"""
    raw_key = str(raw_key).replace("\\n", "\n").replace("\r", "").strip()
    lines = [l.strip() for l in raw_key.split("\n") if l.strip()]
    body = "".join([l for l in lines if not l.startswith("-----")])
    body = re.sub(r"[^A-Za-z0-9+/=]", "", body)
    rem = len(body) % 4
    if rem > 0:
        body += "=" * (4 - rem)
    chunks = [body[i:i+64] for i in range(0, len(body), 64)]
    return "-----BEGIN PRIVATE KEY-----\n" + "\n".join(chunks) + "\n-----END PRIVATE KEY-----\n"

def get_gcp_credentials_dict():
    if "GCP_SERVICE_ACCOUNT" in st.secrets:
        raw = st.secrets["GCP_SERVICE_ACCOUNT"]
        cd = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if "private_key" in cd:
            cd["private_key"] = clean_private_key(cd["private_key"])
        return cd
    elif "gcp_service_account" in st.secrets:
        cd = dict(st.secrets["gcp_service_account"])
        if "private_key" in cd:
            cd["private_key"] = clean_private_key(cd["private_key"])
        return cd
    elif os.path.exists("credentials.json"):
        with open("credentials.json", "r") as f:
            cd = json.load(f)
            if "private_key" in cd:
                cd["private_key"] = clean_private_key(cd["private_key"])
            return cd
    return None

def extract_and_parse_json(raw_str: str):
    """Sanitizer kuat untuk mengekstrak dan mem-parse output JSON dari LLM secara akurat"""
    # 1. Hapus reasoning tags jika ada (<think>...</think>)
    text = re.sub(r"<think>.*?</think>", "", raw_str, flags=re.DOTALL).strip()
    # 2. Hapus markdown code blocks ```json ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
    
    # 3. Cari batas JSON bracket [ ] atau { }
    f_bracket, l_bracket = text.find('['), text.rfind(']')
    f_brace, l_brace = text.find('{'), text.rfind('}')
    
    candidate = text
    if f_bracket != -1 and l_bracket != -1 and (f_brace == -1 or f_bracket < f_brace):
        candidate = text[f_bracket:l_bracket+1]
    elif f_brace != -1 and l_brace != -1:
        candidate = text[f_brace:l_brace+1]
        
    try:
        return json.loads(candidate, strict=False)
    except Exception:
        # Perbaikan manual jika ada tanda kutip tunggal atau unescaped quotes
        clean_cand = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', candidate)
        return json.loads(clean_cand, strict=False)

# ==========================================
# 2. HELPER DATA MULTI-AKUN (GOOGLE SHEETS)
# ==========================================
def get_accounts_worksheet():
    s_id = get_config_val("SPREADSHEET_ID")
    creds_dict = get_gcp_credentials_dict()
    if not s_id or not creds_dict:
        return None
    try:
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
        logger.error(f"Gagal konek ke tab Accounts: {e}")
        return None

def load_accounts() -> list:
    ws = get_accounts_worksheet()
    if ws:
        try:
            records = ws.get_all_records()
            return [r for r in records if str(r.get("user_id", "")).strip() != ""]
        except Exception:
            pass
            
    if "ACCOUNTS_JSON" in st.secrets:
        try:
            val = st.secrets["ACCOUNTS_JSON"]
            return json.loads(val) if isinstance(val, str) else val
        except Exception:
            pass
    return []

def save_new_account_to_sheets(name: str, user_id: str, access_token: str):
    ws = get_accounts_worksheet()
    if ws:
        ws.append_row([str(name).strip(), str(user_id).strip(), str(access_token).strip()])
    else:
        raise Exception("Gagal terhubung ke Google Sheets.")

def delete_account_from_sheets(name: str):
    ws = get_accounts_worksheet()
    if ws:
        records = ws.get_all_records()
        for i, r in enumerate(records, start=2):
            if str(r.get("name", "")).strip() == name:
                ws.delete_rows(i)
                break

# ==========================================
# 3. UNIVERSAL AI ENGINE (DYNAMIC AUTO-DISCOVERY)
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

def get_groq_active_models(api_key: str) -> list:
    url = "[https://api.groq.com/openai/v1/models](https://api.groq.com/openai/v1/models)"
    headers = {"Authorization": f"Bearer {api_key.strip()}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json().get("data", [])
            valid_models = [
                m["id"] for m in data 
                if not any(x in m["id"].lower() for x in ["whisper", "guard", "vision", "audio", "embed"])
            ]
            if valid_models:
                return valid_models
    except Exception:
        pass
    return ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

def call_groq_api(prompt: str, api_key: str) -> str:
    url = "[https://api.groq.com/openai/v1/chat/completions](https://api.groq.com/openai/v1/chat/completions)"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }
    models_to_try = get_groq_active_models(api_key)
    err_list = []
    
    for m in models_to_try:
        payload = {
            "model": m,
            "messages": [
                {"role": "system", "content": "You are a professional Indonesian Threads affiliate copywriter. Always output clean raw JSON only."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"].strip()
            err_list.append(f"[{m}]: HTTP {res.status_code} - {res.text}")
        except Exception as e:
            err_list.append(f"[{m}]: {str(e)}")
            continue
    raise Exception(f"Gagal memanggil Groq AI: {' | '.join(err_list)}")

def call_gemini_rest(prompt: str, api_key: str) -> str:
    models = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]
    err_list = []
    for m in models:
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){m}:generateContent?key={api_key.strip()}"
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
    key = key_override.strip() if key_override else get_config_val("AI_API_KEY", get_config_val("GROQ_API_KEY", get_config_val("GEMINI_API_KEY")))
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

def generate_bulk_single_product_threads(product_name: str, product_notes: str, affiliate_link: str, style_choice: str, length_choice: str, reply_count: int, count: int = 5) -> list:
    style_inst = STYLE_PROMPTS.get(style_choice, "")
    len_inst = LENGTH_CONSTRAINTS.get(length_choice, "Maksimal 350 karakter.")
    
    prompt = f"""
    Bertindaklah sebagai Copywriter Top Tier spesialis Threads Indonesia & Shopee Affiliate.
    Buatkan {count} buah Utas (Thread) yang BERBEDA SUDUT PANDANG & HOOK untuk produk:
    - Nama Produk: {product_name}
    - Catatan/Spesifikasi: {product_notes if product_notes else "Produk viral terlaris, kualitas terjamin"}
    - Gaya Penulisan: {style_inst}
    - Batasan Panjang Teks: {len_inst}
    - Jumlah Balasan (Reply) per Post: {reply_count} balasan (di luar post utama).

    WAJIB Format JSON murni (List of Objects), tanpa teks pengantar atau markdown wrapper ```json:
    [
      {{
        "angle": "Sudut Pandang / Variasi",
        "main_text": "Teks post utama hook ({len_inst})",
        "replies": ["Teks balasan 1 ({len_inst})", "Teks balasan 2 ({len_inst})"]
      }}
    ]
    """
    raw_text = call_ai_engine(prompt)
    parsed = extract_and_parse_json(raw_text)
    
    items = parsed.get("items", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        items = [items]
    
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
    
    prompt = f"""
    Bertindaklah sebagai Copywriter Top Tier spesialis Threads Indonesia.
    Buatkan 1 Utas Kurasi Rekomendasi/Top List bertema: "{curation_topic}".
    
    Daftar Produk:
    {items_text}
    
    Instruksi:
    - "main_text": Hook pembuka rekomendasi ({len_inst}).
    - "replies": Array di mana setiap elemen HANYA membahas 1 Item secara runtut ({len_inst}), diakhiri link Shopee masing-masing.

    WAJIB Format JSON murni tanpa markdown wrapper
