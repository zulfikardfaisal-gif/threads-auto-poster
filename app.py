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

# --- POOL HOOK VIRAL THREADS (Dirotasi otomatis agar tidak monoton) ---
VIRAL_HOOK_PATTERNS = [
    "Pola 'Skeptis ke Plot Twist': Awali dengan mengira barang ini awalnya cuma gimik marketing atau gak penting, tapi pas dipakai ternyata ngebantu banget.",
    "Pola 'Underrated Discovery': Awali dengan rasa heran atau penasaran kenapa barang ini baru disadari fungsinya sekarang padahal praktis banget.",
    "Pola 'Daily Frustration': Awali dengan masalah sepele harian yang sering bikin repot sebelum nemu solusi simpel ini.",
    "Pola 'Investasi Kecil Faedah Gede': Awali dengan nada rekomendasi bahwa dengan harga terjangkau manfaatnya berasa banget buat jangka panjang.",
    "Pola 'Statement Tegas Singkat': Awali dengan 1 kalimat pendek to-the-point yang bikin orang penasaran membaca lanjutannya.",
    "Pola 'Curhat Solutif': Awali dengan pengalaman setelah sering salah beli atau gonta-ganti barang, akhirnya nemu yang beneran awet.",
    "Pola 'Spill Santai': Awali seperti lagi spill rahasia printilan berguna ke teman tongkrongan."
]

VIRAL_TOPICS = [
    "Dilema dunia kerja, lembur, dan overthinking karir usia 20-an",
    "Perdebatan belanja impulsif vs hemat yang selalu berakhir boncos",
    "Curhat realita tinggal di kota besar dan susahnya menabung",
    "Humor linimasa soal tanggal tua dan godaan checkout marketplace",
    "Pilihan hidup karir stabil vs bangun bisnis sendiri yang serba spekulatif",
    "Gaya hidup FOMO vs ketenangan hidup sederhana yang hemat"
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

# Helper parsing waktu
def parse_dt(s: str) -> datetime:
    cleaned = str(s).strip().replace("'", "")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})$", cleaned)
    if not m:
        raise ValueError(f"Format tidak valid: {s}")
    y, mo, d, h, mi = map(int, m.groups())
    return TZ.localize(datetime(y, mo, d, h, mi))

# Helper distribusi slot jam tayang
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

# Helper urutan postingan
def arrange_post_types(num_viral, num_affiliate):
    total = num_viral + num_affiliate
    if total == 0:
        return []
    if num_viral == 0:
        return ["affiliate"] * num_affiliate
    if num_affiliate == 0:
        return ["viral"] * num_viral
    if num_viral == 1:
        return ["viral"] + ["affiliate"] * num_affiliate
    step = total / num_viral
    viral_indices = set()
    for i in range(num_viral):
        viral_indices.add(min(int(round(i * step)), total - 1))
    curr = 0
    while len(viral_indices) < num_viral and curr < total:
        viral_indices.add(curr)
        curr += 1
    return ["viral" if i in viral_indices else "affiliate" for i in range(total)]

# Helper acak panjang-pendek dengan bias
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
        return "Tulis lebih panjang, detail, dan mengalir seperti storytelling mendalam (sekitar 300-450 karakter)."
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

# Helper update tab Config
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

# --- CALL GEMINI DENGAN DYNAMIC DISCOVERY ---
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

# Helper Generator Rantai Balasan (Link selalu di reply terakhir)
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

# Helper Tes Akun Threads
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
    st.caption("Pusat kendali konten manual & autopilot (7 Formula Hook Viral, AI Style Adaptif, Reply Chain Acak, Multi-Akun).")
with c_head2:
    if st.button("🔄 Segarkan Data Sheets"):
        st.cache_data.clear()
        st.rerun()

# Load Data dari Cache
try:
    cfg_data, raw_prods, acc_records, all_data = load_all_sheets_data()
except Exception as e:
    if "429" in str(e):
        st.error("⏳ Google Sheets API sedang terkena jeda kuota request. Tunggu 30-60 detik lalu klik '🔄 Segarkan Data Sheets'.")
    else:
        st.error(f"Gagal memuat data Google Sheets: {e}")
    st.stop()

# Tab Menu
tabs = st.tabs([
    "⚡ Kontrol Autopilot",
    "📦 Katalog Produk (50+ Items)",
    "✍️ Content Studio (Manual & AI)",
    "📋 Antrean & Riwayat",
    "⚙️ Akun Threads & Diagnostik"
])

