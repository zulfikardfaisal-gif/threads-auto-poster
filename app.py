import os
import re
import json
import base64
import random
import time as time_lib
from datetime import datetime, time, date, timedelta
import pytz
import requests
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# --- KONFIGURASI HALAMAN ---
st.set_page_config(
    page_title="Threads Marketing Dashboard",
    page_icon="🧵",
    layout="wide"
)

TZ = pytz.timezone("Asia/Jakarta")

# --- AMBIL KREDENSIAL ---
def get_secret(key, default=""):
    if key in st.secrets:
        return st.secrets[key]
    return os.environ.get(key, default)

SPREADSHEET_ID = get_secret("SPREADSHEET_ID")
GCP_CREDS_BASE64 = get_secret("GCP_CREDS_BASE64")
AI_API_KEY = get_secret("AI_API_KEY")

# --- MATRIKS PILAR TOPIK THREADS DINAMIS ---
TOPIC_PILLARS = {
    "DEBAT_SEPELE": [
        "perdebatan kebiasaan sepele sehari-hari yang bikin orang terbagi jadi dua kubu sengit",
        "etika makan atau nongkrong yang sering bikin salah paham tapi jarang dibahas terbuka",
        "kebiasaan chatting yang diam-diam bikin jengkel (misal: kirim voice note 5 menit atau cuma 'P')"
    ],
    "SOSIAL_DEWASA": [
        "realita pertemanan di usia 25 tahun ke atas dan susahnya mencocokkan jadwal nongkrong",
        "momen ketika energi sosial (social battery) habis total dan pengen langsung pulang",
        "seni menetapkan batasan ke orang lain (subtle cut-off) tanpa merasa bersalah"
    ],
    "DUNIA_KERJA": [
        "situasi rapat kantor berjam-jam yang sebenernya bisa selesai dalam satu baris email",
        "perasaan serba salah waktu libur/cuti tapi notifikasi grup kantor masih muncul",
        "dilema perfeksionis vs yang penting tugas kelar tepat waktu biar cepat pulang"
    ],
    "RUMAH_KOST": [
        "masalah sepele di rumah atau kamar kost yang sering diabaikan padahal bikin emosi tiap hari",
        "perjuangan menjaga kerapian meja kerja atau kamar saat lagi sibuk-sibuknya",
        "barang printilan rumah tangga yang baru disadari faedahnya setelah dicoba sendiri"
    ],
    "JALANAN_KOMUTER": [
        "drama transportasi umum, antrean KRL/MRT, atau macetnya jalanan kota",
        "tipe orang menyebalkan yang sering ditemui di tempat umum atau transportasi massal"
    ],
    "NOSTALGIA_POPKULTUR": [
        "kebiasaan anak zaman rental PS dulu yang sekarang udah gak relevan lagi buat anak sekarang",
        "perbedaan cara kita menikmati waktu luang zaman sekolah vs pas udah kerja"
    ]
}

# --- PEMBERSIH GAYA BAHASA SALES ---
PROMPT_RULES_CLEAN = """
DILARANG KERAS (BLACKLIST TOPIK & KATA):
1. JANGAN PERNAH bahas: gaji, tanggal tua, bokek, reksadana, tabungan, investasi, bayar kos, biaya hidup, atau hitung-hitungan uang.
2. JANGAN PERNAH pakai format tanya-jawab brosur iklan (Contoh terlarang: 'Pusing dengan X? Y solusinya!', 'Lagi butuh X?').
3. DILARANG pakai kata klise sales: 'solusinya', 'cukup dengan...', 'dijamin', 'hadir untuk Anda', 'yuk buruan'.

PANDUAN GAYA (ORGANIK THREADS):
- Gunakan sudut pandang orang pertama ('aku', 'kirain', 'jujur baru sadar').
- Tulis dengan santai, mengalir, celetukan sarkas tipis, atau keluhan nyata khas obrolan linimasa Threads.
- Dilarang pakai hashtag (#), dilarang pakai tanda kutip dua.
"""

VIRAL_HOOK_PATTERNS = [
    "Pola 'Skeptis ke Plot Twist': Awali dengan mengira barang ini awalnya cuma gimik marketing atau gak penting, tapi pas dipakai ternyata ngebantu banget.",
    "Pola 'Underrated Discovery': Awali dengan rasa heran atau penasaran kenapa barang ini baru disadari fungsinya sekarang padahal praktis banget.",
    "Pola 'Daily Frustration': Awali dengan masalah sepele harian yang sering bikin repot sebelum nemu solusi simpel ini.",
    "Pola 'Investasi Kecil Faedah Gede': Awali dengan nada rekomendasi bahwa dengan harga terjangkau manfaatnya berasa banget buat jangka panjang.",
    "Pola 'Statement Tegas Singkat': Awali dengan 1 kalimat pendek to-the-point yang bikin orang penasaran membaca lanjutannya.",
    "Pola 'Curhat Solutif': Awali dengan pengalaman setelah sering salah beli atau gonta-ganti barang, akhirnya nemu yang beneran awet.",
    "Pola 'Spill Santai': Awali seperti lagi spill rahasia printilan berguna ke teman tongkrongan."
]

CLOSING_NARRATIVES = [
    "Btw banyak yang nanya di DM, ini link toko resmi tempat aku beli ya mumpung masih promo:",
    "Biar gak salah beli atau dapet yang zonk, aku taro link official store-nya di sini ya:",
    "Yang mau samaan atau sekadar cek review pembeli lainnya, langsung kepoin di sini:",
    "Spill link belinya di sini ya guys, kemarin pas aku cek lagi ada diskon lumayan:",
    "Daripada ribet nyari tokonya satu-satu, langsung meluncur ke toko resminya di sini:",
    "Kalo mau checkout mending sekarang sebelum kehabisan stok, link belinya di sini:",
    "Kemarin dapet harga flash sale di toko ini dan pengirimannya cepet, linknya:",
    "Biar dapet garansi resmi dan barang original, belinya lewat link ini ya:",
    "Buat yang minta spill racunnya, ini link toko terpercaya yang sering aku pake:",
    "Yang mau CO taro keranjang dulu aja, mumpung vouchernya masih aktif di sini:"
]

def extract_threads_media_id(input_str: str) -> str:
    cleaned = str(input_str).strip()
    if cleaned.isdigit():
        return cleaned
    m = re.search(r"(?:post|t)/([A-Za-z0-9_-]+)", cleaned)
    if not m:
        return cleaned
    shortcode = m.group(1)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    try:
        for char in shortcode:
            media_id = media_id * 64 + alphabet.index(char)
        return str(media_id)
    except Exception:
        return shortcode

# --- KONEKSI GOOGLE SHEETS ---
@st.cache_resource
def get_gspread_client():
    if not GCP_CREDS_BASE64:
        return None
    try:
        creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
        creds = Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Gagal otentikasi Service Account: {e}")
        return None

def get_spreadsheet():
    client = get_gspread_client()
    if not client or not SPREADSHEET_ID:
        return None
    return client.open_by_key(SPREADSHEET_ID)

