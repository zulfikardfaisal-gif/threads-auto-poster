import os
import re
import json
import base64
import logging
import random
import time
from datetime import datetime, timedelta
import pytz
import requests
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ThreadsWorker")
TZ = pytz.timezone("Asia/Jakarta")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GCP_CREDS_BASE64 = os.environ.get("GCP_CREDS_BASE64")
AI_API_KEY = os.environ.get("AI_API_KEY")

# --- MATRIKS TOPIK & HOOK VIRAL ---
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

VIRAL_HOOK_PATTERNS = [
    "Trigger Drama Relasi: Awali baris 1 HURUF KAPITAL dialog/reaksi pasangan/mertua/roommate (misal: 'SUAMI: KAMU KALAU NYUCI KOK RIBET BANGET? 😭😂').",
    "Trigger Nyaris Malu: Awali baris 1 HURUF KAPITAL momen panik di tempat umum/kantor (misal: 'HAMPIR MALU DI RUANG MEETING GARA-GARA BASKET 😭💀').",
    "Trigger Otoritas: Awali baris 1 HURUF KAPITAL terima kasih ke dokter/ahli/tukang servis (misal: 'MAKASIH MBAK DOKTER YANG UDAH KASI TAU INI 😭🙏').",
    "Trigger Kaum Mager: Awali baris 1 HURUF KAPITAL pengakuan malas tapi nemu jalan pintas (misal: 'SEBAGAI ORANG YANG PALING MALES NYIKAT KAMAR MANDI... 🤣').",
    "Trigger Satisfying: Awali baris 1 HURUF KAPITAL kepuasan visual/sensorik (misal: 'THERAPY TERBAIK MINGGU INI: LIAT AIR RENDAMAN JADI BUTEK 🤤😭').",
    "Trigger Parno/Micro-Anxiety: Awali baris 1 HURUF KAPITAL rasa cemas/jijik (misal: 'PARNO TIAP LIAT KECOA MUNCUL DI KITCHEN SET 😭').",
    "Trigger Bukan Boros Tapi Investasi: Awali baris 1 HURUF KAPITAL pembelaan diri (misal: 'BUKAN FOMO, INI NAMANYA INVESTASI RUMAH TANGGA 🤣😭').",
    "Trigger Reverse Psychology: Awali baris 1 HURUF KAPITAL melarang audiens beli (misal: 'JANGAN PERNAH CO BARANG INI KALO GAK MAU KETAGIHAN 😭').",
    "Trigger Terlambat Sadar: Awali baris 1 HURUF KAPITAL heran baru tahu sekarang (misal: 'UMUR 27 TAHUN BARU SADAR BENDA KECIL INI NYELAMATIN PUNGGUNG 😭').",
    "Trigger Skeptis ke Tobat: Awali baris 1 HURUF KAPITAL mengira awalnya cuma gimik iklan (misal: 'KIRAIN CUMA GIMIK IKLAN LEBAY, TERNYATA EMANG SESAKTI ITU 😭').",
    "Trigger Tragedi Kamar Kos: Awali baris 1 HURUF KAPITAL problem ruang sempit (misal: 'MUSUH TERBESAR ANAK KOS: KAMAR LEMBAB SAMPE TAS BERJAMUR 😭😭').",
    "Trigger Callout + Hacks: Awali baris 1 HURUF KAPITAL panggilan segmen (misal: 'BUIBU MERAPAT ‼️' atau 'ANAK KOS SIMAK ‼️')."
]

CLOSING_NARRATIVES = [
    "Btw banyak yang nanya di DM, ini link toko resmi tempat aku beli ya mumpung masih promo:",
    "Biar gak salah beli atau dapet yang zonk, aku taro link official store-nya di sini ya:",
    "Yang mau samaan atau sekadar cek review pembeli lainnya, langsung kepoin di sini:",
    "Spill link belinya di sini ya guys, kemarin pas aku cek lagi ada diskon lumayan:",
    "Daripada ribet nyari tokonya satu-satu, langsung meluncur ke toko resminya di sini:",
    "Kalo mau checkout mending sekarang sebelum kehabisan stok, link belinya di sini:"
]