# ==============================================================================
# TAB 1: KONTROL AUTOPILOT
# ==============================================================================
with tabs[0]:
    st.subheader("Pengaturan Jadwal & Format Autopilot")
    st.write("Atur tanggal aktif, kuota konten harian, **gaya penulisan AI**, **format balasan utas**, dan **pola panjang-pendek teks**.")

    raw_start = cfg_data.get("start_datetime", "2026-09-06 00:00")
    raw_end = cfg_data.get("end_datetime", "2026-09-30 22:00")
    
    try:
        cur_viral_count = int(cfg_data.get("daily_viral_count", 1))
    except:
        cur_viral_count = 1

    try:
        cur_affiliate_count = int(cfg_data.get("daily_affiliate_count", 4))
    except:
        cur_affiliate_count = 4

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
        total_daily = cur_viral_count + cur_affiliate_count
        st.info(
            f"**Konfigurasi Tersimpan Saat Ini:**\n"
            f"- Jadwal: `{raw_start} WIB` s/d `{raw_end} WIB`\n"
            f"- Kuota Harian: **{total_daily} Konten** ({cur_viral_count} Viral + {cur_affiliate_count} Affiliate)\n"
            f"- Target Akun: `{cur_target_acc}`\n"
            f"- Gaya AI: **{cur_ai_style}**\n"
            f"- Rantai Balasan: **{cur_reply_mode}** | Panjang Teks: **{cur_length_bias}**"
        )

    st.divider()

    st.write("#### 🛠️ Sesuaikan Jadwal, Target Akun & Format Harian")
    
    acc_names_all = [str(a["name"]).strip() for a in acc_records if str(a.get("name", "")).strip()]
    auto_acc_options = ["-- Semua Akun (All Accounts) --"] + acc_names_all

    col_ap_acc, col_ap_blank = st.columns([2, 2])
    with col_ap_acc:
        def_acc_idx = auto_acc_options.index(cur_target_acc) if cur_target_acc in auto_acc_options else 0
        sel_auto_acc = st.selectbox("🎯 Target Akun Autopilot", auto_acc_options, index=def_acc_idx)

    # Form Tanggal
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

    st.write("##### 🎯 Porsi Konten & Gaya Penulisan AI")
    col_q1, col_q2 = st.columns(2)
    with col_q1:
        sel_viral = st.number_input(
            "🚀 Jumlah Konten Viral Booster per Hari",
            min_value=0,
            max_value=5,
            value=cur_viral_count,
            help="Postingan organik untuk memicu likes/komentar tanpa link produk"
        )
    with col_q2:
        sel_affiliate = st.number_input(
            "🛍️ Jumlah Konten Affiliate per Hari",
            min_value=1,
            max_value=10,
            value=cur_affiliate_count,
            help="Postingan rekomendasi produk affiliate"
        )

    # PILIHAN AI STYLE & BOBOT PANJANG TEKS
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
        sel_ai_style_ap = st.selectbox(
            "🎨 Gaya Bahasa AI (Autopilot)",
            ai_style_options,
            index=def_style_idx,
            help="Pilih gaya tulisan tertentu atau serahkan ke AI agar berganti gaya secara dinamis tiap postingan."
        )
    with col_st2:
        bias_options = [
            "Dominan Sedang (Lebih banyak standar)",
            "Dominan Pendek (Lebih banyak ringkas)",
            "Dominan Panjang (Lebih banyak storytelling)",
            "Acak Seimbang (Rata: Pendek, Sedang, Panjang)"
        ]
        def_bias_idx = bias_options.index(cur_length_bias) if cur_length_bias in bias_options else 0
        sel_bias_autopilot = st.selectbox(
            "🎲 Pola Panjang Teks (Random Berbobot)",
            bias_options,
            index=def_bias_idx,
            help="AI akan mengacak panjang teks postingan dengan prioritas bobot yang Anda pilih."
        )

    # PILIHAN RANTAI BALASAN (DENGAN OPSI RANDOM 1-3 REPLY)
    st.write("##### 💬 Pengaturan Rantai Balasan (Reply Chain)")
    reply_mode_options = [
        "🎲 Acak (1 - 3 Balasan)",
        "🎲 Acak (1 - 5 Balasan)",
        "🎲 Acak (2 - 4 Balasan)",
        "1 Balasan (Hanya Link)",
        "2 Balasan (1 Cerita + Link)",
        "3 Balasan (2 Cerita + Link)"
    ]
    def_rep_idx = reply_mode_options.index(cur_reply_mode) if cur_reply_mode in reply_mode_options else 0
    sel_reply_mode_ap = st.selectbox(
        "Pilih Format Balasan Utas:",
        reply_mode_options,
        index=def_rep_idx,
        help="Pilih opsi acak (misal 1-3 balasan) agar tiap thread bervariasi. Link affiliate selalu disematkan di balasan paling akhir!"
    )

    total_plan = sel_viral + sel_affiliate
    preview_slots = generate_slots(total_plan)
    preview_types = arrange_post_types(sel_viral, sel_affiliate)

    st.caption(f"💡 **Total Rencana:** {total_plan} postingan per hari. Balasan: `{sel_reply_mode_ap}`.")
    slot_badges = [f"`{preview_slots[i]} ({'Viral 🚀' if preview_types[i]=='viral' else 'Affiliate 🛍️'})`" for i in range(total_plan)]
    st.markdown("🕒 **Distribusi Jam Tayang:** " + " ➜ ".join(slot_badges))

    if st.button("💾 Simpan Pengaturan Autopilot ke Google Sheets", type="primary"):
        sh_obj = get_spreadsheet()
        if sh_obj:
            try:
                new_start_str = f"'{start_d.strftime('%Y-%m-%d')} {start_t.strftime('%H:%M')}"
                new_end_str = f"'{end_d.strftime('%Y-%m-%d')} {end_t.strftime('%H:%M')}"
                
                update_config_keys(sh_obj, {
                    "start_datetime": new_start_str,
                    "end_datetime": new_end_str,
                    "daily_viral_count": str(sel_viral),
                    "daily_affiliate_count": str(sel_affiliate),
                    "target_autopilot_account": str(sel_auto_acc),
                    "reply_mode": str(sel_reply_mode_ap),
                    "length_bias": str(sel_bias_autopilot),
                    "ai_style": str(sel_ai_style_ap)
                })
                st.cache_data.clear()
                st.success("✅ Pengaturan autopilot (AI Style & Random Reply) berhasil disimpan!")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menyimpan ke Google Sheets: {e}")

    st.divider()

    # Tombol Eksekusi Cepat
    st.write("#### ⚡ Eksekusi Cepat: Generate Konten Hari Ini")
    st.caption(f"Akan membuat {total_plan} postingan ({sel_viral} viral + {sel_affiliate} affiliate) dengan 7 pola hook viral Threads untuk {sel_auto_acc}.")

    if st.button("🚀 Generate Konten Autopilot Sekarang"):
        sh_obj = get_spreadsheet()
        if not sh_obj:
            st.error("Koneksi spreadsheet tidak tersedia.")
        else:
            with st.spinner(f"Sedang meracik {total_plan} konten via Gemini AI..."):
                try:
                    if not acc_records:
                        st.error("Tab Accounts masih kosong. Daftarkan akun terlebih dahulu di tab '⚙️ Akun Threads & Diagnostik'.")
                    else:
                        if sel_auto_acc == "-- Semua Akun (All Accounts) --":
                            target_accounts_to_run = acc_names_all
                        else:
                            target_accounts_to_run = [sel_auto_acc]

                        ready_prods = [p for p in raw_prods if str(p.get("status", "")).strip().upper() == "READY"]

                        if sel_affiliate > 0 and not ready_prods:
                            st.error("Tidak ada produk berstatus READY di tab Products.")
                        else:
                            today_str = now.strftime("%Y-%m-%d")
                            data_ws = sh_obj.worksheet("data")
                            new_rows = []

                            for acc_target in target_accounts_to_run:
                                if len(ready_prods) >= sel_affiliate:
                                    sampled_prods = random.sample(ready_prods, sel_affiliate)
                                else:
                                    sampled_prods = random.choices(ready_prods, k=sel_affiliate)

                                aff_idx = 0
                                for slot_time, p_type in zip(preview_slots, preview_types):
                                    chosen_len = pick_length_by_bias(sel_bias_autopilot)
                                    len_desc = get_length_prompt_desc(chosen_len)
                                    style_desc = resolve_style_desc(sel_ai_style_ap)

                                    if p_type == "viral":
                                        topic = random.choice(VIRAL_TOPICS)
                                        prompt_v = (
                                            f"Tulis 1 postingan Threads bahasa Indonesia gaya santai, relate, dan memancing komentar warganet tentang: '{topic}'. "
                                            f"{style_desc}. {len_desc} DILARANG pakai hashtag, tanpa tanda kutip."
                                        )
                                        v_text = call_gemini(prompt_v)
                                        new_rows.append([today_str, slot_time, acc_target, v_text, "", "", "", "PENDING", "", "", ""])
                                    else:
                                        prod = sampled_prods[aff_idx]
                                        aff_idx += 1

                                        # Rotasi 7 Pola Hook Viral
                                        chosen_hook = random.choice(VIRAL_HOOK_PATTERNS)

                                        prompt_a = (
                                            f"Tulis 1 postingan Threads bahasa Indonesia yang sangat natural, tidak kaku, dan memancing engagement untuk barang: '{prod['product_name']}' "
                                            f"(Keunggulan utama: {prod.get('highlight', '')}).\n"
                                            f"- Format Pembuka: {chosen_hook}.\n"
                                            f"- {style_desc}.\n"
                                            f"- {len_desc}.\n"
                                            f"- ATURAN PENTING: Jangan monoton. Buat kalimat pembuka mengalir alami. DILARANG pakai hashtag dan tanda kutip."
                                        )
                                        main_txt = call_gemini(prompt_a)
                                        
                                        # Resolusi jumlah balasan (bisa acak 1-3 reply, link selalu di akhir)
                                        act_rep_count = resolve_reply_count(sel_reply_mode_ap)
                                        replies_chain = generate_affiliate_replies(
                                            prod['product_name'],
                                            prod.get('highlight', ''),
                                            prod['affiliate_link'],
                                            act_rep_count
                                        )
                                        joined_replies = "\n---REPLY---\n".join(replies_chain)

                                        new_rows.append([today_str, slot_time, acc_target, main_txt, "", joined_replies, prod["affiliate_link"], "PENDING", "", "", ""])

                            for r in new_rows:
                                data_ws.append_row(r)

                            st.cache_data.clear()
                            st.success(f"🎉 Berhasil membuat {len(new_rows)} antrean postingan untuk {len(target_accounts_to_run)} akun!")
                            st.rerun()
                except Exception as ex:
                    st.error(f"Terjadi kesalahan: {ex}")

