import os
import re
import json
import base64
import logging
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
        raw_cfg = {}
        for r in cfg_rows:
            if len(r) >= 2 and r[0].strip():
                raw_cfg[r[0].strip()] = r[1].strip()

        start_str = raw_cfg.get("start_datetime", "").strip()
        end_str = raw_cfg.get("end_datetime", "").strip()

        if not start_str or not end_str:
            return True

        now = datetime.now(TZ)
        start_dt = parse_flexible_dt(start_str)
        end_dt = parse_flexible_dt(end_str)

        if not (start_dt.date() <= now.date() <= end_dt.date()):
            logger.info(f"Hari ini ({now.date()}) di luar rentang aktif ({start_dt.date()} s/d {end_dt.date()}). Worker dihentikan.")
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
    """Memotong teks otomatis dan membersihkan sisa delimiter."""
    text = str(text).strip()
    # Hapus sisa teks pemisah jika masih terselip
    text = re.sub(r"-{2,}\s*REPLY\s*-{2,}", "", text, flags=re.IGNORECASE).strip()
    if len(text) <= limit:
        return text
    trimmed = text[:limit].rsplit(" ", 1)[0]
    return trimmed.strip() + "..."

def post_to_threads(user_id: str, access_token: str, text: str, reply_to: str = None) -> str:
    url_container = f"https://graph.threads.net/v1.0/{user_id}/threads"
    clean_text = safe_trim(text, limit=480)

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
    time.sleep(3)

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
    rows = data_ws.get_all_records()

    now_dt = datetime.now(TZ)
    now_time_str = now_dt.strftime("%H:%M")
    today_str = now_dt.strftime("%Y-%m-%d")

    for idx, row in enumerate(rows, start=2):
        status = str(row.get("status", "")).strip().upper()
        s_date = str(row.get("schedule_date", "")).strip()
        s_time = str(row.get("schedule_time", "")).strip()

        if len(s_time) == 4 and s_time[1] == ":":
            s_time = "0" + s_time

        if status == "PENDING" and s_date <= today_str and s_time <= now_time_str:
            acc_name = str(row.get("target_accounts", "")).strip()
            acc = accounts.get(acc_name)

            if not acc:
                logger.warning(f"Akun '{acc_name}' tidak ditemukan di tab Accounts.")
                continue

            user_id = str(acc["user_id"]).strip()
            token = str(acc["access_token"]).strip()

            try:
                # 1. Posting Konten Utama
                main_id = post_to_threads(user_id, token, str(row["main_text"]))
                logger.info(f"Postingan utama terbit: {main_id}")

                # 2. Posting Rantai Balasan Bertingkat (Utas / Thread)
                reply_raw = str(row.get("reply_text", "")).strip()
                if reply_raw:
                    # Pecah teks menggunakan regex kebal format
                    reply_parts = [p.strip() for p in re.split(r"-{2,}\s*REPLY\s*-{2,}", reply_raw, flags=re.IGNORECASE) if p.strip()]
                    parent_id = main_id

                    for r_idx, part in enumerate(reply_parts, start=1):
                        time.sleep(5)  # Jeda lima detik agar server Meta selesai mengindeks balasan sebelumnya
                        try:
                            parent_id = post_to_threads(user_id, token, part, reply_to=parent_id)
                        except Exception:
                            # Fallback: jika sambungan bertingkat gagal, balas langsung ke postingan utama
                            parent_id = post_to_threads(user_id, token, part, reply_to=main_id)
                        logger.info(f"Balasan {r_idx}/{len(reply_parts)} terbit: {parent_id}")

                # Tandai selesai di Google Sheets
                data_ws.update_cell(idx, 8, "POSTED")
                data_ws.update_cell(idx, 9, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
                data_ws.update_cell(idx, 10, main_id)
                data_ws.update_cell(idx, 11, "")
                logger.info(f"Baris {idx} sukses diposting.")
                break

            except Exception as e:
                logger.error(f"Gagal posting baris {idx}: {e}")
                data_ws.update_cell(idx, 8, "FAILED")
                data_ws.update_cell(idx, 11, str(e))
                break

if __name__ == "__main__":
    main()