# --- CACHE DATA READING ---
@st.cache_data(ttl=60)
def load_all_sheets_data():
    client = get_gspread_client()
    if not client or not SPREADSHEET_ID:
        return {}, [], [], []
    
    sh_obj = client.open_by_key(SPREADSHEET_ID)

    cfg_data = {}
    try:
        cfg_rows = sh_obj.worksheet("Config").get_all_values()
        for r in cfg_rows:
            if len(r) >= 2 and r[0].strip():
                cfg_data[r[0].strip()] = r[1].strip()
    except Exception:
        pass

    try:
        prod_rows = sh_obj.worksheet("Products").get_all_records()
    except Exception:
        prod_rows = []

    try:
        acc_rows = sh_obj.worksheet("Accounts").get_all_records()
    except Exception:
        acc_rows = []

    try:
        data_rows = sh_obj.worksheet("data").get_all_records()
    except Exception:
        data_rows = []

    return cfg_data, prod_rows, acc_rows, data_rows

def parse_dt(s: str) -> datetime:
    cleaned = str(s).strip().replace("'", "")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})$", cleaned)
    if not m:
        raise ValueError(f"Format tidak valid: {s}")
    y, mo, d, h, mi = map(int, m.groups())
    return TZ.localize(datetime(y, mo, d, h, mi))

def generate_slots(total_count, start_time_str="08:15", end_time_str="21:30"):
    if total_count <= 0:
        return []
    if total_count == 1:
        return ["12:00"]
    h1, m1 = map(int, start_time_str.split(":"))
    h2, m2 = map(int, end_time_str.split(":"))
    start_minutes = h1 * 60 + m1
    end_minutes = h2 * 60 + m2
    step = (end_minutes - start_minutes) / (total_count - 1)
    slots = []
    for i in range(total_count):
        curr_m = 5 * round(int(round(start_minutes + i * step)) / 5)
        slots.append(f"{curr_m // 60:02d}:{curr_m % 60:02d}")
    return slots

def arrange_post_types_3way(num_viral: int, num_text: int, num_video: int) -> list:
    total = num_viral + num_text + num_video
    if total == 0:
        return []
    buckets = [["viral"] * num_viral, ["video"] * num_video, ["text"] * num_text]
    res = []
    while any(buckets):
        for b in buckets:
            if b:
                res.append(b.pop(0))
    return res[:total]

