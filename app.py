import os
import json
import base64
from datetime import datetime, timedelta
import pytz
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- KONFIGURASI HALAMAN ---
st.set_page_config(
    page_title="Threads Affiliate Studio",
    page_icon="🚀",
    layout="wide"
)

TZ = pytz.timezone("Asia/Jakarta")

# --- KREDENSIAL ---
def get_env_var(key: str, default: str = "") -> str:
    if key in st.secrets:
        return st.secrets[key]
    return os.environ.get(key, default)

SPREADSHEET_ID = get_env_var("SPREADSHEET_ID")
GCP_CREDS_BASE64 = get_env_var("GCP_CREDS_BASE64")
AI_API_KEY = get_env_var("AI_API_KEY")

@st.cache_resource
def get_sheets_client():
    if not GCP_CREDS_BASE64:
        st.error("Kredensial GCP_CREDS_BASE64 belum disetel di Secrets.")
        st.stop()
    creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

@st.cache_resource
def get_ai_model():
    if not AI_API_KEY:
        st.error("AI_API_KEY belum disetel di Secrets.")
        st.stop()
    genai.configure(api_key=AI_API_KEY)
    
    # Deteksi model otomatis yang tersedia
    try:
        available = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
        for pref in ["models/gemini-1.5-flash", "models/gemini-2.0-flash", "models/gemini-1.5-pro"]:
            if pref in available:
                return genai.GenerativeModel(pref)
        return genai.GenerativeModel(available[0])
    except Exception:
        return genai.GenerativeModel("gemini-1.5-flash")

# --- DATABASE SHEETS ---
def load_sheet_data(worksheet_name: str):
    gc = get_sheets_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(worksheet_name)
    return ws, ws.get_all_records()

# --- FUNGSI GENERATOR AI ---
def generate_viral_post(model) -> str:
    prompt = """
    Tulis 1 postingan Threads gaya Indonesia santai/relatable.
    Topik seputar: realita dunia kerja, kebiasaan begadang, overthinking receh, atau kebiasaan boros anak muda.
    Aturan:
    - Tanpa emoji berlebihan.
    - Maksimal 280 karakter.
    - Nada bicara seperti teman tongkrongan.
    - HANYA kembalikan teks postingan.
    """
    res = model.generate_content(prompt)
    return res.text.strip().replace('"', '')

def generate_affiliate_post(model, prod_name: str, highlight: str, aff_link: str, tone: str) -> tuple[str, str]:
    prompt = f"""
    Produk: {prod_name}
    Keunggulan: {highlight}
    Link Pembelian: {aff_link}
    Gaya Bahasa: {tone}

    Tugas:
    Buat 1 postingan Threads rekomendasi racun belanja organik.
    Format output HARUS persis seperti di bawah ini, dipisahkan kata KODE_SPLIT:
    [Teks Postingan Utama]
    KODE_SPLIT
    [Teks Balasan / Reply Berisi Link]

    Aturan Postingan Utama:
    - Awali dengan hook santai atau keluhan relatable.
    - Maksimal 300 karakter.
    - Jangan cantumkan link di postingan utama.

    Aturan Teks Balasan:
    - Ulasan ringkas kenapa barang ini berguna.
    - Cantumkan link pembelian persis: {aff_link}
    - Maksimal 250 karakter.
    """
    res = model.generate_content(prompt)
    parts = res.text.split("KODE_SPLIT")
    if len(parts) >= 2:
        return parts[0].strip().replace('"', ''), parts[1].strip().replace('"', '')
    return res.text.strip(), f"Yang butuh link tokonya ada di sini ya: {aff_link}"

# --- UI DASBOR ---
st.title("🧵 Threads Content & Affiliate Studio")
tab_studio, tab_katalog, tab_antrean = st.tabs(["✨ Content Studio", "📦 Katalog Produk", "📋 Antrean Tab Data"])

