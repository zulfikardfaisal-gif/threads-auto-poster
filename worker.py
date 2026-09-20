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

        start_str = raw_cfg.get("start_datetime", "").strip().replace("'", "")
        end_str = raw_cfg.get("end_datetime", "").strip().replace("'", "")

        if not start_str or not end_str:
            return True

        now = datetime.now(TZ)
        start_dt = parse_flexible_dt(start_str)
        end_dt = parse_flexible_dt(end_str)

        if not (start_dt.date() <= now.date() <= end_dt.date()):
            logger.info(f"Di luar rentang tanggal aktif Config ({start_dt.date()} s/d {end_dt.date()}).")
            return False

        return True
    except Exception as e:
        logger.warning(f"Catatan Config dilewati: {e}. Worker tetap jalan.")
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

def clean_media_url(url: str) -> str:
    url = str(url).strip()
    if not url:
        return ""
    if "res.cloudinary.com" not in url or "/upload/" not in url:
        return url

    # PENTING: File video tidak boleh disentuh filter agar tidak rusak di Meta API
    is_vid = any(url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in url
    if is_vid:
        return url

    # Acak mikro hanya untuk gambar/foto
    sat = random.choice([-4, -2, 2, 4])
    bri = random.choice([-2, -1, 1, 2])
    transform_str = f"e_saturation:{sat},e_brightness:{bri}"
    return url.replace("/upload/", f"/upload/{transform_str}/", 1)

def wait_for_container_ready(creation_id: str, access_token: str, max_retries: int = 35) -> bool:
    """Menunggu kontainer media selesai diproses server Meta sampai berstatus FINISHED."""
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
                err_msg = res.get("error_message", "Meta menolak format file media ini.")
                raise Exception(f"Meta Error: {err_msg}")
            if status == "EXPIRED":
                raise Exception("Meta Container Expired.")
        except Exception as e:
            if "Meta Error" in str(e) or "Meta Container" in str(e):
                raise
            logger.warning(f"Menunggu verifikasi kontainer: {e}")
    raise TimeoutError("Waktu pemrosesan di server Meta habis (lebih dari 3.5 menit).")

def post_to_threads(user_id: str, access_token: str, text: str, media_url: str = None, reply_to: str = None) -> str:
    url_container = f"https://graph.threads.net/v1.0/{user_id}/threads"
    clean_text = safe_trim(text, limit=480)

    media_list = []
    if media_url:
        parts = [p.strip() for p in str(media_url).split(",") if p.strip()]
        for p in parts:
            cleaned = clean_media_url(p)
            if cleaned:
                media_list.append(cleaned)

    # 1. MODE KOMENTAR (REPLY TO TARGET) DENGAN MEDIA (SINGLE VIDEO / IMAGE)
    # Meta Threads API tidak mendukung Carousel di dalam komentar, jadi pakai Single Media
    if reply_to and media_list:
        m_url = media_list[0]
        is_vid = any(m_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in m_url
        logger.info(f"Komentar Nimbrung dengan {'VIDEO' if is_vid else 'IMAGE'}: {m_url}")

        payload = {
            "text": clean_text,
            "reply_to_id": reply_to,
            "access_token": access_token
        }
        if is_vid:
            payload["media_type"] = "VIDEO"
            payload["video_url"] = m_url
        else:
            payload["media_type"] = "IMAGE"
            payload["image_url"] = m_url

        res = requests.post(url_container, data=payload, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal membuat kontainer komentar media: {res}")

        creation_id = res["id"]
        if is_vid:
            logger.info(f"Menunggu video komentar ({creation_id}) siap...")
            wait_for_container_ready(creation_id, access_token)
        else:
            time.sleep(3)

    # 2. CAROUSEL MANDIRI (2 ATAU LEBIH MEDIA)
    elif not reply_to and len(media_list) > 1:
        logger.info(f"Tipe: CAROUSEL ({len(media_list)} slide)")
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

            logger.info(f"Membuat slide #{m_idx} ({'VIDEO' if is_vid else 'IMAGE'})...")
            c_res = requests.post(url_container, data=c_payload, timeout=30).json()
            if "id" not in c_res:
                raise Exception(f"Gagal buat item #{m_idx}: {c_res}")

            c_id = c_res["id"]
            if is_vid:
                wait_for_container_ready(c_id, access_token)
            else:
                time.sleep(2)
            child_ids.append(c_id)

        parent_payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "text": clean_text,
            "access_token": access_token
        }
        res = requests.post(url_container, data=parent_payload, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal membuat kontainer Carousel utama: {res}")

        creation_id = res["id"]
        logger.info(f"Menunggu kontainer Carousel utama ({creation_id}) siap...")
        wait_for_container_ready(creation_id, access_token)

    # 3. SINGLE MEDIA MANDIRI (1 VIDEO ATAU 1 GAMBAR)
    elif not reply_to and len(media_list) == 1:
        m_url = media_list[0]
        is_vid = any(m_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".m4v"]) or "/video/upload/" in m_url
        logger.info(f"Tipe: SINGLE {'VIDEO' if is_vid else 'IMAGE'} -> {m_url}")

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

        res = requests.post(url_container, data=payload, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal membuat kontainer media: {res}")

        creation_id = res["id"]
        if is_vid:
            logger.info(f"Menunggu video ({creation_id}) selesai diproses Meta...")
            wait_for_container_ready(creation_id, access_token)
        else:
            time.sleep(3)

    # 4. TEKS SAJA (MANDIRI ATAU REPLY TEKS)
    else:
        logger.info(f"Tipe: TEXT ONLY {'(REPLY TO ' + str(reply_to) + ')' if reply_to else ''}")
        payload = {
            "media_type": "TEXT",
            "text": clean_text,
            "access_token": access_token
        }
        if reply_to:
            payload["reply_to_id"] = reply_to

        res = requests.post(url_container, data=payload, timeout=30).json()
        if "id" not in res:
            raise Exception(f"Gagal membuat kontainer teks: {res}")

        creation_id = res["id"]
        time.sleep(3)

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
    accounts = {str(r["name"]).strip(): r for r in accounts_ws.get_all_records() if str(r.get("name", "")).strip()}

    data_ws = sh.worksheet("data")
    all_values = data_ws.get_all_values()

    if len(all_values) <= 1:
        logger.info("Tab data kosong atau hanya baris header.")
        return

    now_dt = datetime.now(TZ)
    now_time_str = now_dt.strftime("%H:%M")
    today_str = now_dt.strftime("%Y-%m-%d")

    logger.info(f"Waktu Server Sekarang: {today_str} {now_time_str} WIB")

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
        
        # Kolom L (Kolom ke-12): Target Reply ID (Thread Hijacking)
        target_reply_id = row[11].strip().replace("'", "") if len(row) > 11 else ""

        if len(s_time) == 4 and s_time[1] == ":":
            s_time = "0" + s_time

        if status == "PENDING":
            logger.info(f"Mengecek Baris #{sheet_row_num}: Tanggal='{s_date}' Jam='{s_time}' Akun='{acc_name}' HijackID='{target_reply_id}'")

            if s_date > today_str:
                logger.info(f"Baris #{sheet_row_num} dilewati: Tanggal ({s_date}) belum tiba.")
                continue
            if s_date == today_str and s_time > now_time_str:
                logger.info(f"Baris #{sheet_row_num} dilewati: Jam ({s_time}) belum tiba.")
                continue

            acc = accounts.get(acc_name)
            if not acc:
                logger.warning(f"Akun '{acc_name}' tidak ditemukan di tab Accounts!")
                continue

            user_id = str(acc["user_id"]).strip()
            token = str(acc["access_token"]).strip()

            logger.info(f"=== EKSEKUSI POSTING BARIS #{sheet_row_num} ===")
            logger.info(f"Akun Target: {acc_name}")

            try:
                # 1. Posting Komentar Utama (Dengan Video/Foto jika ada)
                if target_reply_id:
                    logger.info(f"Mode: THREAD HIJACKING (Membalas Post ID: {target_reply_id})")
                    main_id = post_to_threads(user_id, token, main_text, media_url=media_url, reply_to=target_reply_id)
                    logger.info(f"Komentar balasan terbit (ID: {main_id})")
                else:
                    logger.info("Mode: REGULAR POST (Postingan mandiri)")
                    main_id = post_to_threads(user_id, token, main_text, media_url=media_url)
                    logger.info(f"Postingan utama terbit (ID: {main_id})")

                # 2. Posting Rantai Balasan (Spill Link Shopee di bawah komentar sendiri)
                if reply_raw:
                    reply_parts = [p.strip() for p in re.split(r"-{2,}\s*REPLY\s*-{2,}", reply_raw, flags=re.IGNORECASE) if p.strip()]
                    parent_id = main_id

                    for r_idx, part in enumerate(reply_parts, start=1):
                        time.sleep(4)
                        try:
                            parent_id = post_to_threads(user_id, token, part, reply_to=parent_id)
                        except Exception:
                            parent_id = post_to_threads(user_id, token, part, reply_to=main_id)
                        logger.info(f"Balasan link #{r_idx}/{len(reply_parts)} terbit (ID: {parent_id})")

                # Update Status Sukses ke Spreadsheet
                data_ws.update_cell(sheet_row_num, 8, "POSTED")
                data_ws.update_cell(sheet_row_num, 9, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
                data_ws.update_cell(sheet_row_num, 10, main_id)
                data_ws.update_cell(sheet_row_num, 11, "")
                logger.info(f"Baris #{sheet_row_num} selesai sukses!")
                break

            except Exception as e:
                err_text = str(e)
                logger.error(f"Gagal posting baris #{sheet_row_num}: {err_text}")
                data_ws.update_cell(sheet_row_num, 8, "FAILED")
                data_ws.update_cell(sheet_row_num, 11, err_text)
                break

if __name__ == "__main__":
    main()
