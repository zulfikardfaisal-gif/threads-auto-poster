import os
import re
import json
import base64
import logging
import random
import time
from datetime import datetime
import pytz
import requests
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ThreadsWorker")
TZ = pytz.timezone("Asia/Jakarta")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GCP_CREDS_BASE64 = os.environ.get("GCP_CREDS_BASE64")

def parse_flexible_dt(s: str) -> datetime:
    cleaned = str(s).strip().replace("'", "")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})$", cleaned)
    if not m:
        raise ValueError(f"Format datetime tidak sesuai: {s}")
    year, month, day, hour, minute = map(int, m.groups())
    return TZ.localize(datetime(year, month, day, hour, minute))

def is_active_window(sh) -> bool:
    try:
        cfg_ws = sh.worksheet("Config")
        cfg_rows = cfg_ws.get_all_values()
        raw_cfg = {r[0].strip(): r[1].strip() for r in cfg_rows if len(r) >= 2 and r[0].strip()}

        start_str = raw_cfg.get("start_datetime", "").strip()
        end_str = raw_cfg.get("end_datetime", "").strip()

        if not start_str or not end_str:
            return True

        now = datetime.now(TZ)
        start_dt = parse_flexible_dt(start_str)
        end_dt = parse_flexible_dt(end_str)

        if not (start_dt.date() <= now.date() <= end_dt.date()):
            return False

        return True
    except Exception as e:
        logger.warning(f"Gagal membaca tab Config: {e}. Worker tetap lanjut.")
        return True

def get_sheets_client():
    creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def safe_trim(text: str, limit: int = 480) -> str:
    text = str(text).strip()
    text = re.sub(r"-{2,}\s*REPLY\s*-{2,}", "", text, flags=re.IGNORECASE).strip()
    if len(text) <= limit:
        return text
    trimmed = text[:limit].rsplit(" ", 1)[0]
    return trimmed.strip() + "..."

