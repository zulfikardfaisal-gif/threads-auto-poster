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
st.caption("Pusat kendali pembuatan konten manual, Content Studio AI, katalog produk, dan pengaturan autopilot.")

if not sh:
    st.error("Kredensial SPREADSHEET_ID atau GCP_CREDS_BASE64 belum terpasang di Secrets Streamlit.")
    st.stop()

tabs = st.tabs([
    "⚡ Kontrol Autopilot",
    "📦 Katalog Produk (50+ Items)",
    "✍️ Content Studio (Buat Konten Manual & AI)",
    "📋 Antrean & Riwayat",
    "⚙️ Akun Threads"
])

# ==============================================================================
# TAB 1: KONTROL AUTOPILOT (START & END DATE / TIME)
# ==============================================================================
with tabs[0]:
    st.subheader("Pengaturan Jadwal Aktif Autopilot")
    st.write("Atur tanggal dan jam mulai serta berakhirnya sistem autopilot. Di luar rentang ini, bot tidak akan memposting.")

    # Membaca tab Config secara aman (hanya mengambil Kolom A dan B)
    cfg_sheet = sh.worksheet("Config")
    cfg_rows = cfg_sheet.get_all_values()
    cfg_data = {}
    for r in cfg_rows:
        if len(r) >= 2 and r[0].strip():
            cfg_data[r[0].strip()] = r[1].strip()

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
# TAB 3: CONTENT STUDIO (FORMAT MANUAL ASLI LENGKAP DENGAN FITUR AI)
# ==============================================================================
with tabs[2]:
    st.subheader("✍️ Content Studio (Pembuat Konten Threads)")
    st.caption("Buat konten manual secara mandiri atau gunakan bantuan copywriting AI dengan pilihan gaya penulisan lengkap.")

    # Ambil daftar akun & produk dari Sheets
    accs = sh.worksheet("Accounts").get_all_records()
    acc_names = [a["name"] for a in accs] if accs else []
    
    prod_ws = sh.worksheet("Products")
    raw_p = prod_ws.get_all_records()
    all_ready_p = [p for p in raw_p if str(p.get("status", "")).strip().upper() == "READY"]

    # Inisialisasi Session State untuk form
    if "studio_main_text" not in st.session_state:
        st.session_state["studio_main_text"] = ""
    if "studio_reply_text" not in st.session_state:
        st.session_state["studio_reply_text"] = ""
    if "studio_link" not in st.session_state:
        st.session_state["studio_link"] = ""
    if "studio_img" not in st.session_state:
        st.session_state["studio_img"] = ""

    # 1. Parameter Penjadwalan & Akun
    col_cs1, col_cs2, col_cs3 = st.columns([1.5, 1, 1])
    with col_cs1:
        target_account = st.selectbox("🎯 Target Akun Threads", acc_names if acc_names else ["Belum ada akun"])
    with col_cs2:
        schedule_d = st.date_input("📅 Tanggal Posting", value=now.date(), key="cs_date")
    with col_cs3:
        schedule_t = st.time_input("⏰ Jam Posting", value=now.time(), key="cs_time")

    st.divider()

    # 2. Pilihan Mode Konten (Sesuai format asli Content Studio Anda)
    content_mode = st.radio(
        "Pilih Jenis Konten:",
        [
            "🛍️ Single Product Affiliate",
            "📑 Multi-link Listicle (Kurasi Produk)",
            "🚀 Viral Booster (Engagement Organik / Tanpa Link)",
            "✍️ Tulis Manual Bebas (Tanpa AI)"
        ],
        horizontal=True
    )

    # Gaya Bahasa Copywriting
    copy_styles = [
        "Curhat Santai & Relate (Gaya bahasa Threads anak muda)",
        "Storytelling / Pengalaman Pribadi (Bercerita masalah -> solusi)",
        "Review Jujur & Solutif (Highlight kelebihan produk)",
        "Rekomendasi Racun Shopee (Antusias, racun belanja)",
        "Serahkan ke AI (Smart Adaptive Copywriting)"
    ]

    # --- FORM SESUAI MODE KONTEN ---
    if content_mode == "🛍️ Single Product Affiliate":
        col_sp1, col_sp2 = st.columns(2)
        with col_sp1:
            # Bisa pilih dari katalog atau ketik manual
            prod_names_list = ["-- Ketik Nama Manual --"] + [p["product_name"] for p in all_ready_p]
            sel_prod_dropdown = st.selectbox("Pilih Produk dari Katalog READY:", prod_names_list)

            if sel_prod_dropdown != "-- Ketik Nama Manual --":
                chosen_p = next(p for p in all_ready_p if p["product_name"] == sel_prod_dropdown)
                def_name = chosen_p["product_name"]
                def_hl = chosen_p.get("highlight", "")
                def_link = chosen_p.get("affiliate_link", "")
            else:
                def_name, def_hl, def_link = "", "", ""

            sp_name = st.text_input("Nama Produk", value=def_name, placeholder="Misal: Elvicto Brightening Serum")
            sp_link = st.text_input("Link Affiliate", value=def_link, placeholder="https://s.shopee.co.id/...")

        with col_sp2:
            sp_hl = st.text_input("Keunggulan / Highlight Singkat", value=def_hl, placeholder="Bikin cerah, tekstur ringan, gak lengket")
            sp_style = st.selectbox("Gaya Bahasa AI:", copy_styles)

        if st.button("✨ Generate Copywriting via AI"):
            if not sp_name:
                st.error("Nama produk tidak boleh kosong!")
            else:
                with st.spinner("AI sedang meracik copywriting Threads..."):
                    try:
                        prompt = (
                            f"Buat 1 postingan Threads bahasa Indonesia yang natural, sangat menarik, tidak kaku, dan relate. "
                            f"Topik: Membahas produk '{sp_name}' dengan keunggulan: '{sp_hl}'. "
                            f"Gaya penulisan: {sp_style}. "
                            f"Maksimal 250 karakter. DILARANG menggunakan hashtag, dan JANGAN menyertakan link di teks utama."
                        )
                        st.session_state["studio_main_text"] = call_gemini(prompt)
                        st.session_state["studio_link"] = sp_link
                        st.session_state["studio_reply_text"] = f"Yang mau samaan atau cek racunnya, belinya di sini ya:\n{sp_link}" if sp_link else ""
                        st.success("✅ Konten berhasil diracik AI! Silakan cek & edit di bagian Preview di bawah.")
                    except Exception as e:
                        st.error(f"Gagal generate: {e}")

    elif content_mode == "📑 Multi-link Listicle (Kurasi Produk)":
        st.write("Buat kurasi rekomendasi beberapa produk sekaligus (seperti *'5 Rekomendasi Parfum Tahan Lama'*).")
        col_ls1, col_ls2 = st.columns(2)
        with col_ls1:
            listicle_title = st.text_input("Judul / Tema Kurasi", placeholder="Contoh: 3 Barang Meja Kerja yang Bikin Produktif")
            if all_ready_p:
                selected_prods_listicle = st.multiselect(
                    "Pilih Produk dari Katalog:",
                    [p["product_name"] for p in all_ready_p],
                    default=[p["product_name"] for p in all_ready_p[:3]] if len(all_ready_p) >= 3 else []
                )
            else:
                selected_prods_listicle = []
        with col_ls2:
            ls_style = st.selectbox("Gaya Penulisan AI:", copy_styles)

        if st.button("✨ Generate Listicle via AI"):
            if not listicle_title:
                st.error("Judul kurasi wajib diisi!")
            else:
                with st.spinner("AI sedang menyusun draf Listicle..."):
                    try:
                        prompt_hook = (
                            f"Tulis draf postingan pembuka (hook) Threads bahasa Indonesia yang memancing rasa penasaran tentang kurasi: '{listicle_title}'. "
                            f"Gaya bahasa: {ls_style}. Maksimal 220 karakter. Jangan pakai hashtag."
                        )
                        st.session_state["studio_main_text"] = call_gemini(prompt_hook)
                        
                        # Susun Reply Text bernomor persis format Anda: 1. Nama Barang ✨\nLink
                        reply_lines = []
                        chosen_objs = [p for p in all_ready_p if p["product_name"] in selected_prods_listicle]
                        for i, item in enumerate(chosen_objs, start=1):
                            reply_lines.append(f"{i}. {item['product_name']} ✨\n{item['affiliate_link']}")
                        
                        st.session_state["studio_reply_text"] = "\n\n".join(reply_lines)
                        st.session_state["studio_link"] = chosen_objs[0]["affiliate_link"] if chosen_objs else ""
                        st.success("✅ Draf Listicle berhasil diracik! Cek preview di bawah.")
                    except Exception as e:
                        st.error(f"Gagal generate: {e}")

    elif content_mode == "🚀 Viral Booster (Engagement Organik / Tanpa Link)":
        st.write("Postingan non-affiliate tanpa link untuk memicu komentar, likes, dan meningkatkan reputasi akun.")
        col_vb1, col_vb2 = st.columns(2)
        with col_vb1:
            vb_topic = st.text_input("Topik / Isu yang Dibahas", placeholder="Misal: Realita kerja lembur tapi gaji pas-pasan")
        with col_vb2:
            vb_style = st.selectbox("Sudut Pandang / Angle AI:", [
                "Opini Kontroversial / Debat Santai",
                "Humor Realita & Sambat Lucu",
                "Pertanyaan Pemancing Diskusi (Q&A)",
                "Storytelling Pengalaman Pribadi"
            ])

        if st.button("✨ Generate Viral Booster via AI"):
            if not vb_topic:
                st.error("Topik bahasan tidak boleh kosong!")
            else:
                with st.spinner("AI sedang meracik hook diskusi viral..."):
                    try:
                        prompt_vb = (
                            f"Tulis 1 postingan Threads bahasa Indonesia yang sangat relatable dan memicu interaksi/komentar tentang: '{vb_topic}'. "
                            f"Angle/Gaya: {vb_style}. Maksimal 250 karakter. DILARANG pakai hashtag, tanpa tanda petik."
                        )
                        st.session_state["studio_main_text"] = call_gemini(prompt_vb)
                        st.session_state["studio_reply_text"] = ""
                        st.session_state["studio_link"] = ""
                        st.success("✅ Konten Viral Booster siap!")
                    except Exception as e:
                        st.error(f"Gagal generate: {e}")

    elif content_mode == "✍️ Tulis Manual Bebas (Tanpa AI)":
        st.info("Ketik langsung teks postingan dan balasan link Anda secara bebas pada kotak formulir di bawah ini.")

    st.divider()

    # --- 3. KOTAK EDIT & PREVIEW LENGKAP (BISA DIEDIT SEBELUM SIMPAN KE DATA) ---
    st.write("#### 📝 Preview & Finalisasi Konten")
    st.caption("Anda dapat menyunting isi teks di bawah ini sebelum menjadwalkannya ke Google Sheets.")

    with st.form("form_finalize_studio_post"):
        col_pv1, col_pv2 = st.columns(2)
        with col_pv1:
            final_main = st.text_area(
                "Teks Postingan Utama (main_text) *",
                value=st.session_state.get("studio_main_text", ""),
                height=130,
                placeholder="Tulis draf postingan utama di sini..."
            )
            final_img = st.text_input(
                "URL Gambar Utama (main_image_url - Opsional)",
                value=st.session_state.get("studio_img", ""),
                placeholder="https://images... (kosongkan jika hanya teks)"
            )
        with col_pv2:
            final_reply = st.text_area(
                "Teks Balasan / Link (reply_text - Opsional)",
                value=st.session_state.get("studio_reply_text", ""),
                height=130,
                placeholder="Balasan pertama (link pembelian atau lanjutan curhat)..."
            )
            final_link = st.text_input(
                "Link Affiliate Cadangan (affiliate_link - Opsional)",
                value=st.session_state.get("studio_link", ""),
                placeholder="https://s.shopee.co.id/..."
            )

        btn_save_to_sheet = st.form_submit_button("💾 Jadwalkan & Simpan ke Antrean Sheets", type="primary")

        if btn_save_to_sheet:
            if not final_main.strip():
                st.error("Teks postingan utama wajib diisi!")
            else:
                try:
                    data_ws = sh.worksheet("data")
                    row_payload = [
                        schedule_d.strftime("%Y-%m-%d"),
                        schedule_t.strftime("%H:%M"),
                        target_account,
                        final_main.strip(),
                        final_img.strip(),
                        final_reply.strip(),
                        final_link.strip(),
                        "PENDING",
                        "", "", ""
                    ]
                    data_ws.append_row(row_payload)
                    st.success(f"🎉 Postingan berhasil dijadwalkan ke tab 'data' untuk tanggal {schedule_d} jam {schedule_t} WIB!")
                    # Reset session state
                    st.session_state["studio_main_text"] = ""
                    st.session_state["studio_reply_text"] = ""
                    st.session_state["studio_link"] = ""
                    st.session_state["studio_img"] = ""
                    st.rerun()
                except Exception as ex:
                    st.error(f"Gagal menyimpan ke Google Sheets: {ex}")

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