# ==============================================================================
# TAB 2: KATALOG PRODUK (BISA SAMPAI 50+ & BISA DIEDIT KAPAN SAJA)
# ==============================================================================
with tabs[1]:
    st.subheader("📦 Katalog Produk Affiliate")
    st.write("Katalog ini menampung puluhan produk. Bot AI secara acak memilih produk berstatus **READY** setiap hari.")

    expected_cols = ["product_name", "highlight", "affiliate_link", "category", "status"]

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

    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Total Produk Terdaftar", f"{total_items} Item")
    c_m2.metric("Produk Siap Dipakai (READY)", f"{ready_items} Item")
    c_m3.metric("Kapasitas", "50+ Item (Aktif)")

    st.divider()

    st.write("#### 📝 Edit Langsung di Tabel (Excel Style)")
    st.caption("Klik dua kali pada sel mana saja untuk mengedit. Anda juga bisa menambah atau menghapus baris langsung di tabel ini.")

    edited_df = st.data_editor(
        df_prods,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "product_name": st.column_config.TextColumn("Nama Produk", required=True, width="medium"),
            "highlight": st.column_config.TextColumn("Keunggulan / Highlight", width="large"),
            "affiliate_link": st.column_config.TextColumn("Link Affiliate Shopee", required=True, width="medium"),
            "category": st.column_config.TextColumn("Kategori", width="small"),
            "status": st.column_config.SelectboxColumn(
                "Status",
                options=["READY", "DRAFT", "ARCHIVED"],
                required=True,
                width="small"
            ),
        },
        height=350
    )

    if st.button("💾 Simpan Semua Perubahan Tabel ke Google Sheets", type="primary"):
        sh_obj = get_spreadsheet()
        if sh_obj:
            with st.spinner("Menyimpan seluruh katalog ke Google Sheets..."):
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
                    st.error(f"Gagal menyimpan ke Google Sheets: {e}")

    st.divider()

    with st.expander("✏️ Atau Edit Produk Tertentu via Form (Praktis di Layar Ponsel)"):
        if not df_prods.empty:
            prod_titles = df_prods["product_name"].tolist()
            selected_p = st.selectbox("Pilih produk yang ingin diedit:", prod_titles)
            
            p_idx = prod_titles.index(selected_p)
            cur_row = df_prods.iloc[p_idx]

            with st.form("form_edit_single"):
                col_e1, col_e2 = st.columns(2)
                with col_e1:
                    e_name = st.text_input("Nama Produk", value=str(cur_row["product_name"]))
                    e_link = st.text_input("Link Affiliate", value=str(cur_row["affiliate_link"]))
                with col_e2:
                    e_hl = st.text_input("Highlight / Keunggulan", value=str(cur_row["highlight"]))
                    e_cat = st.text_input("Kategori", value=str(cur_row["category"]))
                
                status_opts = ["READY", "DRAFT", "ARCHIVED"]
                cur_stat = str(cur_row["status"]).upper()
                def_stat_idx = status_opts.index(cur_stat) if cur_stat in status_opts else 0
                e_stat = st.selectbox("Status", status_opts, index=def_stat_idx)

                btn_save_single = st.form_submit_button("Simpan Perubahan Produk Ini")
                if btn_save_single:
                    sh_obj = get_spreadsheet()
                    if sh_obj:
                        sheet_row = p_idx + 2
                        prod_ws = sh_obj.worksheet("Products")
                        prod_ws.update_cell(sheet_row, 1, e_name)
                        prod_ws.update_cell(sheet_row, 2, e_hl)
                        prod_ws.update_cell(sheet_row, 3, e_link)
                        prod_ws.update_cell(sheet_row, 4, e_cat)
                        prod_ws.update_cell(sheet_row, 5, e_stat)
                        st.cache_data.clear()
                        st.success(f"✅ Produk '{e_name}' berhasil diperbarui!")
                        st.rerun()
        else:
            st.info("Katalog masih kosong.")

    with st.expander("➕ Tambah 1 Produk Baru"):
        with st.form("form_add_single"):
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                new_name = st.text_input("Nama Produk Baru", placeholder="Contoh: Lampu Tidur Akrilik LED")
                new_link = st.text_input("Link Affiliate", placeholder="https://s.shopee.co.id/...")
            with col_a2:
                new_hl = st.text_input("Highlight Singkat", placeholder="Cahaya hangat, colokan USB, hemat listrik")
                new_cat = st.text_input("Kategori", placeholder="Dekorasi / Rumah Tangga")
            new_stat = st.selectbox("Status", ["READY", "DRAFT", "ARCHIVED"])

            if st.form_submit_button("Tambahkan ke Katalog"):
                if not new_name or not new_link:
                    st.error("Nama produk dan link affiliate wajib diisi!")
                else:
                    sh_obj = get_spreadsheet()
                    if sh_obj:
                        prod_ws = sh_obj.worksheet("Products")
                        prod_ws.append_row([new_name, new_hl, new_link, new_cat, new_stat])
                        st.cache_data.clear()
                        st.success(f"✅ Produk '{new_name}' berhasil ditambahkan ke katalog!")
                        st.rerun()

