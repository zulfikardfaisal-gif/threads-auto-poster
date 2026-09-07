import os
import re
import json
import base64
import random
import logging
from datetime import datetime
import pytz
import requests
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("AutoPilot_Threads")
TZ = pytz.timezone("Asia/Jakarta")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GCP_CREDS_BASE64 = os.environ.get("GCP_CREDS_BASE64")
AI_API_KEY = os.environ.get("AI_API_KEY")

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

def parse_flexible_dt(s: str) -> datetime:
    cleaned = str(s).strip().replace("'", "")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})$", cleaned)
    if not m:
        raise ValueError(f"Format datetime tidak sesuai: {s}")
    year, month, day, hour, minute = map(int, m.groups())
    return TZ.localize(datetime(year, month, day, hour, minute))

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

# Pembacaan tab Config secara aman (kebal jika ada 3 kolom) & pengecekan tanggal
def is_active_window(sh) -> tuple:
    try:
        cfg_ws = sh.worksheet("Config")
        cfg_rows = cfg_ws.get_all_values()
        raw_cfg = {}
        for r in cfg_rows:
            if len(r) >= 2 and r[0].strip():
                raw_cfg[r[0].strip()] = r[1].strip()

        start_str = raw_cfg.get("start_datetime", "").strip()
        end_str = raw_cfg.get("end_datetime", "").strip()

        try:
            num_viral = int(raw_cfg.get("daily_viral_count", 1))
        except:
            num_viral = 1

        try:
            num_affiliate = int(raw_cfg.get("daily_affiliate_count", 4))
        except:
            num_affiliate = 4

        target_acc = raw_cfg.get("target_autopilot_account", "-- Semua Akun (All Accounts) --").strip()
        reply_mode = str(raw_cfg.get("reply_mode", "🎲 Acak (1 - 3 Balasan)")).strip()
        length_bias = str(raw_cfg.get("length_bias", "Dominan Sedang (Lebih banyak standar)")).strip()
        ai_style = str(raw_cfg.get("ai_style", "Serahkan ke AI (Smart Adaptive / Acak Tiap Post)")).strip()

        if not start_str or not end_str:
            return True, num_viral, num_affiliate, target_acc, reply_mode, length_bias, ai_style

        now = datetime.now(TZ)
        start_dt = parse_flexible_dt(start_str)
        end_dt = parse_flexible_dt(end_str)

        today_date = now.date()
        if not (start_dt.date() <= today_date <= end_dt.date()):
            logger.info(f"Hari ini ({today_date}) di luar rentang aktif ({start_dt.date()} s/d {end_dt.date()}). Generator dihentikan.")
            return False, num_viral, num_affiliate, target_acc, reply_mode, length_bias, ai_style

        return True, num_viral, num_affiliate, target_acc, reply_mode, length_bias, ai_style
    except Exception as e:
        logger.warning(f"Gagal membaca tab Config: {e}. Menggunakan default.")
        return True, 1, 4, "-- Semua Akun (All Accounts) --", "🎲 Acak (1 - 3 Balasan)", "Dominan Sedang", "Serahkan ke AI"

def get_sheets_client():
    creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

# Call Gemini dengan Dynamic Model Discovery
def call_gemini(prompt: str) -> str:
    key = str(AI_API_KEY).strip().replace("'", "").replace('"', "") if AI_API_KEY else ""
    if not key:
        raise Exception("API Key Gemini (AI_API_KEY) belum disetel di GitHub Secrets!")

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
                    return res["candidates"][0]["content"]["parts"][0]["text"].strip()
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
            res = requests.post(gen_url, json=payload, timeout=30).json()
            if "candidates" in res and res["candidates"]:
                return res["candidates"][0]["content"]["parts"][0]["text"].strip()
            if "error" in res:
                errors.append(f"{ver}/{m_name}: {res['error'].get('message', str(res))}")
        except Exception as e:
            errors.append(f"{ver}/{m_name}: {str(e)}")

    err_msg = " | ".join(errors[:2]) if errors else "Gagal menghubungi Gemini API."
    raise Exception(f"Gemini API error: {err_msg}")

# Rantai balasan dengan narasi variatif dan link di balasan terakhir
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