def randomize_cloudinary_url(url: str) -> str:
    url = str(url).strip()
    if "res.cloudinary.com" not in url or "/upload/" not in url:
        return url

    # PENTING: JANGAN ubah URL video agar format .mp4 tetap bersih dan tidak error Media Not Found
    is_vid = any(url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in url
    if is_vid:
        return url

    # Randomisasi mikro aman hanya untuk gambar/foto
    sat = random.choice([-4, -2, 2, 4])
    bri = random.choice([-2, -1, 1, 2])
    transform_str = f"e_saturation:{sat},e_brightness:{bri}"

    return url.replace("/upload/", f"/upload/{transform_str}/", 1)

def wait_for_video_processing(creation_id: str, access_token: str, max_retries: int = 25) -> bool:
    url = f"https://graph.threads.net/v1.0/{creation_id}?fields=status,error_message&access_token={access_token}"
    for attempt in range(max_retries):
        time.sleep(6)
        try:
            res = requests.get(url, timeout=15).json()
            status = res.get("status")
            logger.info(f"Cek status video di Meta ({attempt + 1}/{max_retries}): {status}")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                err_msg = res.get('error_message', 'Unknown Meta Error')
                raise Exception(f"Meta menolak video: {err_msg}")
        except Exception as e:
            if "Meta menolak video" in str(e):
                raise
            logger.warning(f"Gagal cek status video: {e}")
    raise TimeoutError("Waktu pemrosesan video di server Meta habis (Timeout lebih dari 2.5 menit).")

def post_to_threads(user_id: str, access_token: str, text: str, media_url: str = None, reply_to: str = None) -> str:
    url_container = f"https://graph.threads.net/v1.0/{user_id}/threads"
    clean_text = safe_trim(text, limit=480)

    media_list = []
    if media_url and not reply_to:
        parts = [p.strip() for p in str(media_url).split(",") if p.strip()]
        for p in parts:
            media_list.append(randomize_cloudinary_url(p))

    # 1. CAROUSEL (2 ATAU LEBIH MEDIA)
    if len(media_list) > 1:
        logger.info(f"Mode: CAROUSEL ({len(media_list)} media)")
        child_ids = []
        for m_idx, m_url in enumerate(media_list, start=1):
            is_vid = any(m_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in m_url
            c_payload = {
                "is_carousel_item": "true",
                "access_token": access_token
            }
            if is_vid:
                c_payload["media_type"] = "VIDEO"
                c_payload["video_url"] = m_url
            else:
                c_payload["media_type"] = "IMAGE"
                c_payload["image_url"] = m_url

            logger.info(f"Membuat item carousel #{m_idx} ({'VIDEO' if is_vid else 'IMAGE'}) dengan URL: {m_url}")
            c_res = requests.post(url_container, data=c_payload, timeout=30).json()
            if "id" not in c_res:
                raise Exception(f"Gagal buat item carousel #{m_idx}: {c_res}")
            
            c_id = c_res["id"]
            if is_vid:
                wait_for_video_processing(c_id, access_token)
            else:
                time.sleep(2)
            child_ids.append(c_id)

        parent_payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "text": clean_text,
            "access_token": access_token
        }
        if reply_to:
            parent_payload["reply_to_id"] = reply_to
        res = requests.post(url_container, data=parent_payload, timeout=30).json()

    # 2. SINGLE MEDIA (1 VIDEO / 1 GAMBAR)
    elif len(media_list) == 1:
        m_url = media_list[0]
        is_vid = any(m_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in m_url
        logger.info(f"Mode: SINGLE {'VIDEO' if is_vid else 'IMAGE'} dengan URL: {m_url}")
        
        payload = {
            "text": clean_text,
            "access_token": access_token
        }
        if is_vid:
            payload["media_type"] = "VIDEO"
            payload["video_url"] = m_url
        else:
            payload["media_type"] = "IMAGE"
            payload["image_url"] = m_url

        if reply_to:
            payload["reply_to_id"] = reply_to

        res = requests.post(url_container, data=payload, timeout=30).json()
        if "id" in res and is_vid:
            wait_for_video_processing(res["id"], access_token)

    # 3. TEKS BIASA
    else:
        logger.info("Mode: TEXT ONLY")
        payload = {
            "media_type": "TEXT",
            "text": clean_text,
            "access_token": access_token
        }
        if reply_to:
            payload["reply_to_id"] = reply_to
        res = requests.post(url_container, data=payload, timeout=30).json()

    if "id" not in res:
        raise Exception(f"Gagal membuat container Threads: {res}")

    creation_id = res["id"]
    time.sleep(4)

    # PUBLISH POST
    url_publish = f"https://graph.threads.net/v1.0/{user_id}/threads_publish"
    pub_res = requests.post(
        url_publish,
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=30
    ).json()

    if "id" not in pub_res:
        raise Exception(f"Gagal publish Threads: {pub_res}")

    return pub_res["id"]

def main():
    if not SPREADSHEET_ID or not GCP_CREDS_BASE64:
        logger.error("Kredensial SPREADSHEET_ID atau GCP_CREDS_BASE64 belum disetel.")
        return

    client = get_sheets_client()
    sh = client.open_by_key(SPREADSHEET_ID)

    if not is_active_window(sh):
        return

    accounts_ws = sh.worksheet("Accounts")
    accounts = {str(r["name"]).strip(): r for r in accounts_ws.get_all_records()}

    data_ws = sh.worksheet("data")
    all_values = data_ws.get_all_values()

    if len(all_values) <= 1:
        logger.info("Tab data kosong.")
        return

    now_dt = datetime.now(TZ)
    now_time_str = now_dt.strftime("%H:%M")
    today_str = now_dt.strftime("%Y-%m-%d")

    for row_idx in range(1, len(all_values)):
        row = all_values[row_idx]
        sheet_row_num = row_idx + 1

        s_date = row[0].strip() if len(row) > 0 else ""
        s_time = row[1].strip() if len(row) > 1 else ""
        acc_name = row[2].strip() if len(row) > 2 else ""
        main_text = row[3].strip() if len(row) > 3 else ""
        media_url = row[4].strip() if len(row) > 4 else ""
        reply_raw = row[5].strip() if len(row) > 5 else ""
        status = row[7].strip().upper() if len(row) > 7 else ""

        if len(s_time) == 4 and s_time[1] == ":":
            s_time = "0" + s_time

        if status == "PENDING" and s_date <= today_str and s_time <= now_time_str:
            acc = accounts.get(acc_name)
            if not acc:
                logger.warning(f"Akun '{acc_name}' tidak ditemukan di tab Accounts.")
                continue

            user_id = str(acc["user_id"]).strip()
            token = str(acc["access_token"]).strip()

            logger.info(f"=== MEMPROSES BARIS #{sheet_row_num} ===")
            logger.info(f"Target Akun: {acc_name}")
            logger.info(f"Media URL Terbaca: '{media_url}'")

            try:
                # 1. Posting Konten Utama
                main_id = post_to_threads(user_id, token, main_text, media_url=media_url)
                logger.info(f"Postingan utama terbit dengan ID: {main_id}")

                # 2. Posting Rantai Balasan
                if reply_raw:
                    reply_parts = [p.strip() for p in re.split(r"-{2,}\s*REPLY\s*-{2,}", reply_raw, flags=re.IGNORECASE) if p.strip()]
                    parent_id = main_id

                    for r_idx, part in enumerate(reply_parts, start=1):
                        time.sleep(5)
                        try:
                            parent_id = post_to_threads(user_id, token, part, reply_to=parent_id)
                        except Exception:
                            parent_id = post_to_threads(user_id, token, part, reply_to=main_id)
                        logger.info(f"Balasan #{r_idx}/{len(reply_parts)} terbit dengan ID: {parent_id}")

                # Update Status Sukses
                data_ws.update_cell(sheet_row_num, 8, "POSTED")
                data_ws.update_cell(sheet_row_num, 9, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
                data_ws.update_cell(sheet_row_num, 10, main_id)
                data_ws.update_cell(sheet_row_num, 11, "")
                logger.info(f"Baris #{sheet_row_num} sukses!")
                break

            except Exception as e:
                err_text = str(e)
                logger.error(f"Gagal posting baris #{sheet_row_num}: {err_text}")
                data_ws.update_cell(sheet_row_num, 8, "FAILED")
                data_ws.update_cell(sheet_row_num, 11, err_text)
                break

if __name__ == "__main__":
    main()