# ==============================================================================
# TAB 3: CONTENT STUDIO (MANUAL & AI DENGAN VIRAL HOOK PATTERNS)
# ==============================================================================
with tabs[2]:
    st.subheader("✍️ Content Studio (Pembuat Konten Manual & AI)")
    st.caption("Atur target akun, waktu mulai, jumlah postingan, selang waktu (2 Jam s/d 1 Hari), panjang teks AI, dan rantai balasan dengan link di akhir.")

    acc_names_all = [str(a["name"]).strip() for a in acc_records if str(a.get("name", "")).strip()]
    account_choices = ["-- Semua Akun (All Accounts) --"] + acc_names_all if acc_names_all else ["Belum ada akun"]
    all_ready_p = [p for p in raw_prods if str(p.get("status", "")).strip().upper() == "READY"]

    if "manual_generated_posts" not in st.session_state:
        st.session_state["manual_generated_posts"] = []

    # 1. PENGATURAN UTAMA
    st.write("#### ⚙️ 1. Pengaturan Jadwal, Frekuensi & Format Teks")
    c_set1, c_set2, c_set3, c_set4 = st.columns(4)
    with c_set1:
        target_account = st.selectbox("🎯 Target Akun Threads", account_choices)
    with c_set2:
        schedule_d = st.date_input("📅 Tanggal Mulai", value=now.date(), key="cs_date")
    with c_set3:
        schedule_t = st.time_input("⏰ Jam Mulai", value=now.time(), key="cs_time")
    with c_set4:
        interval_mins = st.selectbox(
            "⏳ Selang Waktu (Interval)",
            options=[120, 180, 240, 360, 480, 720, 1440],
            index=0,
            format_func=lambda x: f"{x // 60} Jam Sekali" if x < 1440 else "1 Hari Sekali (24 Jam)"
        )

    # PILIHAN PANJANG TEKS & PILIHAN BALASAN
    c_fmt1, c_fmt2 = st.columns([2, 2])
    with c_fmt1:
        manual_length_opt = st.selectbox(
            "📏 Panjang Teks Postingan Utama",
            [
                "Sedang (Standar Threads, 180-250 karakter)",
                "Pendek (Ringkas & Padat, 1-2 kalimat, max 120 karakter)",
                "Panjang (Storytelling Mendalam, 300-450 karakter)",
                "🎲 Acak / Random Sesuai Mood AI"
            ],
            index=0
        )
    with c_fmt2:
        manual_reply_mode = st.selectbox(
            "💬 Format Rantai Balasan (Reply Chain)",
            [
                "1 Balasan (Hanya Link)",
                "2 Balasan (1 Cerita + Link)",
                "3 Balasan (2 Cerita + Link)",
                "🎲 Acak (1 - 3 Balasan)",
                "🎲 Acak (1 - 5 Balasan)"
            ],
            index=0,
            help="Link affiliate selalu ditaruh di balasan terakhir disertai narasi pengantar toko yang variatif."
        )

    st.divider()

    # 2. PILIHAN JENIS KONTEN & JUMLAH GENERATE
    st.write("#### 🎯 2. Konfigurasi Jenis Konten")
    c_mode1, c_mode2 = st.columns([3, 1])
    with c_mode1:
        content_mode = st.radio(
            "Pilih Mode Konten:",
            [
                "📑 Kurasi Produk (Listicle hingga 5 Produk)",
                "🛍️ Single Product Affiliate",
                "🚀 Viral Booster (Engagement Organik / Tanpa Link)",
                "✍️ Tulis Bebas Manual"
            ],
            horizontal=True
        )
    with c_mode2:
        num_posts = st.number_input(
            "🔢 Jumlah Konten",
            min_value=1,
            max_value=10,
            value=1 if content_mode == "📑 Kurasi Produk (Listicle hingga 5 Produk)" else 3,
            help="Berapa postingan yang ingin dibuat sekaligus dengan selang waktu otomatis"
        )

    copy_styles = [
        "Serahkan ke AI (Smart Adaptive Copywriting)",
        "Curhat Santai & Relate (Bahasa Threads anak muda)",
        "Storytelling Pengalaman Pribadi (Masalah -> Solusi)",
        "Review Jujur & Solutif (Highlight keunggulan produk)",
        "Racun Belanja Shopee (Antusias & bikin pengen checkout)"
    ]

    # --- MODE A: KURASI PRODUK (LISTICLE HINGGA 5 PRODUK) ---
    if content_mode == "📑 Kurasi Produk (Listicle hingga 5 Produk)":
        st.write("##### 📑 Pengaturan Kurasi Produk (Hingga 5 Produk)")
        c_k1, c_k2 = st.columns([2, 1])
        with c_k1:
            listicle_title = st.text_input("Judul / Tema Kurasi", placeholder="Contoh: 5 Barang Meja Kerja Biar Gak Gampang Burnout")
        with c_k2:
            ls_count = st.slider("Jumlah Produk yang Dikurasi:", min_value=1, max_value=5, value=min(5, max(2, len(all_ready_p))))

        curated_items = []
        prod_options = ["-- Ketik Manual Sendiri --"] + [p["product_name"] for p in all_ready_p]

        for p_i in range(1, ls_count + 1):
            with st.expander(f"📦 Produk {p_i}", expanded=True):
                col_cp1, col_cp2 = st.columns(2)
                with col_cp1:
                    sel_p_cat = st.selectbox(f"Pilih dari Katalog (Produk {p_i}):", prod_options, key=f"sel_p_{p_i}")
                    if sel_p_cat != "-- Ketik Manual Sendiri --":
                        chosen_obj = next((p for p in all_ready_p if p["product_name"] == sel_p_cat), None)
                        def_p_name = chosen_obj["product_name"] if chosen_obj else ""
                        def_p_hl = chosen_obj.get("highlight", "") if chosen_obj else ""
                        def_p_link = chosen_obj.get("affiliate_link", "") if chosen_obj else ""
                    else:
                        def_p_name, def_p_hl, def_p_link = "", "", ""

                    item_name = st.text_input(f"Nama Produk {p_i}", value=def_p_name, key=f"name_p_{p_i}")
                    item_link = st.text_input(f"Link Affiliate {p_i}", value=def_p_link, key=f"link_p_{p_i}")
                with col_cp2:
                    item_hl = st.text_area(f"Keunggulan / Highlight {p_i}", value=def_p_hl, key=f"hl_p_{p_i}", height=105)
                
                curated_items.append({"name": item_name, "highlight": item_hl, "link": item_link})

        ls_style = st.selectbox("Gaya Penulisan Copywriting:", copy_styles, key="ls_style")

        if st.button("✨ Generate Kurasi Produk via AI", type="primary"):
            valid_items = [it for it in curated_items if it["name"].strip()]
            if not listicle_title:
                st.error("Judul/Tema kurasi wajib diisi!")
            elif not valid_items:
                st.error("Minimal 1 produk harus diisi nama dan link-nya!")
            else:
                with st.spinner("AI sedang meracik kurasi produk..."):
                    try:
                        base_dt = datetime.combine(schedule_d, schedule_t)
                        gen_list = []
                        for post_idx in range(num_posts):
                            p_dt = base_dt + timedelta(minutes=post_idx * interval_mins)
                            
                            act_len = random.choice(["Pendek", "Sedang", "Panjang"]) if "Acak" in manual_length_opt else manual_length_opt
                            len_desc = get_length_prompt_desc(act_len)
                            style_desc = resolve_style_desc(ls_style)

                            prompt_hook = (
                                f"Tulis 1 postingan pembuka (hook) Threads bahasa Indonesia yang bikin penasaran tentang: '{listicle_title}'. "
                                f"{style_desc}. {len_desc} DILARANG pakai hashtag, tanpa tanda kutip."
                            )
                            hook_text = call_gemini(prompt_hook)

                            reply_lines = []
                            for idx_num, it in enumerate(valid_items, start=1):
                                reply_lines.append(f"{idx_num}. {it['name']} ✨\n{it['link']}")
                            reply_full = "\n\n".join(reply_lines)

                            gen_list.append({
                                "date": p_dt.strftime("%Y-%m-%d"),
                                "time": p_dt.strftime("%H:%M"),
                                "account": target_account,
                                "main": hook_text,
                                "reply": reply_full,
                                "link": valid_items[0]["link"] if valid_items else ""
                            })

                        st.session_state["manual_generated_posts"] = gen_list
                        st.success(f"🎉 Berhasil meracik {len(gen_list)} draf kurasi!")
                    except Exception as e:
                        st.error(f"Gagal generate: {e}")

    # --- MODE B: SINGLE PRODUCT AFFILIATE ---
    elif content_mode == "🛍️ Single Product Affiliate":
        st.write("##### 🛍️ Pengaturan Single Product")
        c_sp1, c_sp2 = st.columns(2)
        with c_sp1:
            prod_opt = ["-- Pilih Otomatis / Acak dari Katalog READY --", "-- Ketik Manual --"] + [p["product_name"] for p in all_ready_p]
            sel_sp = st.selectbox("Pilihan Produk:", prod_opt)

            if sel_sp not in ["-- Pilih Otomatis / Acak dari Katalog READY --", "-- Ketik Manual --"]:
                obj_sp = next((p for p in all_ready_p if p["product_name"] == sel_sp), None)
                def_sp_name = obj_sp["product_name"] if obj_sp else ""
                def_sp_hl = obj_sp.get("highlight", "") if obj_sp else ""
                def_sp_link = obj_sp.get("affiliate_link", "") if obj_sp else ""
            else:
                def_sp_name, def_sp_hl, def_sp_link = "", "", ""

            sp_name_input = st.text_input("Nama Produk (jika manual)", value=def_sp_name)
            sp_link_input = st.text_input("Link Affiliate", value=def_sp_link)
        with c_sp2:
            sp_hl_input = st.text_area("Highlight / Keunggulan", value=def_sp_hl, height=105)
            sp_style = st.selectbox("Gaya Bahasa AI:", copy_styles)

        if st.button("✨ Generate Single Product Posts via AI", type="primary"):
            with st.spinner("AI sedang meracik konten affiliate..."):
                try:
                    base_dt = datetime.combine(schedule_d, schedule_t)
                    gen_list = []
                    for post_idx in range(num_posts):
                        p_dt = base_dt + timedelta(minutes=post_idx * interval_mins)

                        if sel_sp == "-- Pilih Otomatis / Acak dari Katalog READY --" and all_ready_p:
                            p_curr = random.choice(all_ready_p)
                            p_name = p_curr["product_name"]
                            p_hl = p_curr.get("highlight", "")
                            p_link = p_curr.get("affiliate_link", "")
                        else:
                            p_name = sp_name_input
                            p_hl = sp_hl_input
                            p_link = sp_link_input

                        act_len = random.choice(["Pendek", "Sedang", "Panjang"]) if "Acak" in manual_length_opt else manual_length_opt
                        len_desc = get_length_prompt_desc(act_len)
                        style_desc = resolve_style_desc(sp_style)

                        # Undi Pola Hook Viral
                        chosen_hook = random.choice(VIRAL_HOOK_PATTERNS)

                        prompt = (
                            f"Tulis 1 postingan Threads bahasa Indonesia yang sangat natural, tidak kaku, dan memancing engagement untuk produk: '{p_name}' "
                            f"(Keunggulan: '{p_hl}').\n"
                            f"- Format Pembuka: {chosen_hook}.\n"
                            f"- {style_desc}.\n"
                            f"- {len_desc}.\n"
                            f"- ATURAN PENTING: Jangan monoton. Buat kalimat pembuka mengalir alami. DILARANG pakai hashtag dan tanda kutip."
                        )
                        main_t = call_gemini(prompt)

                        actual_rep = resolve_reply_count(manual_reply_mode)
                        replies_chain = generate_affiliate_replies(p_name, p_hl, p_link, actual_rep)
                        joined_replies = "\n---REPLY---\n".join(replies_chain)

                        gen_list.append({
                            "date": p_dt.strftime("%Y-%m-%d"),
                            "time": p_dt.strftime("%H:%M"),
                            "account": target_account,
                            "main": main_t,
                            "reply": joined_replies,
                            "link": p_link
                        })

                    st.session_state["manual_generated_posts"] = gen_list
                    st.success(f"🎉 Berhasil membuat {len(gen_list)} draf dengan formula hook viral!")
                except Exception as e:
                    st.error(f"Gagal generate: {e}")

    # --- MODE C: VIRAL BOOSTER ---
    elif content_mode == "🚀 Viral Booster (Engagement Organik / Tanpa Link)":
        st.write("##### 🚀 Pengaturan Postingan Viral Organik")
        c_vb1, c_vb2 = st.columns(2)
        with c_vb1:
            vb_topic = st.text_input("Tema / Topik Diskusi", placeholder="Misal: Dilema kerja lembur vs resign bangun usaha")
        with c_vb2:
            vb_style = st.selectbox("Sudut Pandang / Angle:", [
                "Opini Santai Pemancing Debat",
                "Humor Realita & Sambat Lucu",
                "Pertanyaan Diskusi (Tanya Warganet)",
                "Storytelling Pengalaman Pribadi"
            ])

        if st.button("✨ Generate Postingan Viral via AI", type="primary"):
            with st.spinner("AI sedang meracik hook diskusi viral..."):
                try:
                    base_dt = datetime.combine(schedule_d, schedule_t)
                    gen_list = []
                    for post_idx in range(num_posts):
                        p_dt = base_dt + timedelta(minutes=post_idx * interval_mins)
                        curr_topic = vb_topic if vb_topic else random.choice(VIRAL_TOPICS)
                        
                        act_len = random.choice(["Pendek", "Sedang", "Panjang"]) if "Acak" in manual_length_opt else manual_length_opt
                        len_desc = get_length_prompt_desc(act_len)

                        prompt = (
                            f"Tulis 1 postingan Threads bahasa Indonesia yang sangat relatable dan memicu interaksi/komentar warganet tentang: '{curr_topic}'. "
                            f"Angle: {vb_style}. {len_desc} DILARANG pakai hashtag, tanpa tanda kutip."
                        )
                        v_text = call_gemini(prompt)
                        gen_list.append({
                            "date": p_dt.strftime("%Y-%m-%d"),
                            "time": p_dt.strftime("%H:%M"),
                            "account": target_account,
                            "main": v_text,
                            "reply": "",
                            "link": ""
                        })

                    st.session_state["manual_generated_posts"] = gen_list
                    st.success(f"🎉 Berhasil membuat {len(gen_list)} draf viral organik!")
                except Exception as e:
                    st.error(f"Gagal generate: {e}")

    # --- MODE D: TULIS BEBAS MANUAL ---
    elif content_mode == "✍️ Tulis Bebas Manual":
        st.write("##### ✍️ Tambahkan Draf Manual dengan Selang Waktu")
        c_man1, c_man2 = st.columns(2)
        with c_man1:
            man_main = st.text_area("Teks Postingan Utama", placeholder="Ketik teks utama di sini...", height=120)
        with c_man2:
            man_reply = st.text_area(
                "Teks Balasan (Gunakan '---REPLY---' untuk memisahkan balasan bertingkat)",
                placeholder="Balasan 1...\n---REPLY---\nBalasan 2 (Link Shopee)...",
                height=120
            )
            man_link = st.text_input("Link Affiliate Cadangan", placeholder="https://...")

        if st.button("➕ Tambahkan ke Daftar Antrean di Bawah"):
            if not man_main.strip():
                st.error("Teks postingan utama wajib diisi!")
            else:
                base_dt = datetime.combine(schedule_d, schedule_t)
                current_len = len(st.session_state["manual_generated_posts"])
                p_dt = base_dt + timedelta(minutes=current_len * interval_mins)
                st.session_state["manual_generated_posts"].append({
                    "date": p_dt.strftime("%Y-%m-%d"),
                    "time": p_dt.strftime("%H:%M"),
                    "account": target_account,
                    "main": man_main.strip(),
                    "reply": man_reply.strip(),
                    "link": man_link.strip()
                })
                st.success("✅ Konten manual ditambahkan ke daftar preview di bawah!")

    st.divider()

    # --- 3. PREVIEW & SIMPAN KE GOOGLE SHEETS TAB 'DATA' ---
    st.write("#### 📝 3. Preview Draf Antrean & Finalisasi")
    st.caption("Periksa dan sunting teks sebelum menyimpan ke Google Sheets. Balasan bertingkat dipisahkan oleh tanda '---REPLY---'.")

    posts_to_show = st.session_state.get("manual_generated_posts", [])

    if not posts_to_show:
        st.info("Belum ada draf yang digenerate. Klik tombol generate di atas untuk mulai membuat postingan.")
    else:
        st.write(f"Total draf siap dijadwalkan: **{len(posts_to_show)} postingan** (Target: `{target_account}`)")
        
        for idx_p, p_item in enumerate(posts_to_show):
            with st.container():
                st.markdown(f"**📌 Post #{idx_p + 1} — Jadwal: `{p_item['date']} {p_item['time']} WIB` | Target: `{p_item['account']}`**")
                col_box1, col_box2 = st.columns(2)
                with col_box1:
                    p_item["main"] = st.text_area(f"Teks Utama #{idx_p + 1}", value=p_item["main"], height=90, key=f"preview_main_{idx_p}")
                with col_box2:
                    p_item["reply"] = st.text_area(f"Balasan / Rantai Balasan #{idx_p + 1}", value=p_item["reply"], height=90, key=f"preview_reply_{idx_p}")
                    p_item["link"] = st.text_input(f"Link #{idx_p + 1}", value=p_item["link"], key=f"preview_link_{idx_p}")

        col_b1, col_b2 = st.columns([2, 1])
        with col_b1:
            if st.button("💾 Simpan & Jadwalkan Semua ke Google Sheets", type="primary"):
                sh_obj = get_spreadsheet()
                if sh_obj:
                    with st.spinner("Menyimpan ke antrean Google Sheets..."):
                        try:
                            if target_account == "-- Semua Akun (All Accounts) --":
                                accounts_to_save = acc_names_all
                            else:
                                accounts_to_save = [target_account]

                            data_ws = sh_obj.worksheet("data")
                            total_saved = 0
                            for acc_name_single in accounts_to_save:
                                for itm in posts_to_show:
                                    data_ws.append_row([
                                        itm["date"],
                                        itm["time"],
                                        acc_name_single,
                                        itm["main"],
                                        "",
                                        itm["reply"],
                                        itm["link"],
                                        "PENDING",
                                        "", "", ""
                                    ])
                                    total_saved += 1
                            
                            st.cache_data.clear()
                            st.success(f"🎉 Berhasil menyimpan {total_saved} antrean postingan untuk {len(accounts_to_save)} akun!")
                            st.session_state["manual_generated_posts"] = []
                            st.rerun()
                        except Exception as ex:
                            st.error(f"Gagal menyimpan: {ex}")
        with col_b2:
            if st.button("🗑️ Kosongkan Draf di Atas"):
                st.session_state["manual_generated_posts"] = []
                st.rerun()