PROMPT_RULES_CLEAN = """
DILARANG:
1. JANGAN PERNAH bahas: gaji, tanggal tua, bokek, reksadana, tabungan, bayar kos, biaya hidup, atau uang.
2. JANGAN pakai format tanya-jawab brosur iklan ('Pusing dengan X? Y solusinya!').
3. DILARANG kata klise sales: 'solusinya', 'cukup dengan...', 'dijamin', 'hadir untuk Anda'.
ATURAN WAJIB:
- Baris 1 WAJIB 1 kalimat pendek HURUF KAPITAL + 1-2 emoji penahan jempol (1-3 detik).
- Ceritakan santai sudut pandang orang pertama ('aku', 'suami', 'roommate').
- Dilarang pakai hashtag (#) dan tanda kutip dua.
"""

def parse_flexible_dt(s: str) -> datetime:
    cleaned = str(s).strip().replace("'", "")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})$", cleaned)
    if not m:
        raise ValueError(f"Format datetime tidak sesuai: {s}")
    year, month, day, hour, minute = map(int, m.groups())
    return TZ.localize(datetime(year, month, day, hour, minute))

def get_sheets_client():
    creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def call_gemini(prompt: str) -> str:
    key = str(AI_API_KEY).strip().replace("'", "").replace('"', "") if AI_API_KEY else ""
    if not key:
        raise Exception("AI_API_KEY belum disetel di GitHub Secrets/Environment!")

    for ver in ["v1", "v1beta"]:
        for model in ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]:
            url = f"https://generativelanguage.googleapis.com/{ver}/models/{model}:generateContent?key={key}"
            try:
                r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=25)
                res = r.json()
                if "candidates" in res and res["candidates"]:
                    return res["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception:
                continue
    raise Exception("Gagal menghubungi Gemini API setelah beberapa percobaan.")

def generate_slots(total_count, start_time_str="08:15", end_time_str="21:30"):
    if total_count <= 0:
        return []
    if total_count == 1:
        return ["12:00"]
    h1, m1 = map(int, start_time_str.split(":"))
    h2, m2 = map(int, end_time_str.split(":"))
    start_m = h1 * 60 + m1
    end_m = h2 * 60 + m2
    step = (end_m - start_m) / (total_count - 1)
    slots = []
    for i in range(total_count):
        curr_m = 5 * round(int(round(start_m + i * step)) / 5)
        slots.append(f"{curr_m // 60:02d}:{curr_m % 60:02d}")
    return slots

def arrange_post_types(num_viral: int, num_text: int, num_video: int) -> list:
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

def prepare_media_for_post(media_raw: str) -> str:
    if not media_raw:
        return ""
    parts = [p.strip() for p in str(media_raw).split(",") if p.strip()]
    if len(parts) == 1:
        single = parts[0]
        is_video = any(single.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in single
        if is_video:
            return f"{single}, {single}"
    return ", ".join(parts)

def generate_affiliate_replies(prod_name: str, prod_hl: str, aff_link: str, reply_count: int = 2) -> list:
    if not aff_link:
        return []
    intro = random.choice(CLOSING_NARRATIVES)
    final_reply = f"{intro}\n{aff_link}"
    if reply_count <= 1:
        return [final_reply]

    prompt = (
        f"Tulis 1 balasan singkat lanjutan (maksimal 100 karakter) menceritakan kepuasan pakai '{prod_name}' "
        f"(keunggulan: {prod_hl}). Santai, relate warganet Threads. DILARANG hashtag dan link."
    )
    try:
        inter = call_gemini(prompt).strip().strip('"')
    except Exception:
        inter = "Praktis banget sih ini buat pemakaian jangka panjang."
    return [inter, final_reply]

def check_and_auto_generate_daily(sh, today_str: str):
    """Mengecek apakah jadwal hari ini sudah ada di Sheets. Jika belum, racik otomatis via AI."""
    data_ws = sh.worksheet("data")
    all_vals = data_ws.get_all_values()

    # Cek apakah hari ini sudah ada jadwal (PENDING atau POSTED)
    has_today = False
    for r in all_vals[1:]:
        if len(r) > 0 and r[0].strip().replace("'", "").replace("/", "-") == today_str:
            has_today = True
            break

    if has_today:
        return  # Jadwal hari ini sudah terisi, lewati

    logger.info(f"⚡ JADWAL HARI INI ({today_str}) BELUM ADA! Menjalankan Auto-Generate via Gemini AI...")

    cfg_ws = sh.worksheet("Config")
    cfg = {r[0].strip(): r[1].strip() for r in cfg_ws.get_all_values() if len(r) >= 2 and r[0].strip()}

    # Cek apakah tanggal hari ini masih dalam rentang aktif Config
    start_str = cfg.get("start_datetime", "").strip().replace("'", "")
    end_str = cfg.get("end_datetime", "").strip().replace("'", "")
    if start_str and end_str:
        now_d = datetime.now(TZ).date()
        if not (parse_flexible_dt(start_str).date() <= now_d <= parse_flexible_dt(end_str).date()):
            logger.info("Di luar rentang tanggal aktif Config. Auto-generate dilewati.")
            return

    v_count = int(cfg.get("daily_viral_count", 2))
    t_count = int(cfg.get("daily_text_count", 2))
    m_count = int(cfg.get("daily_video_count", 4))
    target_acc = cfg.get("target_autopilot_account", "-- Semua Akun (All Accounts) --")

    accounts_ws = sh.worksheet("Accounts")
    acc_records = accounts_ws.get_all_records()
    if not acc_records:
        logger.error("Tidak ada akun di tab Accounts!")
        return

    target_accounts = [a["name"] for a in acc_records] if target_acc == "-- Semua Akun (All Accounts) --" else [target_acc]

    products_ws = sh.worksheet("Products")
    prods = [p for p in products_ws.get_all_records() if str(p.get("status", "")).strip().upper() == "READY"]
    if not prods:
        logger.error("Tidak ada produk berstatus READY di tab Products!")
        return

    prods_video = [p for p in prods if str(p.get("media_url", "")).strip()] or prods
    prods_text = [p for p in prods if not str(p.get("media_url", "")).strip()] or prods

    total_plan = v_count + t_count + m_count
    slots = generate_slots(total_plan)
    p_types = arrange_post_types(v_count, t_count, m_count)

    new_rows = []
    for acc in target_accounts:
        sampled_video = random.sample(prods_video, min(m_count, len(prods_video))) if m_count > 0 else []
        sampled_text = random.sample(prods_text, min(t_count, len(prods_text))) if t_count > 0 else []

        v_i, t_i = 0, 0
        for slot_time, p_type in zip(slots, p_types):
            hook_formula = random.choice(VIRAL_HOOK_PATTERNS)

            if p_type == "viral":
                cat = random.choice(list(TOPIC_PILLARS.keys()))
                topic = random.choice(TOPIC_PILLARS[cat])
                prompt = (
                    f"Tulis 1 postingan Threads bahasa Indonesia tentang: '{topic}'.\n{PROMPT_RULES_CLEAN}\n"
                    f"Maksimal 220 karakter. Langsung tulis teks postingan tanpa tanda kutip."
                )
                text = call_gemini(prompt)
                new_rows.append([today_str, slot_time, acc, text, "", "", "", "PENDING", "", "", "", ""])

            elif p_type == "video":
                p_cur = sampled_video[v_i] if v_i < len(sampled_video) else random.choice(prods)
                v_i += 1
                prompt = (
                    f"Tulis 1 postingan Threads bahasa Indonesia penahan jempol video produk: '{p_cur['product_name']}' "
                    f"(Keunggulan: {p_cur.get('highlight', '')}).\n- Hook Formula: {hook_formula}\n{PROMPT_RULES_CLEAN}\n"
                    f"Maksimal 220 karakter. Langsung tulis teks tanpa tanda kutip."
                )
                main_txt = call_gemini(prompt)
                reps = generate_affiliate_replies(p_cur['product_name'], p_cur.get('highlight', ''), p_cur['affiliate_link'])
                media = prepare_media_for_post(str(p_cur.get("media_url", "")).strip())
                new_rows.append([today_str, slot_time, acc, main_txt, media, "\n---REPLY---\n".join(reps), p_cur["affiliate_link"], "PENDING", "", "", "", ""])

            else:
                p_cur = sampled_text[t_i] if t_i < len(sampled_text) else random.choice(prods)
                t_i += 1
                prompt = (
                    f"Tulis 1 postingan Threads bahasa Indonesia rekomendasi teks produk: '{p_cur['product_name']}' "
                    f"(Keunggulan: {p_cur.get('highlight', '')}).\n- Hook Formula: {hook_formula}\n{PROMPT_RULES_CLEAN}\n"
                    f"Maksimal 220 karakter. Langsung tulis teks tanpa tanda kutip."
                )
                main_txt = call_gemini(prompt)
                reps = generate_affiliate_replies(p_cur['product_name'], p_cur.get('highlight', ''), p_cur['affiliate_link'])
                new_rows.append([today_str, slot_time, acc, main_txt, "", "\n---REPLY---\n".join(reps), p_cur["affiliate_link"], "PENDING", "", "", "", ""])

    for r in new_rows:
        data_ws.append_row(r)
    logger.info(f"🎉 SUKSES AUTO-GENERATE {len(new_rows)} postingan untuk hari ini ({today_str})!")

def clean_media_url(url: str) -> str:
    url = str(url).strip()
    if not url or "res.cloudinary.com" not in url or "/upload/" not in url:
        return url
    if any(url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in url:
        return url
    sat = random.choice([-4, -2, 2, 4])
    bri = random.choice([-2, -1, 1, 2])
    return url.replace("/upload/", f"/upload/e_saturation:{sat},e_brightness:{bri}/", 1)

def wait_for_container_ready(creation_id: str, access_token: str, max_retries: int = 35) -> bool:
    url = f"https://graph.threads.net/v1.0/{creation_id}?fields=status,error_message&access_token={access_token}"
    for attempt in range(max_retries):
        time.sleep(6)
        try:
            res = requests.get(url, timeout=15).json()
            status = res.get("status")
            logger.info(f"Cek status kontainer {creation_id} ({attempt + 1}/{max_retries}): {status}")
            if status in ["FINISHED", "PUBLISHED"]:
                return True
            if status == "ERROR":
                raise Exception(f"Meta Error: {res.get('error_message')}")
        except Exception as e:
            if "Meta Error" in str(e):
                raise
            logger.warning(f"Menunggu verifikasi kontainer: {e}")
    raise TimeoutError("Waktu pemrosesan di server Meta habis.")

def post_to_threads(user_id: str, access_token: str, text: str, media_url: str = None, reply_to: str = None) -> str:
    url_container = f"https://graph.threads.net/v1.0/{user_id}/threads"
    clean_text = str(text).strip()[:480]

    media_list = []
    if media_url:
        for p in [p.strip() for p in str(media_url).split(",") if p.strip()]:
            cleaned = clean_media_url(p)
            if cleaned:
                media_list.append(cleaned)

    # 1. Reply Komentar (Hijack) dengan Media
    if reply_to and media_list:
        m_url = media_list[0]
        is_vid = any(m_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in m_url
        payload = {"text": clean_text, "reply_to_id": reply_to, "access_token": access_token}
        payload["media_type"] = "VIDEO" if is_vid else "IMAGE"
        payload["video_url" if is_vid else "image_url"] = m_url
        res = requests.post(url_container, data=payload, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal buat komentar media: {res}")
        cid = res["id"]
        if is_vid:
            wait_for_container_ready(cid, access_token)
        else:
            time.sleep(3)

    # 2. Carousel Mandiri (2+ Media)
    elif not reply_to and len(media_list) > 1:
        child_ids = []
        for m_url in media_list:
            is_vid = any(m_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in m_url
            c_payload = {"is_carousel_item": "true", "access_token": access_token}
            c_payload["media_type"] = "VIDEO" if is_vid else "IMAGE"
            c_payload["video_url" if is_vid else "image_url"] = m_url
            c_res = requests.post(url_container, data=c_payload, timeout=30).json()
            if "id" not in c_res:
                raise Exception(f"Gagal buat item slide: {c_res}")
            c_id = c_res["id"]
            if is_vid:
                wait_for_container_ready(c_id, access_token)
            else:
                time.sleep(2)
            child_ids.append(c_id)

        res = requests.post(url_container, data={"media_type": "CAROUSEL", "children": ",".join(child_ids), "text": clean_text, "access_token": access_token}, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal buat kontainer Carousel: {res}")
        cid = res["id"]
        wait_for_container_ready(cid, access_token)

    # 3. Single Media Mandiri
    elif not reply_to and len(media_list) == 1:
        m_url = media_list[0]
        is_vid = any(m_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in m_url
        payload = {"text": clean_text, "access_token": access_token}
        payload["media_type"] = "VIDEO" if is_vid else "IMAGE"
        payload["video_url" if is_vid else "image_url"] = m_url
        res = requests.post(url_container, data=payload, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal buat media kontainer: {res}")
        cid = res["id"]
        if is_vid:
            wait_for_container_ready(cid, access_token)
        else:
            time.sleep(3)

    # 4. Teks Saja
    else:
        payload = {"media_type": "TEXT", "text": clean_text, "access_token": access_token}
        if reply_to:
            payload["reply_to_id"] = reply_to
        res = requests.post(url_container, data=payload, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal buat teks: {res}")
        cid = res["id"]
        time.sleep(2)

    # Publish
    pub_res = requests.post(f"https://graph.threads.net/v1.0/{user_id}/threads_publish", data={"creation_id": cid, "access_token": access_token}, timeout=30).json()
    if "id" not in pub_res:
        raise Exception(f"Gagal publish: {pub_res}")
    return pub_res["id"]

def main():
    if not SPREADSHEET_ID or not GCP_CREDS_BASE64:
        logger.error("Kredensial SPREADSHEET_ID atau GCP_CREDS_BASE64 belum disetel.")
        return

    client = get_sheets_client()
    sh = client.open_by_key(SPREADSHEET_ID)

    now_dt = datetime.now(TZ)
    now_time_str = now_dt.strftime("%H:%M")
    today_str = now_dt.strftime("%Y-%m-%d")
    logger.info(f"Waktu Server: {today_str} {now_time_str} WIB")

    # --- LANGKAH 1: AUTO-GENERATE KONTEN JIKA HARI INI KOSONG ---
    try:
        check_and_auto_generate_daily(sh, today_str)
    except Exception as e:
        logger.error(f"Gagal saat auto-generate harian: {e}")

    # --- LANGKAH 2: EKSEKUSI POSTINGAN PENDING ---
    accounts_ws = sh.worksheet("Accounts")
    accounts = {str(r["name"]).strip(): r for r in accounts_ws.get_all_records() if str(r.get("name", "")).strip()}

    data_ws = sh.worksheet("data")
    all_values = data_ws.get_all_values()

    for row_idx in range(1, len(all_values)):
        row = all_values[row_idx]
        sheet_row_num = row_idx + 1

        s_date = row[0].strip().replace("'", "").replace("/", "-") if len(row) > 0 else ""
        s_time = row[1].strip().replace("'", "") if len(row) > 1 else ""
        acc_name = row[2].strip().replace("'", "") if len(row) > 2 else ""
        main_text = row[3].strip() if len(row) > 3 else ""
        media_url = row[4].strip().replace("'", "") if len(row) > 4 else ""
        reply_raw = row[5].strip() if len(row) > 5 else ""
        status = row[7].strip().replace("'", "").upper() if len(row) > 7 else ""
        target_reply_id = row[11].strip().replace("'", "") if len(row) > 11 else ""

        if len(s_time) == 4 and s_time[1] == ":":
            s_time = "0" + s_time

        if status == "PENDING":
            if s_date > today_str:
                continue
            if s_date == today_str and s_time > now_time_str:
                continue

            acc = accounts.get(acc_name)
            if not acc:
                logger.warning(f"Akun '{acc_name}' tidak ditemukan!")
                continue

            user_id = str(acc["user_id"]).strip()
            token = str(acc["access_token"]).strip()

            logger.info(f"Posting Baris #{sheet_row_num} (Akun: {acc_name})...")
            try:
                if target_reply_id:
                    main_id = post_to_threads(user_id, token, main_text, media_url=media_url, reply_to=target_reply_id)
                else:
                    main_id = post_to_threads(user_id, token, main_text, media_url=media_url)

                if reply_raw:
                    reply_parts = [p.strip() for p in re.split(r"-{2,}\s*REPLY\s*-{2,}", reply_raw, flags=re.IGNORECASE) if p.strip()]
                    parent_id = main_id
                    for part in reply_parts:
                        time.sleep(4)
                        try:
                            parent_id = post_to_threads(user_id, token, part, reply_to=parent_id)
                        except Exception:
                            parent_id = post_to_threads(user_id, token, part, reply_to=main_id)

                data_ws.update_cell(sheet_row_num, 8, "POSTED")
                data_ws.update_cell(sheet_row_num, 9, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
                data_ws.update_cell(sheet_row_num, 10, main_id)
                data_ws.update_cell(sheet_row_num, 11, "")
                logger.info(f"Baris #{sheet_row_num} sukses!")
                break
            except Exception as e:
                logger.error(f"Gagal baris #{sheet_row_num}: {e}")
                data_ws.update_cell(sheet_row_num, 8, "FAILED")
                data_ws.update_cell(sheet_row_num, 11, str(e))
                break

if __name__ == "__main__":
    main()
