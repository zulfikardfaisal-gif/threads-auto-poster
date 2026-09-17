import os
import re
import json
import base64
import random
import logging
from datetime import datetime, timedelta
import pytz
import gspread
from google.oauth2.service_account import Credentials
from google import genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutoGenerator")
TZ = pytz.timezone("Asia/Jakarta")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GCP_CREDS_BASE64 = os.environ.get("GCP_CREDS_BASE64")
AI_API_KEY = os.environ.get("AI_API_KEY")

def get_sheets_client():
    creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def get_ai_client():
    return genai.Client(api_key=AI_API_KEY)

def generate_viral_content(client) -> str:
    """Membuat konten opini santai/sambat untuk memancing interaksi tanpa jualan."""
    prompt = """
    Tulis 1 postingan Threads gaya Indonesia santai/relatable.
    Topik seputar: realita dunia kerja, kebiasaan begadang, overthinking receh, atau kebiasaan boros anak muda.
    Aturan:
    - Tanpa emoji berlebihan.
    - Maksimal 280 karakter.
    - Nada bicara seperti teman tongkrongan (bukan robot, bukan iklan formal).
    - HANYA kembalikan teks postingan, tanpa tanda kutip pembuka/penutup.
    """
    res = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return res.text.strip().replace('"', '')

def generate_affiliate_content(client, prod_name: str, highlight: str, aff_link: str) -> tuple[str, str]:
    """Membuat postingan utama dan rantai balasan link Shopee."""
    prompt = f"""
    Produk: {prod_name}
    Keunggulan: {highlight}
    Link Pembelian: {aff_link}

    Tugas:
    Buat 1 postingan Threads ala rekomendasi racun belanja organik.
    Format output HARUS persis seperti di bawah ini, dipisahkan kata KODE_SPLIT:
    [Teks Postingan Utama]
    KODE_SPLIT
    [Teks Balasan / Reply Berisi Link]

    Aturan Postingan Utama:
    - Fokus pada masalah menyebalkan sehari-hari yang diselesaikan oleh produk ini.
    - Maksimal 300 karakter.
    - Jangan sebut link di postingan utama.

    Aturan Teks Balasan:
    - Ulasan ringkas jujur kenapa barang ini bermanfaat.
    - Cantumkan link pembelian persis: {aff_link}
    - Maksimal 250 karakter.
    """
    res = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    parts = res.text.split("KODE_SPLIT")
    if len(parts) >= 2:
        return parts[0].strip().replace('"', ''), parts[1].strip().replace('"', '')
    return res.text.strip(), f"Yang butuh link tokonya ada di sini ya: {aff_link}"

def main():
    if not SPREADSHEET_ID or not GCP_CREDS_BASE64 or not AI_API_KEY:
        logger.error("Kredensial Environment Variables belum lengkap.")
        return

    gc = get_sheets_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    ai = get_ai_client()

    # 1. Baca Konfigurasi
    cfg_ws = sh.worksheet("Config")
    cfg = {r[0].strip(): r[1].strip() for r in cfg_ws.get_all_values() if len(r) >= 2 and r[0].strip()}
    
    count_viral = int(cfg.get("count_viral", 3))
    count_text = int(cfg.get("count_aff_text", 3))
    count_media = int(cfg.get("count_aff_media", 4))
    interval_hours = int(cfg.get("post_interval_hours", 2))

    # 2. Baca Akun Aktif
    acc_ws = sh.worksheet("Accounts")
    accounts = [r["name"].strip() for r in acc_ws.get_all_records() if r.get("name")]
    if not accounts:
        logger.error("Tidak ada akun yang ditemukan di tab Accounts.")
        return

    # 3. Baca Tab Products
    prod_ws = sh.worksheet("Products")
    all_products = prod_ws.get_all_records()
    ready_products = [p for p in all_products if str(p.get("status", "")).strip().upper() == "READY"]

    prods_with_media = [p for p in ready_products if str(p.get("media_url", "")).strip()]
    prods_text_only = [p for p in ready_products if not str(p.get("media_url", "")).strip()]

    selected_posts = []

    # A. Buat Konten Viral Booster
    for _ in range(count_viral):
        main_txt = generate_viral_content(ai)
        selected_posts.append({
            "main_text": main_txt,
            "media_url": "",
            "reply_text": "",
            "type": "VIRAL"
        })

    # B. Buat Konten Produk Teks Saja
    chosen_text_prods = random.sample(prods_text_only, min(count_text, len(prods_text_only)))
    for p in chosen_text_prods:
        m_txt, r_txt = generate_affiliate_content(ai, p["product_name"], p["highlight"], p["affiliate_link"])
        selected_posts.append({
            "main_text": m_txt,
            "media_url": "",
            "reply_text": r_txt,
            "type": "AFF_TEXT"
        })

    # C. Buat Konten Produk Media (Foto / Video)
    chosen_media_prods = random.sample(prods_with_media, min(count_media, len(prods_with_media)))
    for p in chosen_media_prods:
        m_txt, r_txt = generate_affiliate_content(ai, p["product_name"], p["highlight"], p["affiliate_link"])
        selected_posts.append({
            "main_text": m_txt,
            "media_url": str(p.get("media_url", "")).strip(),
            "reply_text": r_txt,
            "type": "AFF_MEDIA"
        })

    # 4. Acak Urutan Konten (Supaya Alami dan Tidak Monoton)
    random.shuffle(selected_posts)

    # 5. Pasang Jadwal dan Akun Target, Lalu Simpan ke Tab Data
    data_ws = sh.worksheet("data")
    start_time = datetime.now(TZ) + timedelta(minutes=15)

    rows_to_insert = []
    for i, post in enumerate(selected_posts):
        schedule_dt = start_time + timedelta(hours=i * interval_hours)
        acc_target = accounts[i % len(accounts)]  # Rotasi bergiliran antar 4 akun

        rows_to_insert.append([
            schedule_dt.strftime("%Y-%m-%d"),
            schedule_dt.strftime("%H:%M"),
            acc_target,
            post["main_text"],
            post["media_url"],
            post["reply_text"],
            f"Tipe: {post['type']}",
            "PENDING",
            "",
            "",
            ""
        ])

    for row in rows_to_insert:
        data_ws.append_row(row)

    logger.info(f"Berhasil menambahkan {len(rows_to_insert)} draf antrean postingan ke tab data.")

if __name__ == "__main__":
    main()
