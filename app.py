import os
import re
import json
import base64
import random
import logging
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

# --- KONEKSI GOOGLE SHEETS DENGAN REFRESH OTOMATIS ---
@st.cache_resource(ttl=300)
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
        st.error(f"Gagal otentikasi Google Cloud: {e}")
        return None

def get_sheet_safely():
    client = get_gspread_client()
    if not client or not SPREADSHEET_ID:
        return None
    try:
        return client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        st.error(f"Gagal membuka spreadsheet ({SPREADSHEET_ID}): {e}")
        return None

sh = get_sheet_safely()

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
    if "candidates" in res and res["candidates"]:
        return res["candidates"][0]["content"]["parts"][0]["text"].strip()
    # Fallback model jika kuota flash padat
    url2 = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={AI_API_KEY}"
    res2 = requests.post(url2, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30).json()
    if "candidates" in res2 and res2["candidates"]:
        return res2["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise Exception(f"Gemini error: {res}")

# --- TAMPILAN UTAMA ---
st.title("🧵 Threads Affiliate & Autopilot Dashboard")
st.caption("Pusat kendali pembuatan konten manual, Content Studio AI (Jumlah, Selang Waktu & Kurasi 5 Produk), katalog produk, dan autopilot.")

if not sh:
    st.error("Kredensial SPREADSHEET_ID atau GCP_CREDS_BASE64 belum terpasang dengan benar di Secrets Streamlit.")
    st.stop()

tabs = st.tabs([
    "⚡ Kontrol Autopilot",
    "📦 Katalog Produk (50+ Items)",
    "✍️ Content Studio (Manual & AI)",
    "📋 Antrean & Riwayat",
    "⚙️ Akun Threads"
])

# ==============================================================================
# TAB 1: KONTROL AUTOPILOT (START & END DATE / TIME)
# ==============================================================================
with tabs[0]:
    st.subheader("Pengaturan Jadwal Aktif Autopilot")
    st.write("Atur tanggal dan jam mulai serta berakhirnya sistem autopilot. Di luar rentang ini, bot tidak akan memposting.")

    try:
        cfg_sheet = sh.worksheet("Config")
        cfg_rows = cfg_sheet.get_all_values()
        cfg_data = {}
        for r in cfg_rows:
            if len(r) >= 2 and r[0].strip():
                cfg_data[r[0].strip()] = r[1].strip()
    except Exception as e:
        st.warning(f"Tidak dapat membaca tab Config: {e}. Menggunakan nilai default.")
        cfg_data = {}

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
        try:
            new_start_str = f"'{start_d.strftime('%Y-%m-%d')} {start_t.strftime('%H:%M')}"
            new_end_str = f"'{end_d.strftime('%Y-%m-%d')} {end_t.strftime('%H:%M')}"
            
            cfg_sheet.update_cell(2, 1, "start_datetime")
            cfg_sheet.update_cell(2, 2, new_start_str)
            cfg_sheet.update_cell(3, 1, "end_datetime")
            cfg_sheet.update_cell(3, 2, new_end_str)
            st.success("✅ Jadwal autopilot berhasil diperbarui!")
            st.rerun()
        except Exception as e:
            st.error(f"Gagal menyimpan ke Google Sheets: {e}")

    st.divider()

    st.write("#### ⚡ Eksekusi Cepat: Generate 5 Konten Hari Ini")
    st.caption("Menghasilkan 1 konten viral (08:15) dan 4 konten affiliate acak langsung ke antrean `data`.")

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

                        topic = random.choice(viral_prompts)
                        prompt_v = f"Tulis 1 postingan Threads bahasa Indonesia gaya santai, relate, dan memancing komentar tentang: {topic}. Maksimal 250 karakter. Tanpa hashtag dan tanda kutip."
                        v_text = call_gemini(prompt_v)
                        new_rows.append([today_str, slots[0], target_acc, v_text, "", "", "", "PENDING", "", "", ""])

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

    try:
        prod_ws = sh.worksheet("Products")
        raw_prods = prod_ws.get_all_records()
    except Exception as e:
        st.error(f"Gagal membaca tab Products: {e}")
        raw_prods = []

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
        with st.spinner("Menyimpan seluruh katalog ke Google Sheets..."):
            try:
                prod_ws.clear()
                header = [expected_cols]
                data_rows = edited_df.fillna("").values.tolist()
                prod_ws.update(header + data_rows)
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
                    sheet_row = p_idx + 2
                    prod_ws.update_cell(sheet_row, 1, e_name)
                    prod_ws.update_cell(sheet_row, 2, e_hl)
                    prod_ws.update_cell(sheet_row, 3, e_link)
                    prod_ws.update_cell(sheet_row, 4, e_cat)
                    prod_ws.update_cell(sheet_row, 5, e_stat)
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
                    prod_ws.append_row([new_name, new_hl, new_link, new_cat, new_stat])
                    st.success(f"✅ Produk '{new_name}' berhasil ditambahkan ke katalog!")
                    st.rerun()

# ==============================================================================
# TAB 3: CONTENT STUDIO (LENGKAP: JUMLAH, SELANG WAKTU & KURASI HINGGA 5 PRODUK)
# ==============================================================================
with tabs[2]:
    st.subheader("✍️ Content Studio (Pembuat Konten Manual & AI)")
    st.caption("Atur target akun, waktu mulai, jumlah postingan, selang waktu (interval), dan kurasi hingga 5 produk.")

    try:
        accs = sh.worksheet("Accounts").get_all_records()
        acc_names = [a["name"] for a in accs] if accs else []
    except Exception:
        acc_names = []

    try:
        prod_ws = sh.worksheet("Products")
        raw_p = prod_ws.get_all_records()
        all_ready_p = [p for p in raw_p if str(p.get("status", "")).strip().upper() == "READY"]
    except Exception:
        all_ready_p = []

    if "manual_generated_posts" not in st.session_state:
        st.session_state["manual_generated_posts"] = []

    # 1. PENGATURAN UTAMA: TARGET, WAKTU MULAI, JUMLAH & SELANG WAKTU
    st.write("#### ⚙️ 1. Pengaturan Jadwal & Frekuensi")
    c_set1, c_set2, c_set3, c_set4 = st.columns(4)
    with c_set1:
        target_account = st.selectbox("🎯 Target Akun Threads", acc_names if acc_names else ["Belum ada akun"])
    with c_set2:
        schedule_d = st.date_input("📅 Tanggal Mulai", value=now.date(), key="cs_date")
    with c_set3:
        schedule_t = st.time_input("⏰ Jam Mulai", value=now.time(), key="cs_time")
    with c_set4:
        interval_mins = st.selectbox(
            "⏳ Selang Waktu (Interval)",
            options=[15, 30, 45, 60, 90, 120, 180, 240],
            index=3,
            format_func=lambda x: f"{x} Menit Sekali"
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
        "Curhat Santai & Relate (Bahasa Threads anak muda)",
        "Storytelling Pengalaman Pribadi (Masalah -> Solusi)",
        "Review Jujur & Solutif (Highlight keunggulan produk)",
        "Racun Belanja Shopee (Antusias & bikin pengen checkout)",
        "Serahkan ke AI (Smart Adaptive Copywriting)"
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
                            
                            prompt_hook = (
                                f"Tulis 1 postingan pembuka (hook) Threads bahasa Indonesia yang bikin penasaran tentang: '{listicle_title}'. "
                                f"Gaya penulisan: {ls_style}. Maksimal 220 karakter. DILARANG pakai hashtag, tanpa tanda kutip."
                            )
                            hook_text = call_gemini(prompt_hook)

                            # Balasan berurutan 1 sampai 5
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
                        st.success(f"🎉 Berhasil meracik {len(gen_list)} draf postingan kurasi dengan selang waktu {interval_mins} menit!")
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

                        prompt = (
                            f"Tulis 1 postingan Threads bahasa Indonesia yang santai, tidak hard-selling, dan relate untuk produk: '{p_name}' "
                            f"(Keunggulan: '{p_hl}'). Gaya bahasa: {sp_style}. Maksimal 220 karakter. Tanpa hashtag dan tanda kutip."
                        )
                        main_t = call_gemini(prompt)
                        reply_t = f"Yang mau samaan atau cek racunnya, belinya di sini ya:\n{p_link}" if p_link else ""

                        gen_list.append({
                            "date": p_dt.strftime("%Y-%m-%d"),
                            "time": p_dt.strftime("%H:%M"),
                            "account": target_account,
                            "main": main_t,
                            "reply": reply_t,
                            "link": p_link
                        })

                    st.session_state["manual_generated_posts"] = gen_list
                    st.success(f"🎉 Berhasil membuat {len(gen_list)} draf dengan jeda selang {interval_mins} menit!")
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
                    topics_pool = [
                        "Dilema dunia kerja, lembur, dan overthinking karir",
                        "Belanja impulsif vs resolusi hemat yang selalu gagal",
                        "Curhat realita tinggal di kota besar dan biaya hidup",
                        "Gaya hidup FOMO vs ketenangan hidup sederhana"
                    ]
                    for post_idx in range(num_posts):
                        p_dt = base_dt + timedelta(minutes=post_idx * interval_mins)
                        curr_topic = vb_topic if vb_topic else random.choice(topics_pool)
                        prompt = (
                            f"Tulis 1 postingan Threads bahasa Indonesia yang sangat relatable dan memicu interaksi/komentar warganet tentang: '{curr_topic}'. "
                            f"Angle: {vb_style}. Maksimal 250 karakter. DILARANG pakai hashtag, tanpa tanda kutip."
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
                    st.success(f"🎉 Berhasil membuat {len(gen_list)} draf viral organik dengan selang waktu {interval_mins} menit!")
                except Exception as e:
                    st.error(f"Gagal generate: {e}")

    # --- MODE D: TULIS BEBAS MANUAL ---
    elif content_mode == "✍️ Tulis Bebas Manual":
        st.write("##### ✍️ Tambahkan Draf Manual dengan Selang Waktu")
        c_man1, c_man2 = st.columns(2)
        with c_man1:
            man_main = st.text_area("Teks Postingan Utama", placeholder="Ketik teks utama di sini...", height=120)
        with c_man2:
            man_reply = st.text_area("Teks Balasan / Link (Opsional)", placeholder="Ketik teks balasan atau link...", height=120)
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
    st.caption("Periksa dan sunting teks sebelum menyimpan ke Google Sheets.")

    posts_to_show = st.session_state.get("manual_generated_posts", [])

    if not posts_to_show:
        st.info("Belum ada draf yang digenerate. Klik tombol generate di atas untuk mulai membuat postingan.")
    else:
        st.write(f"Total antrean siap simpan: **{len(posts_to_show)} postingan**")
        
        for idx_p, p_item in enumerate(posts_to_show):
            with st.container():
                st.markdown(f"**📌 Post #{idx_p + 1} — Jadwal: `{p_item['date']} {p_item['time']} WIB`**")
                col_box1, col_box2 = st.columns(2)
                with col_box1:
                    p_item["main"] = st.text_area(f"Teks Utama #{idx_p + 1}", value=p_item["main"], height=90, key=f"preview_main_{idx_p}")
                with col_box2:
                    p_item["reply"] = st.text_area(f"Balasan / Link #{idx_p + 1}", value=p_item["reply"], height=90, key=f"preview_reply_{idx_p}")
                    p_item["link"] = st.text_input(f"Link #{idx_p + 1}", value=p_item["link"], key=f"preview_link_{idx_p}")

        col_b1, col_b2 = st.columns([2, 1])
        with col_b1:
            if st.button("💾 Simpan & Jadwalkan Semua ke Google Sheets", type="primary"):
                with st.spinner("Menyimpan ke antrean Google Sheets..."):
                    try:
                        data_ws = sh.worksheet("data")
                        for itm in posts_to_show:
                            data_ws.append_row([
                                itm["date"],
                                itm["time"],
                                itm["account"],
                                itm["main"],
                                "",
                                itm["reply"],
                                itm["link"],
                                "PENDING",
                                "", "", ""
                            ])
                        st.success(f"🎉 Berhasil menyimpan {len(posts_to_show)} postingan ke tab 'data'!")
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
    try:
        data_ws = sh.worksheet("data")
        all_data = data_ws.get_all_records()
        if all_data:
            st.dataframe(all_data, use_container_width=True)
        else:
            st.info("Belum ada antrean di tab data.")
    except Exception as e:
        st.error(f"Gagal membaca tab data: {e}")

# ==============================================================================
# TAB 5: AKUN THREADS
# ==============================================================================
with tabs[4]:
    st.subheader("⚙️ Akun Threads Terhubung")
    try:
        acc_ws = sh.worksheet("Accounts")
        acc_data = acc_ws.get_all_records()
        if acc_data:
            st.dataframe(acc_data, use_container_width=True)
        else:
            st.warning("Belum ada akun yang terdaftar di tab Accounts.")
    except Exception as e:
        st.error(f"Gagal membaca tab Accounts: {e}")