def prepare_media_for_post(media_raw: str, duplicate_single_video: bool = True) -> str:
    if not media_raw:
        return ""
    parts = [p.strip() for p in str(media_raw).split(",") if p.strip()]
    if len(parts) == 1 and duplicate_single_video:
        single = parts[0]
        is_video = any(single.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in single
        if is_video:
            return f"{single}, {single}"
    return ", ".join(parts)

def get_random_dynamic_topic() -> tuple[str, str]:
    category = random.choice(list(TOPIC_PILLARS.keys()))
    sub_topic = random.choice(TOPIC_PILLARS[category])
    angles = [
        "Celetukan keheranan santai khas tongkrongan",
        "Pengakuan kebiasaan unik yang ternyata banyak dialami orang lain",
        "Opini pemantik yang memancing dua kubu warganet berkomentar",
        "Keluhan relatable terhadap situasi tersebut tanpa nada menggurui"
    ]
    return sub_topic, random.choice(angles)

def pick_length_by_bias(bias: str) -> str:
    lengths = ["Pendek", "Sedang", "Panjang"]
    if "Dominan Pendek" in bias:
        weights = [0.60, 0.25, 0.15]
    elif "Dominan Panjang" in bias:
        weights = [0.15, 0.25, 0.60]
    elif "Dominan Sedang" in bias:
        weights = [0.20, 0.60, 0.20]
    else:
        weights = [0.33, 0.34, 0.33]
    return random.choices(lengths, weights=weights, k=1)[0]

def get_length_prompt_desc(length_opt: str) -> str:
    if "Pendek" in length_opt:
        return "Tulis sangat ringkas, padat, dan to-the-point (maksimal 100-120 karakter, 1-2 kalimat saja)."
    elif "Panjang" in length_opt:
        return "Tulis lebih panjang, detail, dan mengalir seperti curhat mendalam (sekitar 300-450 karakter)."
    else:
        return "Tulis dengan panjang sedang standar Threads (sekitar 180-250 karakter)."

def resolve_reply_count(reply_mode: str) -> int:
    if "1 - 3" in reply_mode:
        return random.randint(1, 3)
    elif "1 - 5" in reply_mode:
        return random.randint(1, 5)
    elif "2 - 4" in reply_mode:
        return random.randint(2, 4)
    m = re.search(r"\d+", str(reply_mode))
    if m:
        return max(1, int(m.group()))
    return 1

def resolve_style_desc(style_opt: str) -> str:
    if "Serahkan ke AI" in style_opt:
        pool = [
            "Curhat Santai & Relate (Bahasa Threads anak muda)",
            "Storytelling Pengalaman Pribadi (Masalah -> Solusi)",
            "Review Jujur & Solutif (Highlight keunggulan produk)",
            "Racun Belanja Shopee (Antusias & bikin pengen checkout)"
        ]
        return f"Gaya bahasa: {random.choice(pool)}"
    return f"Gaya bahasa: {style_opt}"

def update_config_keys(sh_obj, kv_pairs: dict):
    cfg_ws = sh_obj.worksheet("Config")
    all_vals = cfg_ws.get_all_values()
    existing_keys = {}
    for row_idx, r in enumerate(all_vals, start=1):
        if r and r[0].strip():
            existing_keys[r[0].strip()] = row_idx
    for k, v in kv_pairs.items():
        if k in existing_keys:
            cfg_ws.update_cell(existing_keys[k], 2, str(v))
        else:
            cfg_ws.append_row([k, str(v)])

# --- CALL GEMINI REST API ---
def call_gemini_core(prompt: str) -> tuple:
    key = str(AI_API_KEY).strip().replace("'", "").replace('"', "") if AI_API_KEY else ""
    if not key:
        raise Exception("API Key Gemini (AI_API_KEY) belum disetel!")

    available_pairs = []
    errors = []

    for ver in ["v1", "v1beta"]:
        list_url = f"https://generativelanguage.googleapis.com/{ver}/models?key={key}"
        try:
            r = requests.get(list_url, timeout=10)
            res = r.json()
            if "models" in res:
                for m in res["models"]:
                    if "generateContent" in m.get("supportedGenerationMethods", []):
                        m_name = m["name"].replace("models/", "")
                        available_pairs.append((ver, m_name))
            elif "error" in res:
                errors.append(f"{ver}: {res['error'].get('message', str(res['error']))}")
        except Exception as e:
            errors.append(f"{ver}: {str(e)}")

    preferred = ["1.5-flash", "2.0-flash", "flash", "1.5-pro", "gemini-pro"]
    def get_rank(pair):
        v, n = pair
        for idx, pref in enumerate(preferred):
            if pref in n.lower():
                return idx
        return 99

    available_pairs.sort(key=get_rank)

    if available_pairs:
        for ver, m_name in available_pairs:
            gen_url = f"https://generativelanguage.googleapis.com/{ver}/models/{m_name}:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            try:
                r = requests.post(gen_url, json=payload, timeout=30)
                res = r.json()
                if "candidates" in res and res["candidates"]:
                    return res["candidates"][0]["content"]["parts"][0]["text"].strip(), f"{ver}/{m_name}"
            except Exception:
                continue

    fallback_models = [
        ("v1", "gemini-1.5-flash"),
        ("v1", "gemini-1.5-pro"),
        ("v1beta", "gemini-1.5-flash"),
        ("v1beta", "gemini-2.0-flash"),
        ("v1beta", "gemini-1.5-flash-latest"),
    ]
    for ver, m_name in fallback_models:
        gen_url = f"https://generativelanguage.googleapis.com/{ver}/models/{m_name}:generateContent?key={key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            r = requests.post(gen_url, json=payload, timeout=30).json()
            if "candidates" in res and res["candidates"]:
                return res["candidates"][0]["content"]["parts"][0]["text"].strip(), f"{ver}/{m_name}"
            if "error" in res:
                errors.append(f"{ver}/{m_name}: {res['error'].get('message', str(res))}")
        except Exception as e:
            errors.append(f"{ver}/{m_name}: {str(e)}")

    err_msg = " | ".join(errors[:2]) if errors else "Gagal menghubungi Gemini API."
    raise Exception(err_msg)

def call_gemini(prompt: str) -> str:
    text, _ = call_gemini_core(prompt)
    return text

def generate_affiliate_replies(prod_name: str, prod_hl: str, aff_link: str, reply_count: int) -> list:
    if reply_count <= 0 or not aff_link:
        return []

    try:
        prompt_closing = (
            f"Tulis 1 kalimat pengantar santai dan natural (maksimal 70 karakter) sebelum spill link toko pembelian '{prod_name}'. "
            f"Contoh tema: info official store, voucher diskon toko, atau alasan checkout mumpung ready. "
            f"Tanpa hashtag, tanpa tanda kutip, dan JANGAN tulis link-nya."
        )
        closing_intro = call_gemini(prompt_closing).strip().strip('"').strip("'")
        if not closing_intro or len(closing_intro) > 100:
            closing_intro = random.choice(CLOSING_NARRATIVES)
    except Exception:
        closing_intro = random.choice(CLOSING_NARRATIVES)

    final_reply = f"{closing_intro}\n{aff_link}"

    if reply_count == 1:
        return [final_reply]

    intermediate_count = reply_count - 1
    prompt_intermediate = (
        f"Untuk postingan Threads tentang produk '{prod_name}' (Keunggulan: '{prod_hl}'). "
        f"Tulis persis {intermediate_count} tweet balasan pendek lanjutan yang menyambung secara bertahap (sebelum spill link). "
        f"Gaya santai, relate, jujur seperti curhat pengalaman pakai. Pisahkan setiap balasan dengan tanda '---'. "
        f"Maksimal 120 karakter per balasan. DILARANG pakai hashtag dan JANGAN sebutkan link."
    )
    try:
        raw_res = call_gemini(prompt_intermediate)
        parts = [p.strip() for p in raw_res.split("---") if p.strip()]
        replies = parts[:intermediate_count]
        while len(replies) < intermediate_count:
            replies.append("Worth it banget sih ini buat pemakaian jangka panjang.")
    except Exception:
        replies = ["Barang ini praktis banget buat kebutuhan sehari-hari." for _ in range(intermediate_count)]

    replies.append(final_reply)
    return replies

def check_threads_token(user_id: str, access_token: str) -> dict:
    url = f"https://graph.threads.net/v1.0/me?fields=id,username,threads_profile_picture_url&access_token={access_token}"
    try:
        r = requests.get(url, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": {"message": str(e)}}

# Tampilan Header
c_head1, c_head2 = st.columns([4, 1])
with c_head1:
    st.title("🧵 Threads Affiliate & Autopilot Dashboard")
    st.caption("Pusat kendali konten autopilot, manual & Thread Hijacking (Komentar Video di Postingan Viral).")
with c_head2:
    if st.button("🔄 Segarkan Data Sheets"):
        st.cache_data.clear()
        st.rerun()

# Load Data dari Cache
try:
    cfg_data, raw_prods, acc_records, all_data = load_all_sheets_data()
except Exception as e:
    if "429" in str(e):
        st.error("⏳ Google Sheets API terkena jeda kuota request. Tunggu 30-60 detik lalu klik '🔄 Segarkan Data Sheets'.")
    else:
        st.error(f"Gagal memuat data Google Sheets: {e}")
    st.stop()

# Tab Menu
tabs = st.tabs([
    "⚡ Kontrol Autopilot",
    "🎯 Hijack Thread Viral",
    "📦 Katalog Produk (50+ Items)",
    "✍️ Content Studio (Manual & AI)",
    "📋 Antrean & Riwayat",
    "⚙️ Akun Threads & Diagnostik"
])

# ==============================================================================
# TAB 1: KONTROL AUTOPILOT
# ==============================================================================
with tabs[0]:
    st.subheader("Pengaturan Jadwal & Komposisi Harian Autopilot")
    st.write("Atur tanggal aktif, rasio harian (**Video**, **Teks**, **Viral**), dan **gaya penulisan AI**.")

    raw_start = cfg_data.get("start_datetime", "2026-09-06 00:00")
    raw_end = cfg_data.get("end_datetime", "2026-09-30 22:00")
    
    try:
        cur_viral_count = int(cfg_data.get("daily_viral_count", 2))
    except:
        cur_viral_count = 2

    try:
        cur_text_count = int(cfg_data.get("daily_text_count", 2))
    except:
        cur_text_count = 2

    try:
        cur_video_count = int(cfg_data.get("daily_video_count", 4))
    except:
        cur_video_count = 4

    cur_target_acc = cfg_data.get("target_autopilot_account", "-- Semua Akun (All Accounts) --")
    cur_reply_mode = cfg_data.get("reply_mode", "🎲 Acak (1 - 3 Balasan)")
    cur_length_bias = cfg_data.get("length_bias", "Dominan Sedang (Lebih banyak standar)")
    cur_ai_style = cfg_data.get("ai_style", "Serahkan ke AI (Smart Adaptive / Acak Tiap Post)")

    now = datetime.now(TZ)
    try:
        cur_start = parse_dt(raw_start)
        cur_end = parse_dt(raw_end)
        is_active = (cur_start.date() <= now.date() <= cur_end.date())
    except Exception:
        is_active = False

    col_stat1, col_stat2 = st.columns(2)
    with col_stat1:
        if is_active:
            st.success(f"🟢 **STATUS: AUTOPILOT AKTIF**\n\nWaktu sekarang: `{now.strftime('%Y-%m-%d %H:%M:%S')} WIB`")
        else:
            st.warning(f"🔴 **STATUS: AUTOPILOT NON-AKTIF / DI LUAR JADWAL**\n\nWaktu sekarang: `{now.strftime('%Y-%m-%d %H:%M:%S')} WIB`")
    with col_stat2:
        total_daily = cur_viral_count + cur_text_count + cur_video_count
        st.info(
            f"**Konfigurasi Tersimpan Saat Ini:**\n"
            f"- Jadwal: `{raw_start} WIB` s/d `{raw_end} WIB`\n"
            f"- Kuota Harian: **{total_daily} Konten** ({cur_video_count} Video + {cur_text_count} Teks + {cur_viral_count} Viral)\n"
            f"- Target Akun: `{cur_target_acc}`"
        )

    st.divider()

    st.write("#### 🛠️ Sesuaikan Jadwal & Komposisi Harian")
    acc_names_all = [str(a["name"]).strip() for a in acc_records if str(a.get("name", "")).strip()]
    auto_acc_options = ["-- Semua Akun (All Accounts) --"] + acc_names_all

    col_ap_acc, _ = st.columns([2, 2])
    with col_ap_acc:
        def_acc_idx = auto_acc_options.index(cur_target_acc) if cur_target_acc in auto_acc_options else 0
        sel_auto_acc = st.selectbox("🎯 Target Akun Autopilot", auto_acc_options, index=def_acc_idx)

    col_d1, col_t1 = st.columns(2)
    with col_d1:
        try:
            def_start_date = parse_dt(raw_start).date()
        except:
            def_start_date = now.date()
        start_d = st.date_input("Tanggal Mulai (Start Date)", value=def_start_date)
    with col_t1:
        try:
            def_start_time = parse_dt(raw_start).time()
        except:
            def_start_time = time(0, 0)
        start_t = st.time_input("Jam Mulai (Start Time)", value=def_start_time)

    col_d2, col_t2 = st.columns(2)
    with col_d2:
        try:
            def_end_date = parse_dt(raw_end).date()
        except:
            def_end_date = now.date()
        end_d = st.date_input("Tanggal Berakhir (End Date)", value=def_end_date)
    with col_t2:
        try:
            def_end_time = parse_dt(raw_end).time()
        except:
            def_end_time = time(22, 0)
        end_t = st.time_input("Jam Berakhir (End Time)", value=def_end_time)

    st.write("##### 🎯 Porsi Konten Harian (Video, Teks, Viral)")
    col_q1, col_q2, col_q3 = st.columns(3)
    with col_q1:
        sel_video = st.number_input("🎬 Produk Video / Gambar", 0, 15, cur_video_count)
    with col_q2:
        sel_text = st.number_input("📝 Produk Teks Saja", 0, 15, cur_text_count)
    with col_q3:
        sel_viral = st.number_input("🚀 Konten Viral Booster", 0, 10, cur_viral_count)

    auto_dup_video = st.checkbox("🎬 Trik Thumbnail: Jika produk hanya punya 1 video, kirim jadi 2 video (Carousel)", value=True)

    col_st1, col_st2 = st.columns(2)
    with col_st1:
        ai_style_options = [
            "Serahkan ke AI (Smart Adaptive / Acak Tiap Post)",
            "Curhat Santai & Relate (Bahasa Threads anak muda)",
            "Storytelling Pengalaman Pribadi (Masalah -> Solusi)",
            "Review Jujur & Solutif (Highlight keunggulan produk)",
            "Racun Belanja Shopee (Antusias & bikin pengen checkout)"
        ]
        def_style_idx = ai_style_options.index(cur_ai_style) if cur_ai_style in ai_style_options else 0
        sel_ai_style_ap = st.selectbox("🎨 Gaya Bahasa AI (Autopilot)", ai_style_options, index=def_style_idx)
    with col_st2:
        bias_options = [
            "Dominan Sedang (Lebih banyak standar)",
            "Dominan Pendek (Lebih banyak ringkas)",
            "Dominan Panjang (Lebih banyak storytelling)",
            "Acak Seimbang (Rata: Pendek, Sedang, Panjang)"
        ]
        def_bias_idx = bias_options.index(cur_length_bias) if cur_length_bias in bias_options else 0
        sel_bias_autopilot = st.selectbox("🎲 Pola Panjang Teks", bias_options, index=def_bias_idx)

    st.write("##### 💬 Format Rantai Balasan (Reply Chain)")
    reply_mode_options = [
        "🎲 Acak (1 - 3 Balasan)",
        "🎲 Acak (1 - 5 Balasan)",
        "🎲 Acak (2 - 4 Balasan)",
        "1 Balasan (Hanya Link)",
        "2 Balasan (1 Cerita + Link)",
        "3 Balasan (2 Cerita + Link)"
    ]
    def_rep_idx = reply_mode_options.index(cur_reply_mode) if cur_reply_mode in reply_mode_options else 0
    sel_reply_mode_ap = st.selectbox("Pilih Format Balasan Utas:", reply_mode_options, index=def_rep_idx)

    total_plan = sel_video + sel_text + sel_viral
    preview_slots = generate_slots(total_plan)
    preview_types = arrange_post_types_3way(sel_viral, sel_text, sel_video)

    type_labels = {"viral": "Viral 🚀", "video": "Video 🎬", "text": "Teks 📝"}
    st.caption(f"💡 **Total:** {total_plan} post/hari ({sel_video} Video, {sel_text} Teks, {sel_viral} Viral).")
    slot_badges = [f"`{preview_slots[i]} ({type_labels.get(preview_types[i], 'Post')})`" for i in range(total_plan)]
    st.markdown("🕒 **Distribusi Jam:** " + " ➜ ".join(slot_badges))

    if st.button("💾 Simpan Pengaturan Autopilot", type="primary"):
        sh_obj = get_spreadsheet()
        if sh_obj:
            try:
                new_start_str = f"'{start_d.strftime('%Y-%m-%d')} {start_t.strftime('%H:%M')}"
                new_end_str = f"'{end_d.strftime('%Y-%m-%d')} {end_t.strftime('%H:%M')}"
                
                update_config_keys(sh_obj, {
                    "start_datetime": new_start_str,
                    "end_datetime": new_end_str,
                    "daily_viral_count": str(sel_viral),
                    "daily_text_count": str(sel_text),
                    "daily_video_count": str(sel_video),
                    "target_autopilot_account": str(sel_auto_acc),
                    "reply_mode": str(sel_reply_mode_ap),
                    "length_bias": str(sel_bias_autopilot),
                    "ai_style": str(sel_ai_style_ap)
                })
                st.cache_data.clear()
                st.success("✅ Pengaturan autopilot tersimpan!")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menyimpan: {e}")

    st.divider()

    st.write("#### ⚡ Eksekusi Cepat: Generate Konten Hari Ini")
    if st.button("🚀 Generate Konten Autopilot Sekarang"):
        sh_obj = get_spreadsheet()
        if not sh_obj:
            st.error("Spreadsheet tidak tersedia.")
        else:
            with st.spinner(f"Sedang meracik {total_plan} konten via Gemini AI..."):
                try:
                    if not acc_records:
                        st.error("Daftarkan akun di tab Akun Threads terlebih dahulu.")
                    else:
                        target_accounts_to_run = acc_names_all if sel_auto_acc == "-- Semua Akun (All Accounts) --" else [sel_auto_acc]
                        ready_prods = [p for p in raw_prods if str(p.get("status", "")).strip().upper() == "READY"]
                        prods_with_media = [p for p in ready_prods if str(p.get("media_url", "")).strip()]
                        prods_text_only = [p for p in ready_prods if not str(p.get("media_url", "")).strip()] or ready_prods

                        today_str = now.strftime("%Y-%m-%d")
                        data_ws = sh_obj.worksheet("data")
                        new_rows = []

                        for acc_target in target_accounts_to_run:
                            sampled_video = random.sample(prods_with_media, min(sel_video, len(prods_with_media))) if (sel_video > 0 and prods_with_media) else []
                            sampled_text = random.sample(prods_text_only, min(sel_text, len(prods_text_only))) if (sel_text > 0 and prods_text_only) else []

                            v_idx = 0
                            t_idx = 0

                            for slot_time, p_type in zip(preview_slots, preview_types):
                                chosen_len = pick_length_by_bias(sel_bias_autopilot)
                                len_desc = get_length_prompt_desc(chosen_len)
                                style_desc = resolve_style_desc(sel_ai_style_ap)

                                if p_type == "viral":
                                    sub_top, angle_top = get_random_dynamic_topic()
                                    prompt_v = (
                                        f"Tulis 1 postingan Threads bahasa Indonesia tentang: '{sub_top}'.\n"
                                        f"Sudut pandang: {angle_top}.\n{PROMPT_RULES_CLEAN}\n{len_desc}\n"
                                        f"Maksimal 220 karakter. Langsung tulis teks postingan tanpa tanda kutip."
                                    )
                                    v_text = call_gemini(prompt_v)
                                    new_rows.append([today_str, slot_time, acc_target, v_text, "", "", "", "PENDING", "", "", "", ""])

                                elif p_type == "video":
                                    prod = sampled_video[v_idx] if v_idx < len(sampled_video) else random.choice(ready_prods)
                                    v_idx += 1
                                    chosen_hook = random.choice(VIRAL_HOOK_PATTERNS)
                                    prompt_a = (
                                        f"Tulis 1 postingan Threads bahasa Indonesia yang memancing rasa penasaran penonton video untuk: '{prod['product_name']}' "
                                        f"(Keunggulan: {prod.get('highlight', '')}).\n- Format Pembuka: {chosen_hook}.\n- {style_desc}.\n- {len_desc}.\n"
                                        f"- ATURAN PENTING: DILARANG keras bergaya brosur jualan. DILARANG pakai hashtag dan tanda kutip."
                                    )
                                    main_txt = call_gemini(prompt_a)
                                    act_rep_count = resolve_reply_count(sel_reply_mode_ap)
                                    replies_chain = generate_affiliate_replies(prod['product_name'], prod.get('highlight', ''), prod['affiliate_link'], act_rep_count)
                                    raw_m = str(prod.get("media_url", "")).strip()
                                    final_media = prepare_media_for_post(raw_m, duplicate_single_video=auto_dup_video)

                                    new_rows.append([today_str, slot_time, acc_target, main_txt, final_media, "\n---REPLY---\n".join(replies_chain), prod["affiliate_link"], "PENDING", "", "", "", ""])

                                else:
                                    prod = sampled_text[t_idx] if t_idx < len(sampled_text) else random.choice(ready_prods)
                                    t_idx += 1
                                    chosen_hook = random.choice(VIRAL_HOOK_PATTERNS)
                                    prompt_a = (
                                        f"Tulis 1 postingan Threads bahasa Indonesia rekomendasi teks tanpa gambar untuk: '{prod['product_name']}' "
                                        f"(Keunggulan: {prod.get('highlight', '')}).\n- Format Pembuka: {chosen_hook}.\n- {style_desc}.\n- {len_desc}.\n"
                                        f"- ATURAN PENTING: Tulis santai seperti curhat nyata, tanpa hashtag dan tanpa tanda kutip."
                                    )
                                    main_txt = call_gemini(prompt_a)
                                    act_rep_count = resolve_reply_count(sel_reply_mode_ap)
                                    replies_chain = generate_affiliate_replies(prod['product_name'], prod.get('highlight', ''), prod['affiliate_link'], act_rep_count)

                                    new_rows.append([today_str, slot_time, acc_target, main_txt, "", "\n---REPLY---\n".join(replies_chain), prod["affiliate_link"], "PENDING", "", "", "", ""])

                        for r in new_rows:
                            data_ws.append_row(r)

                        st.cache_data.clear()
                        st.success(f"🎉 Berhasil membuat {len(new_rows)} antrean postingan!")
                        st.rerun()
                except Exception as ex:
                    st.error(f"Terjadi kesalahan: {ex}")

# ==============================================================================
# TAB 2: HIJACK THREAD VIRAL (KOMENTAR VIDEO / TEKS)
# ==============================================================================
with tabs[1]:
    st.subheader("🎯 Thread Hijacking (Nimbrung di Postingan Viral)")
    st.caption("Balas postingan viral orang lain dengan komentar relate + video demo produk dari katalog, lalu selipkan link Shopee di balasan sendiri.")

    all_ready_p = [p for p in raw_prods if str(p.get("status", "")).strip().upper() == "READY"]
    acc_names_all = [str(a["name"]).strip() for a in acc_records if str(a.get("name", "")).strip()]

    c_hj1, c_hj2 = st.columns([2, 1])
    with c_hj1:
        viral_url_input = st.text_input(
            "🔗 Link Postingan Threads Viral atau Numeric Post ID:",
            placeholder="Contoh: https://www.threads.net/@namauser/post/C9xyz123 atau ID angka"
        )
    with c_hj2:
        hj_account = st.selectbox("Akun untuk Membalas:", acc_names_all if acc_names_all else ["Belum ada akun"], key="hj_acc")

    resolved_post_id = extract_threads_media_id(viral_url_input) if viral_url_input else ""
    if resolved_post_id:
        st.info(f"Target Media ID Terdeteksi: `{resolved_post_id}`")

    st.write("##### 💬 Konteks Obrolan di Postingan Viral Tersebut")
    viral_context_input = st.text_area(
        "Ceritakan singkat masalah/keluhan yang sedang ramai di postingan itu:",
        placeholder="Contoh: Banyak yang ngeluh wajan teflonnya gampang ngelupas dan lengket, atau pada debat capek nyikatin celah keramik kamar mandi yang berlumut.",
        height=90
    )

    col_hp1, col_hp2 = st.columns(2)
    with col_hp1:
        prod_hijack_options = ["-- Biarkan AI Memilih Otomatis yang Paling Cocok --"] + [p["product_name"] for p in all_ready_p]
        sel_hj_prod = st.selectbox("Pilih Produk Solusi dari Katalog:", prod_hijack_options)
    with col_hp2:
        hj_style = st.selectbox("Gaya Bahasa Nimbrung:", [
            "Curhat Relate & Nimbrung Santai (Khas Warganet Threads)",
            "Plot Twist & Pengalaman Pribadi (Dulu sama, sekarang beres)",
            "Spill Santai Solutif (Bocor Alus)"
        ])

    if st.button("✨ Racik Balasan Nimbrung via AI", type="primary"):
        if not viral_url_input or not resolved_post_id:
            st.error("Link atau ID postingan viral wajib diisi!")
        elif not viral_context_input.strip():
            st.error("Ketik ringkasan konteks postingan viral tersebut!")
        elif not all_ready_p:
            st.error("Katalog produk berstatus READY masih kosong.")
        else:
            with st.spinner("AI sedang menganalisis dan meracik komentar solutif..."):
                try:
                    if sel_hj_prod == "-- Biarkan AI Memilih Otomatis yang Paling Cocok --":
                        katalog_ringkas = "\n".join([f"- {p['product_name']}: {p.get('highlight', '')}" for p in all_ready_p[:20]])
                        prompt_pilih = (
                            f"Dari daftar produk ini:\n{katalog_ringkas}\n\n"
                            f"Pilih 1 produk yang paling NYAMBUNG dan bisa menjadi solusi untuk masalah ini: '{viral_context_input}'. "
                            f"Hanya tulis nama persis produk yang dipilih tanpa penjelasan lain."
                        )
                        terpilih_nama = call_gemini(prompt_pilih).strip().strip('"').strip("'")
                        target_prod = next((p for p in all_ready_p if p["product_name"].lower() in terpilih_nama.lower()), random.choice(all_ready_p))
                    else:
                        target_prod = next((p for p in all_ready_p if p["product_name"] == sel_hj_prod), all_ready_p[0])

                    p_name = target_prod["product_name"]
                    p_hl = target_prod.get("highlight", "")
                    p_link = target_prod.get("affiliate_link", "")
                    p_media = str(target_prod.get("media_url", "")).strip()

                    prompt_hijack = (
                        f"Tulis 1 komentar balasan Threads untuk nimbrung di postingan viral yang membahas: '{viral_context_input}'.\n"
                        f"Solusi yang kamu pakai: produk '{p_name}' (Keunggulan: {p_hl}).\n"
                        f"Gaya: {hj_style}.\n"
                        f"{PROMPT_RULES_CLEAN}\n"
                        f"ATURAN WAJIB:\n"
                        f"1. Seolah-olah kamu warganet biasa yang senasib dan relate.\n"
                        f"2. Ceritakan bagaimana benda kecil ini menyelesaikan masalah itu tanpa terkesan jualan.\n"
                        f"3. DILARANG sebut nama produk secara kaku (sebut 'alat ini', 'benda ini', atau 'printilan ini').\n"
                        f"4. DILARANG sebutkan link atau kata 'yuk beli' di komentar ini.\n"
                        f"Maksimal 180 karakter. Langsung tulis teks komentar tanpa tanda kutip."
                    )
                    main_comment = call_gemini(prompt_hijack)

                    prompt_spill = (
                        f"Tulis 1 baris balasan santai (maksimal 70 karakter) sebelum menyematkan link toko resmi pembelian '{p_name}'. "
                        f"Contoh tema: buat yang nanya di DM beli dimana, atau info toko officialnya. Tanpa hashtag, tanpa link."
                    )
                    spill_intro = call_gemini(prompt_spill).strip().strip('"').strip("'")
                    if not spill_intro or len(spill_intro) > 100:
                        spill_intro = random.choice(CLOSING_NARRATIVES)
                    reply_link_post = f"{spill_intro}\n{p_link}"

                    st.session_state["hijack_draft"] = {
                        "account": hj_account,
                        "target_id": resolved_post_id,
                        "main": main_comment,
                        "media": p_media,
                        "reply": reply_link_post,
                        "link": p_link,
                        "prod_name": p_name
                    }
                    st.success(f"🎉 Komentar balasan berhasil diracik dengan rekomendasi solusi: **{p_name}**!")
                except Exception as ex:
                    st.error(f"Gagal generate: {ex}")

    if "hijack_draft" in st.session_state and st.session_state["hijack_draft"]:
        d = st.session_state["hijack_draft"]
        st.divider()
        st.write("#### 📝 Preview Balasan Komentar & Video")
        st.caption(f"Akan membalas Post ID: `{d['target_id']}` menggunakan akun `{d['account']}`.")

        col_pv1, col_pv2 = st.columns(2)
        with col_pv1:
            d["main"] = st.text_area("1. Komentar Nimbrung (Teks Postingan Balasan):", value=d["main"], height=100)
            d["media"] = st.text_input("Media URL Video/Foto (Cloudinary):", value=d.get("media", ""), help="Kosongkan jika hanya ingin komentar teks saja.")
        with col_pv2:
            d["reply"] = st.text_area("2. Balasan di Bawah Komentar Sendiri (Spill Link):", value=d["reply"], height=100)
            st.info(f"Produk Terpilih: **{d.get('prod_name', '-')}**\n\nLink: `{d.get('link', '-')}`")

        if st.button("🚀 Kirim Komentar (Dengan Video) ke Antrean", type="primary"):
            sh_obj = get_spreadsheet()
            if sh_obj:
                try:
                    data_ws = sh_obj.worksheet("data")
                    now_str_date = now.strftime("%Y-%m-%d")
                    now_str_time = (now - timedelta(minutes=2)).strftime("%H:%M")

                    data_ws.append_row([
                        now_str_date,
                        now_str_time,
                        d["account"],
                        d["main"],
                        d.get("media", ""),
                        d["reply"],
                        d["link"],
                        "PENDING",
                        "", "", "",
                        d["target_id"]
                    ])
                    st.cache_data.clear()
                    st.success("🎉 Berhasil disimpan ke antrean Google Sheets lengkap dengan video! Jalankan Run workflow di GitHub Actions untuk menerbitkan.")
                    st.session_state["hijack_draft"] = None
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menyimpan ke Sheets: {e}")

# ==============================================================================
# TAB 3: KATALOG PRODUK
# ==============================================================================
with tabs[2]:
    st.subheader("📦 Katalog Produk Affiliate")
    expected_cols = ["product_name", "highlight", "affiliate_link", "category", "status", "media_url"]

    if raw_prods:
        df_prods = pd.DataFrame(raw_prods)
        for col in expected_cols:
            if col not in df_prods.columns:
                df_prods[col] = ""
        df_prods = df_prods[expected_cols]
    else:
        df_prods = pd.DataFrame(columns=expected_cols)

    total_items = len(df_prods)
    ready_items = len(df_prods[df_prods["status"].astype(str).str.upper() == "READY"]) if not df_prods.empty else 0
    media_items = len(df_prods[df_prods["media_url"].astype(str).str.strip() != ""]) if not df_prods.empty else 0

    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Total Produk Terdaftar", f"{total_items} Item")
    c_m2.metric("Produk READY", f"{ready_items} Item")
    c_m3.metric("Produk Memiliki Media", f"{media_items} Item")

    st.divider()
    st.write("#### 📝 Edit Langsung di Tabel (Excel Style)")

    edited_df = st.data_editor(
        df_prods,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "product_name": st.column_config.TextColumn("Nama Produk", required=True, width="medium"),
            "highlight": st.column_config.TextColumn("Keunggulan / Highlight", width="large"),
            "affiliate_link": st.column_config.TextColumn("Link Affiliate Shopee", required=True, width="medium"),
            "category": st.column_config.TextColumn("Kategori", width="small"),
            "status": st.column_config.SelectboxColumn("Status", options=["READY", "DRAFT", "ARCHIVED"], required=True, width="small"),
            "media_url": st.column_config.TextColumn("Media URL (Cloudinary)", width="large")
        },
        height=350
    )

    if st.button("💾 Simpan Semua Perubahan Tabel", type="primary"):
        sh_obj = get_spreadsheet()
        if sh_obj:
            with st.spinner("Menyimpan katalog ke Google Sheets..."):
                try:
                    prod_ws = sh_obj.worksheet("Products")
                    prod_ws.clear()
                    header = [expected_cols]
                    data_rows = edited_df.fillna("").values.tolist()
                    prod_ws.update(header + data_rows)
                    st.cache_data.clear()
                    st.success("✅ Katalog berhasil diperbarui sepenuhnya!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal menyimpan ke Sheets: {e}")

# ==============================================================================
# TAB 4: CONTENT STUDIO (MANUAL & AI)
# ==============================================================================
with tabs[3]:
    st.subheader("✍️ Content Studio (Pembuat Konten Manual & AI)")
    st.caption("Pusat pembuatan postingan mandiri atau paket kombinasi harian.")

    if "manual_generated_posts" not in st.session_state:
        st.session_state["manual_generated_posts"] = []

    st.write("#### ⚙️ 1. Pengaturan Jadwal & Frekuensi")
    c_set1, c_set2, c_set3, c_set4 = st.columns(4)
    with c_set1:
        target_account = st.selectbox("🎯 Target Akun Threads", ["-- Semua Akun (All Accounts) --"] + acc_names_all)
    with c_set2:
        schedule_d = st.date_input("📅 Tanggal Mulai", value=now.date(), key="cs_date")
    with c_set3:
        schedule_t = st.time_input("⏰ Jam Mulai", value=now.time(), key="cs_time")
    with c_set4:
        interval_mins = st.selectbox("⏳ Selang Waktu", [60, 120, 180, 240, 360, 480, 720, 1440], index=1, format_func=lambda x: f"{x // 60} Jam Sekali" if x < 1440 else "1 Hari Sekali")

    c_fmt1, c_fmt2 = st.columns(2)
    with c_fmt1:
        manual_length_opt = st.selectbox("📏 Panjang Teks", ["Sedang (180-250 karakter)", "Pendek (max 120 karakter)", "Panjang (300-450 karakter)", "🎲 Acak Sesuai AI"])
    with c_fmt2:
        manual_reply_mode = st.selectbox("💬 Rantai Balasan", ["1 Balasan (Hanya Link)", "2 Balasan (1 Cerita + Link)", "3 Balasan (2 Cerita + Link)", "🎲 Acak (1 - 3 Balasan)"])

    st.divider()
    content_mode = st.radio("Pilih Mode Konten:", ["🎯 Paket Kombinasi Harian (Video + Teks + Viral)", "🛍️ Single Product Affiliate", "🚀 Viral Booster", "✍️ Tulis Bebas Manual"], horizontal=True)

    copy_styles = ["Serahkan ke AI", "Curhat Santai & Relate", "Storytelling Pengalaman Pribadi", "Review Jujur & Solutif", "Racun Belanja Shopee"]

    if content_mode == "🎯 Paket Kombinasi Harian (Video + Teks + Viral)":
        ck1, ck2, ck3 = st.columns(3)
        with ck1:
            cb_video = st.number_input("Jumlah Video", 0, 10, 4, key="cb_v")
        with ck2:
            cb_text = st.number_input("Jumlah Teks", 0, 10, 3, key="cb_t")
        with ck3:
            cb_viral = st.number_input("Jumlah Viral", 0, 10, 3, key="cb_vi")

        cb_style = st.selectbox("Gaya Bahasa AI:", copy_styles, key="cb_style")
        cb_dup_video = st.checkbox("🎬 Trik Thumbnail 2 Video (Carousel)", value=True, key="cb_dup")

        if st.button("✨ Generate Paket Kombinasi via AI", type="primary"):
            prods_with_media = [p for p in all_ready_p if str(p.get("media_url", "")).strip()]
            prods_text_only = [p for p in all_ready_p if not str(p.get("media_url", "")).strip()] or all_ready_p
            tot_combo = cb_video + cb_text + cb_viral

            if tot_combo > 0:
                with st.spinner(f"Meracik {tot_combo} postingan..."):
                    base_dt = datetime.combine(schedule_d, schedule_t)
                    post_types = arrange_post_types_3way(cb_viral, cb_text, cb_video)
                    gen_list = []

                    sampled_video = random.sample(prods_with_media, min(cb_video, len(prods_with_media))) if (cb_video > 0 and prods_with_media) else []
                    sampled_text = random.sample(prods_text_only, min(cb_text, len(prods_text_only))) if (cb_text > 0 and prods_text_only) else []

                    v_i, t_i = 0, 0
                    for idx_c, p_t in enumerate(post_types):
                        p_dt = base_dt + timedelta(minutes=idx_c * interval_mins)
                        act_len = random.choice(["Pendek", "Sedang", "Panjang"]) if "Acak" in manual_length_opt else manual_length_opt
                        len_desc = get_length_prompt_desc(act_len)
                        style_desc = resolve_style_desc(cb_style)

                        if p_t == "viral":
                            sub_top, angle_top = get_random_dynamic_topic()
                            prompt_v = f"Tulis 1 postingan Threads bahasa Indonesia tentang: '{sub_top}'. Sudut pandang: {angle_top}.\n{PROMPT_RULES_CLEAN}\n{len_desc}\nMaksimal 220 karakter tanpa kutip."
                            v_txt = call_gemini(prompt_v)
                            gen_list.append({"date": p_dt.strftime("%Y-%m-%d"), "time": p_dt.strftime("%H:%M"), "account": target_account, "main": v_txt, "media": "", "reply": "", "link": ""})
                        elif p_t == "video":
                            p_cur = sampled_video[v_i] if v_i < len(sampled_video) else random.choice(all_ready_p)
                            v_i += 1
                            prompt_a = f"Tulis 1 postingan Threads bahasa Indonesia pancingan penasaran untuk: '{p_cur['product_name']}' (Keunggulan: {p_cur.get('highlight', '')}).\n{style_desc}.\n{len_desc}.\nDILARANG gaya brosur, tanpa hashtag dan tanpa tanda kutip."
                            m_txt = call_gemini(prompt_a)
                            replies = generate_affiliate_replies(p_cur['product_name'], p_cur.get('highlight', ''), p_cur['affiliate_link'], resolve_reply_count(manual_reply_mode))
                            raw_m = str(p_cur.get("media_url", "")).strip()
                            gen_list.append({"date": p_dt.strftime("%Y-%m-%d"), "time": p_dt.strftime("%H:%M"), "account": target_account, "main": m_txt, "media": prepare_media_for_post(raw_m, duplicate_single_video=cb_dup_video), "reply": "\n---REPLY---\n".join(replies), "link": p_cur["affiliate_link"]})
                        else:
                            p_cur = sampled_text[t_i] if t_i < len(sampled_text) else random.choice(all_ready_p)
                            t_i += 1
                            prompt_a = f"Tulis 1 postingan Threads bahasa Indonesia santai rekomendasi teks untuk: '{p_cur['product_name']}' (Keunggulan: {p_cur.get('highlight', '')}).\n{style_desc}.\n{len_desc}.\nTanpa hashtag dan tanda kutip."
                            m_txt = call_gemini(prompt_a)
                            replies = generate_affiliate_replies(p_cur['product_name'], p_cur.get('highlight', ''), p_cur['affiliate_link'], resolve_reply_count(manual_reply_mode))
                            gen_list.append({"date": p_dt.strftime("%Y-%m-%d"), "time": p_dt.strftime("%H:%M"), "account": target_account, "main": m_txt, "media": "", "reply": "\n---REPLY---\n".join(replies), "link": p_cur["affiliate_link"]})

                    st.session_state["manual_generated_posts"] = gen_list
                    st.success(f"🎉 Berhasil membuat {len(gen_list)} draf!")

    elif content_mode == "🛍️ Single Product Affiliate":
        c_sp1, c_sp2 = st.columns(2)
        with c_sp1:
            sel_sp = st.selectbox("Pilih Produk:", ["-- Acak dari Katalog READY --"] + [p["product_name"] for p in all_ready_p])
        with c_sp2:
            sp_style = st.selectbox("Gaya Bahasa AI:", copy_styles, key="sp_style")
            sp_dup_v = st.checkbox("🎬 Trik Thumbnail 2 Video (Carousel)", value=True, key="sp_dup")

        if st.button("✨ Generate Single Product Post", type="primary"):
            p_cur = random.choice(all_ready_p) if sel_sp == "-- Acak dari Katalog READY --" else next((p for p in all_ready_p if p["product_name"] == sel_sp), all_ready_p[0])
            act_len = random.choice(["Pendek", "Sedang", "Panjang"]) if "Acak" in manual_length_opt else manual_length_opt
            prompt = f"Tulis 1 postingan Threads bahasa Indonesia pancingan penasaran untuk: '{p_cur['product_name']}' (Keunggulan: {p_cur.get('highlight', '')}).\n{PROMPT_RULES_CLEAN}\n{get_length_prompt_desc(act_len)}\nTanpa hashtag dan tanda kutip."
            m_txt = call_gemini(prompt)
            replies = generate_affiliate_replies(p_cur['product_name'], p_cur.get('highlight', ''), p_cur['affiliate_link'], resolve_reply_count(manual_reply_mode))
            raw_m = str(p_cur.get("media_url", "")).strip()

            st.session_state["manual_generated_posts"] = [{
                "date": schedule_d.strftime("%Y-%m-%d"),
                "time": schedule_t.strftime("%H:%M"),
                "account": target_account,
                "main": m_txt,
                "media": prepare_media_for_post(raw_m, duplicate_single_video=sp_dup_v),
                "reply": "\n---REPLY---\n".join(replies),
                "link": p_cur["affiliate_link"]
            }]
            st.success("🎉 Draf berhasil dibuat!")

    elif content_mode == "🚀 Viral Booster":
        vb_topic = st.text_input("Topik (Kosongkan jika ingin acak otomatis):", placeholder="Misal: Etika membatalkan janji mendadak")
        if st.button("✨ Generate Postingan Viral", type="primary"):
            sub_top, angle_top = (vb_topic, "Opini santai") if vb_topic.strip() else get_random_dynamic_topic()
            act_len = random.choice(["Pendek", "Sedang", "Panjang"]) if "Acak" in manual_length_opt else manual_length_opt
            prompt_v = f"Tulis 1 postingan Threads bahasa Indonesia tentang: '{sub_top}'. Sudut pandang: {angle_top}.\n{PROMPT_RULES_CLEAN}\n{get_length_prompt_desc(act_len)}\nMaksimal 220 karakter tanpa kutip."
            v_txt = call_gemini(prompt_v)
            st.session_state["manual_generated_posts"] = [{
                "date": schedule_d.strftime("%Y-%m-%d"),
                "time": schedule_t.strftime("%H:%M"),
                "account": target_account,
                "main": v_txt,
                "media": "",
                "reply": "",
                "link": ""
            }]
            st.success("🎉 Postingan viral berhasil dibuat!")

    elif content_mode == "✍️ Tulis Bebas Manual":
        c_man1, c_man2 = st.columns(2)
        with c_man1:
            man_main = st.text_area("Teks Utama", height=100)
            man_media = st.text_input("Media URL")
        with c_man2:
            man_reply = st.text_area("Balasan (Pisahkan dengan ---REPLY---)", height=100)
            man_link = st.text_input("Link Shopee")

        if st.button("➕ Tambah Manual"):
            st.session_state["manual_generated_posts"].append({
                "date": schedule_d.strftime("%Y-%m-%d"),
                "time": schedule_t.strftime("%H:%M"),
                "account": target_account,
                "main": man_main.strip(),
                "media": man_media.strip(),
                "reply": man_reply.strip(),
                "link": man_link.strip()
            })
            st.success("✅ Ditambahkan!")

    # PREVIEW & SIMPAN
    posts_to_show = st.session_state.get("manual_generated_posts", [])
    if posts_to_show:
        st.divider()
        st.write("#### 📝 Preview Antrean Draf")
        for idx_p, p_item in enumerate(posts_to_show):
            with st.container():
                st.markdown(f"**📌 Post #{idx_p + 1} | `{p_item['date']} {p_item['time']}` | Target: `{p_item['account']}`**")
                col_box1, col_box2 = st.columns(2)
                with col_box1:
                    p_item["main"] = st.text_area(f"Teks #{idx_p + 1}", value=p_item["main"], height=80, key=f"preview_main_{idx_p}")
                    p_item["media"] = st.text_input(f"Media #{idx_p + 1}", value=p_item.get("media", ""), key=f"preview_media_{idx_p}")
                with col_box2:
                    p_item["reply"] = st.text_area(f"Reply #{idx_p + 1}", value=p_item["reply"], height=80, key=f"preview_reply_{idx_p}")
                    p_item["link"] = st.text_input(f"Link #{idx_p + 1}", value=p_item["link"], key=f"preview_link_{idx_p}")

        col_b1, col_b2 = st.columns([2, 1])
        with col_b1:
            if st.button("💾 Simpan Semua ke Google Sheets", type="primary"):
                sh_obj = get_spreadsheet()
                if sh_obj:
                    data_ws = sh_obj.worksheet("data")
                    accounts_to_save = acc_names_all if target_account == "-- Semua Akun (All Accounts) --" else [target_account]
                    for acc_name_single in accounts_to_save:
                        for itm in posts_to_show:
                            data_ws.append_row([
                                itm["date"],
                                itm["time"],
                                acc_name_single,
                                itm["main"],
                                itm.get("media", ""),
                                itm["reply"],
                                itm["link"],
                                "PENDING",
                                "", "", "", ""
                            ])
                    st.cache_data.clear()
                    st.success("🎉 Draf berhasil disimpan ke antrean!")
                    st.session_state["manual_generated_posts"] = []
                    st.rerun()
        with col_b2:
            if st.button("🗑️ Kosongkan Draf"):
                st.session_state["manual_generated_posts"] = []
                st.rerun()

# ==============================================================================
# TAB 5: ANTREAN & RIWAYAT
# ==============================================================================
with tabs[4]:
    st.subheader("📋 Daftar Antrean & Status Postingan")
    if all_data:
        st.dataframe(all_data, use_container_width=True)
    else:
        st.info("Belum ada antrean di tab data.")

# ==============================================================================
# TAB 6: AKUN THREADS & DIAGNOSTIK
# ==============================================================================
with tabs[5]:
    st.subheader("⚙️ Manajemen Akun Threads & Diagnostik")
    if acc_records:
        df_acc = pd.DataFrame(acc_records)
        if "access_token" in df_acc.columns:
            df_acc["token_preview"] = df_acc["access_token"].apply(lambda t: str(t)[:10] + "..." + str(t)[-6:] if len(str(t)) > 16 else "********")
            st.dataframe(df_acc[["name", "user_id", "token_preview"]], use_container_width=True)
        else:
            st.dataframe(df_acc, use_container_width=True)

    st.divider()
    c_diag1, c_diag2 = st.columns(2)
    with c_diag1:
        st.write("##### 🧵 Tes Koneksi Threads")
        if acc_records:
            acc_to_test = st.selectbox("Pilih Akun:", [a["name"] for a in acc_records], key="sel_test_acc")
            if st.button("🔍 Tes Token"):
                chosen_acc = next((a for a in acc_records if a["name"] == acc_to_test), None)
                if chosen_acc:
                    res = check_threads_token(str(chosen_acc["user_id"]), str(chosen_acc["access_token"]))
                    if "id" in res:
                        st.success(f"✅ Token Aktif! (@{res.get('username')})")
                    else:
                        st.error(f"❌ Gagal: {res}")
    with c_diag2:
        st.write("##### 🤖 Tes Koneksi Gemini AI")
        if st.button("⚡ Tes Gemini"):
            try:
                reply, model = call_gemini_core("Tes koneksi!")
                st.success(f"✅ Terhubung ({model}): {reply}")
            except Exception as e:
                st.error(f"❌ Gagal: {e}")

    st.divider()
    st.write("#### ➕ Tambah Akun Threads Baru")
    with st.form("form_add_acc"):
        col_acc1, col_acc2 = st.columns(2)
        with col_acc1:
            new_acc_name = st.text_input("Nama Label Akun *", placeholder="Misal: akun_kedua")
            new_acc_uid = st.text_input("Threads User ID *", placeholder="17841...")
        with col_acc2:
            new_acc_token = st.text_area("Long-Lived Access Token *", height=90)
        btn_add = st.form_submit_button("💾 Simpan Akun")
        if btn_add:
            if new_acc_name and new_acc_uid and new_acc_token:
                sh_obj = get_spreadsheet()
                if sh_obj:
                    acc_ws = sh_obj.worksheet("Accounts")
                    acc_ws.append_row([new_acc_name.strip(), new_acc_uid.strip(), new_acc_token.strip()])
                    st.cache_data.clear()
                    st.success("✅ Akun tersimpan!")
                    st.rerun()
