import os
import re
import json
import base64
import random
import logging
from datetime import datetime, time, date
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

# --- KONEKSI GOOGLE SHEETS ---
@st.cache_resource
def init_sheets():
    if not GCP_CREDS_BASE64 or not SPREADSHEET_ID:
        return None
    try:
        creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
        creds = Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        )
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        st.error(f"Gagal menghubungkan ke Google Sheets: {e}")
        return None

sh = init_sheets()

# --- HELPER PARSING WAKTU ---
def parse_dt(s: str) -> datetime:
    cleaned = str(s).strip().replace("'", "")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})$", cleaned)
    if not m:
        raise ValueError(f"Format tidak valid: {s}")
    y, mo, d, h, mi = map(int, m.groups())
    return TZ.localize(datetime(y, mo, d, h, mi))

# --- HELPER GEMINI AI ---
def call_gemini(prompt: str) -> str:
    if not AI_API_KEY:
        raise Exception("API Key Gemini (AI_API_KEY) belum disetel!")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={AI_API_KEY}"
    res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30).json()
    return res["candidates"][0]["content"]["parts"][0]["text"].strip()

# --- TAMPILAN UTAMA ---
st.title("🧵 Threads Affiliate & Autopilot Dashboard")
st.caption("Pusat kendali pembuatan konten manual, katalog produk (50+ items), dan pengaturan autopilot.")

if not sh:
    st.error("Kredensial SPREADSHEET_ID atau GCP_CREDS_BASE64 belum terpasang di Secrets Streamlit.")
    st.stop()

tabs = st.tabs([
    "⚡ Kontrol Autopilot",
    "📦 Katalog Produk (50+ Items)",
    "✍️ Buat Konten Manual",
    "📋 Antrean & Riwayat",
    "⚙️ Akun Threads"
])