# ==================== TAB 1: CONTENT STUDIO ====================
with tab_studio:
    st.subheader("Buat & Jadwalkan Postingan")
    
    col_a, col_b = st.columns([1, 1])
    
    with col_a:
        acc_ws, acc_records = load_sheet_data("Accounts")
        acc_list = [r["name"] for r in acc_records if r.get("name")]
        target_account = st.selectbox("Pilih Akun Threads Target:", acc_list if acc_list else ["Default"])
        
        mode = st.radio("Tipe Konten:", ["Produk Affiliate (Katalog)", "Viral Booster (Tanpa Link)"], horizontal=True)
        
        prod_ws, prod_records = load_sheet_data("Products")
        ready_prods = [p for p in prod_records if str(p.get("status", "")).strip().upper() == "READY"]

        selected_prod = None
        if mode == "Produk Affiliate (Katalog)":
            if not ready_prods:
                st.warning("Belum ada produk berstatus READY di tab Products.")
            else:
                prod_names = [f"{p['product_name']} ({p.get('category', 'Umum')})" for p in ready_prods]
                choice = st.selectbox("Pilih Produk:", prod_names)
                selected_prod = ready_prods[prod_names.index(choice)]
        
        tone = st.selectbox("Tone / Gaya Bahasa:", [
            "Sambat Halus & Relatable",
            "Spill Santai ala Tongkrongan",
            "Storytelling Masalah ke Solusi",
            "Antusias / Racun Belanja"
        ])

        col_d, col_t = st.columns(2)
        sched_date = col_d.date_input("Tanggal Tayang:", datetime.now(TZ).date())
        sched_time = col_t.time_input("Jam Tayang:", (datetime.now(TZ) + timedelta(minutes=10)).time())

    with col_b:
        st.write("**Pratinjau & Generate:**")
        ai_model = get_ai_model()

        if st.button("✨ Generate Teks Konten via AI", use_container_width=True):
            if mode == "Viral Booster (Tanpa Link)":
                st.session_state["gen_main"] = generate_viral_post(ai_model)
                st.session_state["gen_reply"] = ""
                st.session_state["gen_media"] = ""
            else:
                if selected_prod:
                    m_txt, r_txt = generate_affiliate_post(
                        ai_model,
                        selected_prod["product_name"],
                        selected_prod.get("highlight", ""),
                        selected_prod.get("affiliate_link", ""),
                        tone
                    )
                    st.session_state["gen_main"] = m_txt
                    st.session_state["gen_reply"] = r_txt
                    st.session_state["gen_media"] = str(selected_prod.get("media_url", "")).strip()

        gen_main = st.text_area("Postingan Utama:", value=st.session_state.get("gen_main", ""), height=120)
        gen_media = st.text_input("Media URL (Foto/Video Cloudinary):", value=st.session_state.get("gen_media", ""))
        gen_reply = st.text_area("Balasan / Utas (Link Affiliate):", value=st.session_state.get("gen_reply", ""), height=100)

        if gen_media:
            st.caption("Pratinjau Link Media terpasang:")
            for m in [x.strip() for x in gen_media.split(",") if x.strip()]:
                st.code(m, language="text")

        if st.button("💾 Simpan ke Antrean Tab Data", type="primary", use_container_width=True):
            if not gen_main:
                st.error("Teks postingan utama tidak boleh kosong.")
            else:
                data_ws, _ = load_sheet_data("data")
                time_fmt = sched_time.strftime("%H:%M")
                date_fmt = sched_date.strftime("%Y-%m-%d")

                row_data = [
                    date_fmt,
                    time_fmt,
                    target_account,
                    gen_main,
                    gen_media,
                    gen_reply,
                    f"Mode: {mode}",
                    "PENDING",
                    "",
                    "",
                    ""
                ]
                data_ws.append_row(row_data)
                st.success(f"Berhasil dijadwalkan ke tab 'data' untuk jam {time_fmt} WIB!")

# ==================== TAB 2: KATALOG PRODUK ====================
with tab_katalog:
    st.subheader("Katalog Produk Tab `Products`")
    prod_ws, prod_records = load_sheet_data("Products")
    
    if prod_records:
        st.dataframe(prod_records, use_container_width=True)
    else:
        st.info("Tab Products masih kosong.")

    st.write("---")
    st.write("**Tambah Produk Baru ke Katalog:**")
    with st.form("tambah_produk_form", clear_on_submit=True):
        f_name = st.text_input("Nama Produk:")
        f_highlight = st.text_area("Highlight / Masalah yang Diselesaikan:")
        f_link = st.text_input("Link Shopee Affiliate:")
        f_category = st.selectbox("Kategori:", ["Meja Kerja", "Problem Solver", "Kamar Estetik", "Health & Lifestyle", "Lainnya"])
        f_media = st.text_input("Link Media Cloudinary (Kosongkan jika teks saja, pisahkan koma jika carousel):")
        f_status = st.selectbox("Status:", ["READY", "DRAFT"])

        submit_prod = st.form_submit_button("Tambahkan ke Tab Products")
        if submit_prod:
            if not f_name or not f_link:
                st.error("Nama Produk dan Link Affiliate wajib diisi.")
            else:
                prod_ws.append_row([f_name, f_highlight, f_link, f_category, f_status, f_media])
                st.success(f"Produk '{f_name}' berhasil ditambahkan ke katalog!")
                st.rerun()

# ==================== TAB 3: ANTREAN DATA ====================
with tab_antrean:
    st.subheader("Daftar Antrean Tab `data`")
    data_ws, data_records = load_sheet_data("data")
    if data_records:
        st.dataframe(data_records, use_container_width=True)
    else:
        st.info("Belum ada antrean postingan di tab data.")