def main():
    if not SPREADSHEET_ID or not GCP_CREDS_BASE64 or not AI_API_KEY:
        logger.error("Kredensial SPREADSHEET_ID, GCP_CREDS_BASE64, atau AI_API_KEY belum disetel di GitHub Secrets.")
        return

    client = get_sheets_client()
    sh = client.open_by_key(SPREADSHEET_ID)

    active, num_viral, num_affiliate, target_config_acc, reply_mode, length_bias, ai_style = is_active_window(sh)
    if not active:
        return

    accounts_ws = sh.worksheet("Accounts")
    accounts = accounts_ws.get_all_records()
    if not accounts:
        logger.error("Tab Accounts kosong.")
        return

    all_acc_names = [str(a["name"]).strip() for a in accounts if str(a.get("name", "")).strip()]
    if target_config_acc == "-- Semua Akun (All Accounts) --" or not target_config_acc:
        target_accounts_list = all_acc_names
    else:
        target_accounts_list = [target_config_acc]

    products_ws = sh.worksheet("Products")
    prod_rows = products_ws.get_all_records()
    ready_prods = [p for p in prod_rows if str(p.get("status", "")).strip().upper() == "READY"]

    total_needed = num_viral + num_affiliate
    slots = generate_slots(total_needed)
    types = arrange_post_types(num_viral, num_affiliate)

    today_str = datetime.now(TZ).strftime("%Y-%m-%d")
    data_ws = sh.worksheet("data")
    new_entries = []

    for acc_name in target_accounts_list:
        if num_affiliate > 0:
            if len(ready_prods) >= num_affiliate:
                sampled = random.sample(ready_prods, num_affiliate)
            else:
                sampled = random.choices(ready_prods, k=num_affiliate)
        else:
            sampled = []

        aff_counter = 0
        for slot_time, p_type in zip(slots, types):
            chosen_len = pick_length_by_bias(length_bias)
            len_desc = get_length_prompt_desc(chosen_len)
            style_desc = resolve_style_desc(ai_style)

            if p_type == "viral":
                viral_topic = random.choice(VIRAL_TOPICS)
                prompt_v = (
                    f"Tulis 1 postingan Threads bahasa Indonesia gaya santai, relate, dan memancing komentar warganet tentang: '{viral_topic}'. "
                    f"{style_desc}. {len_desc} DILARANG pakai hashtag, tanpa tanda kutip."
                )
                v_text = call_gemini(prompt_v)
                new_entries.append([today_str, slot_time, acc_name, v_text, "", "", "", "PENDING", "", "", ""])
            else:
                prod = sampled[aff_counter]
                aff_counter += 1

                # Mengundi salah satu dari 7 pola hook viral Threads
                chosen_hook = random.choice(VIRAL_HOOK_PATTERNS)

                prompt_aff = (
                    f"Tulis 1 postingan Threads bahasa Indonesia yang sangat natural, tidak kaku, dan memancing engagement untuk barang: '{prod['product_name']}' "
                    f"(Keunggulan utama: {prod.get('highlight', '')}).\n"
                    f"- Format Pembuka: {chosen_hook}.\n"
                    f"- {style_desc}.\n"
                    f"- {len_desc}.\n"
                    f"- ATURAN PENTING: Jangan monoton. Buat kalimat pembuka mengalir alami. DILARANG pakai hashtag dan tanda kutip."
                )
                main_aff = call_gemini(prompt_aff)
                
                # Resolusi rantai balasan (bisa acak 1-3 reply, link selalu di akhir)
                act_rep_count = resolve_reply_count(reply_mode)
                replies_chain = generate_affiliate_replies(
                    prod['product_name'],
                    prod.get('highlight', ''),
                    prod['affiliate_link'],
                    act_rep_count
                )
                joined_replies = "\n---REPLY---\n".join(replies_chain)

                new_rows = [today_str, slot_time, acc_name, main_aff, "", joined_replies, prod["affiliate_link"], "PENDING", "", "", ""]
                new_entries.append(new_rows)

    for entry in new_entries:
        data_ws.append_row(entry)

    logger.info(f"Berhasil menambahkan {len(new_entries)} konten autopilot (Style={ai_style}, Reply={reply_mode}).")

if __name__ == "__main__":
    main()