# ==============================================================================
# TAB 1: KONTROL AUTOPILOT (START & END DATE / TIME)
# ==============================================================================
with tabs[0]:
    st.subheader("Pengaturan Jadwal Aktif Autopilot")
    st.write("Atur tanggal dan jam mulai serta berakhirnya sistem autopilot. Di luar rentang ini, bot tidak akan memposting.")

    cfg_sheet = sh.worksheet("Config")
    cfg_data = dict(cfg_sheet.get_all_values())
    raw_start = cfg_data.get("start_datetime", "2026-09-06 08:00")
    raw_end = cfg_data.get("end_datetime", "2026-09-30 22:00")

    now = datetime.now(TZ)
    try:
        cur_start = parse_dt(raw_start)
        cur_end = parse_dt(raw_end)
        is_active = (cur_start <= now <= cur_end)
    except Exception:
        is_active = False

    col_stat1, col_stat2 = st.columns(2)
    with col_stat1:
        if is_active:
            st.success(f"🟢 **STATUS: AUTOPILOT AKTIF**\n\nWaktu sekarang: `{now.strftime('%Y-%m-%d %H:%M:%S')} WIB`")
        else:
            st.warning(f"🔴 **STATUS: AUTOPILOT NON-AKTIF / DI LUAR JADWAL**\n\nWaktu sekarang: `{now.strftime('%Y-%m-%d %H:%M:%S')} WIB`")
    with col_stat2:
        st.info(f"**Jadwal Tersimpan Saat Ini:**\n- Mulai: `{raw_start} WIB`\n- Berakhir: `{raw_end} WIB`")

    st.divider()

    st.write("#### 🛠️ Ubah Rentang Jadwal Aktif")
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
            def_start_time = time(8, 0)
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

    if st.button("💾 Simpan Pengaturan Jadwal ke Google Sheets", type="primary"):
        new_start_str = f"'{start_d.strftime('%Y-%m-%d')} {start_t.strftime('%H:%M')}"
        new_end_str = f"'{end_d.strftime('%Y-%m-%d')} {end_t.strftime('%H:%M')}"
        
        cfg_sheet.update_cell(2, 1, "start_datetime")
        cfg_sheet.update_cell(2, 2, new_start_str)
        cfg_sheet.update_cell(3, 1, "end_datetime")
        cfg_sheet.update_cell(3, 2, new_end_str)
        st.success("✅ Jadwal autopilot berhasil diperbarui!")
        st.rerun()

    st.divider()

    st.write("#### ⚡ Eksekusi Cepat: Generate 5 Konten Hari Ini")
    st.caption("Menghasilkan 1 konten viral (08:15) dan 4 konten affiliate acak langsung ke tab antrean `data`.")

    if st.button("🚀 Generate 5 Konten Autopilot Sekarang"):
        with st.spinner("Sedang menghubungi Gemini AI dan menyusun antrean..."):
            try:
                accs = sh.worksheet("Accounts").get_all_records()
                if not accs:
                    st.error("Tab Accounts masih kosong. Daftarkan akun terlebih dahulu.")
                else:
                    target_acc = accs[0]["name"]
                    prod_ws = sh.worksheet("Products")
                    all_prods = prod_ws.get_all_records()
                    ready_prods = [p for p in all_prods if str(p.get("status", "")).strip().upper() == "READY"]

                    if len(ready_prods) < 4:
                        st.error(f"Produk berstatus READY kurang dari 4 (hanya ada {len(ready_prods)}). Tambahkan di tab Katalog Produk.")
                    else:
                        slots = ["08:15", "11:45", "15:30", "18:45", "21:15"]
                        viral_prompts = [
                            "Dilema dunia kerja, lembur, dan overthinking karir usia 20-an",
                            "Perdebatan belanja impulsif vs hemat yang selalu berakhir boncos",
                            "Curhat realita tinggal di kota besar dan susahnya menabung",
                            "Humor linimasa soal tanggal tua dan godaan checkout marketplace"
                        ]
                        today_str = now.strftime("%Y-%m-%d")
                        data_ws = sh.worksheet("data")
                        new_rows = []

                        # 1. Konten Viral
                        topic = random.choice(viral_prompts)
                        prompt_v = f"Tulis 1 postingan Threads bahasa Indonesia gaya santai, relate, dan memancing komentar tentang: {topic}. Maksimal 250 karakter. Tanpa hashtag dan tanda kutip."
                        v_text = call_gemini(prompt_v)
                        new_rows.append([today_str, slots[0], target_acc, v_text, "", "", "", "PENDING", "", "", ""])

                        # 2-5. Konten Affiliate
                        sampled = random.sample(ready_prods, 4)
                        for i, prod in enumerate(sampled, start=1):
                            prompt_a = f"Tulis hook teks Threads bahasa Indonesia santai gaya curhat tanpa hard-selling untuk: '{prod['product_name']}' ({prod.get('highlight', '')}). Maks 220 karakter. Tanpa hashtag dan tanda kutip."
                            main_txt = call_gemini(prompt_a)
                            reply_txt = f"Yang mau samaan atau cek racunnya, belinya di sini ya:\n{prod['affiliate_link']}"
                            new_rows.append([today_str, slots[i], target_acc, main_txt, "", reply_txt, prod["affiliate_link"], "PENDING", "", "", ""])

                        for r in new_rows:
                            data_ws.append_row(r)

                        st.success("🎉 Berhasil membuat 5 konten baru ke antrean Google Sheets!")
                        st.rerun()
            except Exception as ex:
                st.error(f"Terjadi kesalahan: {ex}")