# ==============================================================================
# TAB 4: ANTREAN & RIWAYAT POSTINGAN
# ==============================================================================
with tabs[3]:
    st.subheader("📋 Daftar Antrean & Status Postingan")
    if all_data:
        st.dataframe(all_data, use_container_width=True)
    else:
        st.info("Belum ada antrean di tab data.")

# ==============================================================================
# TAB 5: AKUN THREADS & DIAGNOSTIK SISTEM
# ==============================================================================
with tabs[4]:
    st.subheader("⚙️ Manajemen Akun Threads & Diagnostik Sistem")
    st.write("Kelola akun Threads, daftarkan akun baru, serta lakukan pengujian koneksi token Threads dan Google Gemini AI.")

    # 1. Tabel Akun Terdaftar
    st.write("#### 👥 Daftar Akun Terhubung")
    if acc_records:
        df_acc = pd.DataFrame(acc_records)
        if "access_token" in df_acc.columns:
            df_acc["token_preview"] = df_acc["access_token"].apply(lambda t: str(t)[:10] + "..." + str(t)[-6:] if len(str(t)) > 16 else "********")
            st.dataframe(df_acc[["name", "user_id", "token_preview"]], use_container_width=True)
        else:
            st.dataframe(df_acc, use_container_width=True)
    else:
        st.warning("Belum ada akun yang terdaftar di tab Accounts.")

    st.divider()

    # 2. FITUR DIAGNOSTIK & TES KONEKSI
    st.write("#### 🩺 Diagnostik & Tes Koneksi")
    c_diag1, c_diag2 = st.columns(2)

    with c_diag1:
        st.write("##### 🧵 Tes Koneksi Akun Threads")
        st.caption("Verifikasi apakah Access Token Threads masih aktif dan valid ke Meta API.")
        
        if acc_records:
            acc_to_test = st.selectbox("Pilih Akun untuk Dites:", [a["name"] for a in acc_records], key="sel_test_acc")
            if st.button("🔍 Tes Token Akun Ini"):
                chosen_acc = next((a for a in acc_records if a["name"] == acc_to_test), None)
                if chosen_acc:
                    with st.spinner(f"Menghubungi Threads Graph API untuk '{acc_to_test}'..."):
                        res = check_threads_token(str(chosen_acc["user_id"]), str(chosen_acc["access_token"]))
                        if "id" in res:
                            st.success(
                                f"✅ **Koneksi Threads Berhasil!**\n\n"
                                f"- **Username:** @{res.get('username', 'N/A')}\n"
                                f"- **User ID:** `{res.get('id')}`\n"
                                f"- **Status:** Token Aktif & Siap Posting"
                            )
                        else:
                            st.error(f"❌ **Koneksi Gagal / Token Expired:**\n`{res}`")
        else:
            st.info("Tambahkan akun terlebih dahulu untuk melakukan tes koneksi.")

    with c_diag2:
        st.write("##### 🤖 Tes Koneksi Google Gemini AI")
        st.caption("Uji coba respon model Gemini API dengan AI_API_KEY yang terpasang.")
        if st.button("⚡ Tes Respon Gemini AI"):
            with st.spinner("Menguji koneksi ke Google Gemini API..."):
                t_start = time_lib.time()
                try:
                    ai_reply, model_used = call_gemini_core("Halo! Balas persis kalimat ini: 'Koneksi Gemini AI Aktif & Berhasil!'")
                    elapsed = round(time_lib.time() - t_start, 2)
                    st.success(
                        f"✅ **Gemini AI Terhubung Sempurna!**\n\n"
                        f"- **Model Aktif:** `{model_used}`\n"
                        f"- **Respon:** *\"{ai_reply}\"*\n"
                        f"- **Latensi:** `{elapsed} detik`\n"
                        f"- **Status:** Siap Menulis Copywriting Otomatis"
                    )
                except Exception as ex:
                    key_preview = (str(AI_API_KEY)[:6] + "..." + str(AI_API_KEY)[-4:]) if AI_API_KEY else "Belum disetel"
                    st.error(f"❌ **Koneksi Gemini AI Gagal:**\n\n`{ex}`")
                    st.info(
                        f"💡 **Info Diagnostik:**\n"
                        f"- Key Terbaca: `{key_preview}`\n"
                        f"- Pastikan API Key diambil dari **aistudio.google.com** (Google AI Studio)."
                    )

    st.divider()

    # 3. FITUR TAMBAH AKUN THREADS BARU
    st.write("#### ➕ Tambah Akun Threads Baru")
    st.caption("Kredensial akun akan langsung disimpan ke tab 'Accounts' di Google Sheets Anda.")

    with st.form("form_add_threads_acc"):
        col_acc1, col_acc2 = st.columns(2)
        with col_acc1:
            new_acc_name = st.text_input("Nama Label Akun *", placeholder="Misal: akun_kedua atau faisal_lifestyle")
            new_acc_uid = st.text_input("Threads User ID *", placeholder="Misal: 17841400000000000")
        with col_acc2:
            new_acc_token = st.text_area("Long-Lived Access Token *", placeholder="THQW... (Token Threads dari Meta Developers)", height=105)

        test_before_add = st.checkbox("🔍 Validasi & tes token ini ke Meta Threads sebelum menyimpan", value=True)
        btn_add_acc = st.form_submit_button("💾 Simpan Akun ke Google Sheets", type="primary")

        if btn_add_acc:
            if not new_acc_name.strip() or not new_acc_uid.strip() or not new_acc_token.strip():
                st.error("Nama label akun, User ID, dan Access Token wajib diisi!")
            else:
                passed_test = True
                if test_before_add:
                    with st.spinner("Memvalidasi token ke Meta Threads API..."):
                        test_res = check_threads_token(new_acc_uid.strip(), new_acc_token.strip())
                        if "id" not in test_res:
                            passed_test = False
                            st.error(f"❌ Validasi Token Gagal! Meta API menolak token ini: {test_res}")

                if passed_test:
                    sh_obj = get_spreadsheet()
                    if sh_obj:
                        try:
                            acc_ws = sh_obj.worksheet("Accounts")
                            acc_ws.append_row([new_acc_name.strip(), new_acc_uid.strip(), new_acc_token.strip()])
                            st.cache_data.clear()
                            st.success(f"🎉 Akun Threads '{new_acc_name}' berhasil didaftarkan dan disimpan ke Google Sheets!")
                            st.rerun()
                        except Exception as e_acc:
                            st.error(f"Gagal menyimpan akun ke Google Sheets: {e_acc}")