# ==============================================================================
# TAB 2: KATALOG PRODUK (BISA SAMPAI 50+ & BISA DIEDIT KAPAN SAJA)
# ==============================================================================
with tabs[1]:
    st.subheader("📦 Katalog Produk Affiliate")
    st.write("Katalog ini mampu menampung puluhan produk. Bot AI secara acak memilih produk berstatus **READY** setiap hari.")

    prod_ws = sh.worksheet("Products")
    raw_prods = prod_ws.get_all_records()

    # Kolom standar
    expected_cols = ["product_name", "highlight", "affiliate_link", "category", "status"]

    if raw_prods:
        df_prods = pd.DataFrame(raw_prods)
        # Pastikan kolom sesuai
        for col in expected_cols:
            if col not in df_prods.columns:
                df_prods[col] = ""
        df_prods = df_prods[expected_cols]
    else:
        df_prods = pd.DataFrame(columns=expected_cols)

    # Indikator Ringkasan
    total_items = len(df_prods)
    ready_items = len(df_prods[df_prods["status"].astype(str).str.upper() == "READY"]) if not df_prods.empty else 0

    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Total Produk Terdaftar", f"{total_items} Item")
    c_m2.metric("Produk Siap Dipakai (READY)", f"{ready_items} Item")
    c_m3.metric("Kapasitas", "50+ Item (Aktif)")

    st.divider()

    # --- FITUR 1: EDIT LANGSUNG DI TABEL (EXCEL STYLE) ---
    st.write("#### 📝 Edit Langsung di Tabel (Bisa ubah nama, link, highlight, dan status)")
    st.caption("Klik dua kali pada kotak mana saja untuk mengedit. Anda juga bisa menambah atau menghapus baris langsung di tabel ini.")

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
        with st.spinner("Menyimpan seluruh katalog ke Google Sheets..."):
            try:
                # Bersihkan tab Products dan tulis ulang
                prod_ws.clear()
                header = [expected_cols]
                data_rows = edited_df.fillna("").values.tolist()
                prod_ws.update(header + data_rows)
                st.success("✅ Katalog berhasil diperbarui sepenuhnya!")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menyimpan ke Google Sheets: {e}")

    st.divider()

    # --- FITUR 2: EDIT SPESIFIK LEWAT FORM (SANGAT MUDAH DI HP) ---
    with st.expander("✏️ Atau Edit Produk Tertentu via Form (Pilihan praktis di HP)"):
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
                    sheet_row = p_idx + 2  # baris 1 header
                    prod_ws.update_cell(sheet_row, 1, e_name)
                    prod_ws.update_cell(sheet_row, 2, e_hl)
                    prod_ws.update_cell(sheet_row, 3, e_link)
                    prod_ws.update_cell(sheet_row, 4, e_cat)
                    prod_ws.update_cell(sheet_row, 5, e_stat)
                    st.success(f"✅ Produk '{e_name}' berhasil diperbarui!")
                    st.rerun()
        else:
            st.info("Katalog masih kosong.")

    # --- FITUR 3: TAMBAH PRODUK BARU SATU PER SATU ---
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
                    prod_ws.append_row([new_name, new_hl, new_link, new_cat, new_stat])
                    st.success(f"✅ Produk '{new_name}' berhasil ditambahkan ke katalog!")
                    st.rerun()

# ==============================================================================
# TAB 3: BUAT KONTEN MANUAL (FITUR ASLI ANDA)
# ==============================================================================
with tabs[2]:
    st.subheader("✍️ Pembuat Konten Manual")
    st.write("Gunakan fitur ini jika Anda ingin menulis postingan khusus di luar jadwal autopilot.")

    accs = sh.worksheet("Accounts").get_all_records()
    acc_names = [a["name"] for a in accs] if accs else []

    with st.form("form_manual_post"):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            sel_acc = st.selectbox("Target Akun", acc_names if acc_names else ["Belum ada akun"])
            m_date = st.date_input("Tanggal Posting", value=now.date())
        with col_m2:
            m_time = st.time_input("Jam Posting", value=now.time())
            m_link = st.text_input("Link Affiliate (Opsional)", placeholder="https://...")

        m_main = st.text_area("Teks Postingan Utama", placeholder="Tulis isi thread utama Anda di sini...", height=100)
        m_reply = st.text_area("Teks Balasan / Link (Opsional)", placeholder="Tulis balasan pertama (misal link belanja)...", height=80)

        submit_manual = st.form_submit_button("Jadwalkan Postingan Manual")

        if submit_manual:
            if not m_main:
                st.error("Teks utama tidak boleh kosong!")
            else:
                data_ws = sh.worksheet("data")
                data_ws.append_row([
                    m_date.strftime("%Y-%m-%d"),
                    m_time.strftime("%H:%M"),
                    sel_acc,
                    m_main,
                    "",
                    m_reply,
                    m_link,
                    "PENDING",
                    "", "", ""
                ])
                st.success("✅ Postingan manual berhasil dimasukkan ke antrean!")
                st.rerun()

# ==============================================================================
# TAB 4: ANTREAN & RIWAYAT POSTINGAN
# ==============================================================================
with tabs[3]:
    st.subheader("📋 Daftar Antrean & Status Postingan")
    data_ws = sh.worksheet("data")
    all_data = data_ws.get_all_records()

    if all_data:
        st.dataframe(all_data, use_container_width=True)
    else:
        st.info("Belum ada antrean di tab data.")

# ==============================================================================
# TAB 5: AKUN THREADS
# ==============================================================================
with tabs[4]:
    st.subheader("⚙️ Akun Threads Terhubung")
    acc_ws = sh.worksheet("Accounts")
    acc_data = acc_ws.get_all_records()

    if acc_data:
        st.dataframe(acc_data, use_container_width=True)
    else:
        st.warning("Belum ada akun yang terdaftar di tab Accounts.")
