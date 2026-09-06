import os, json, base64, logging
from datetime import datetime
import pytz, requests, gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ThreadsWorker")
TZ = pytz.timezone("Asia/Jakarta")

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
GCP_CREDS_BASE64 = os.environ.get("GCP_CREDS_BASE64")

def get_sheets_client():
    creds_json = base64.b64decode(GCP_CREDS_BASE64).decode("utf-8")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def is_active_window(sh):
    try:
        cfg = dict(sh.worksheet("Config").get_all_values())
        start_str, end_str = cfg.get("start_datetime", "").strip(), cfg.get("end_datetime", "").strip()
        if not start_str or not end_str:
            return True
        now = datetime.now(TZ)
        start_dt = TZ.localize(datetime.strptime(start_str, "%Y-%m-%d %H:%M"))
        end_dt = TZ.localize(datetime.strptime(end_str, "%Y-%m-%d %H:%M"))
        if not (start_dt <= now <= end_dt):
            logger.info(f"Di luar jadwal aktif ({start_str} s/d {end_str}). Worker berhenti.")
            return False
        return True
    except Exception as e:
        logger.warning(f"Lewati cek Config: {e}")
        return True

def post_threads(user_id, token, text, reply_to=None):
    url = f"https://graph.threads.net/v1.0/{user_id}/threads"
    payload = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to:
        payload["reply_to_id"] = reply_to
    res = requests.post(url, data=payload).json()
    if "id" not in res:
        raise Exception(f"Gagal buat wadah: {res}")
    pub_res = requests.post(
        f"https://graph.threads.net/v1.0/{user_id}/threads_publish",
        data={"creation_id": res["id"], "access_token": token}
    ).json()
    if "id" not in pub_res:
        raise Exception(f"Gagal publish: {pub_res}")
    return pub_res["id"]

def main():
    if not SPREADSHEET_ID or not GCP_CREDS_BASE64:
        return
    client = get_sheets_client()
    sh = client.open_by_key(SPREADSHEET_ID)
    
    if not is_active_window(sh):
        return

    accounts = {r["name"]: r for r in sh.worksheet("Accounts").get_all_records()}
    data_ws = sh.worksheet("data")
    rows = data_ws.get_all_records()
    now_str = datetime.now(TZ).strftime("%H:%M")
    today_str = datetime.now(TZ).strftime("%Y-%m-%d")

    for idx, row in enumerate(rows, start=2):
        if row.get("status") == "PENDING" and str(row.get("schedule_date")) <= today_str and str(row.get("schedule_time")) <= now_str:
            acc = accounts.get(row.get("target_accounts"))
            if not acc:
                continue
            try:
                main_id = post_threads(acc["user_id"], acc["access_token"], row["main_text"])
                if row.get("reply_text"):
                    post_threads(acc["user_id"], acc["access_token"], row["reply_text"], reply_to=main_id)
                data_ws.update_cell(idx, 8, "POSTED")
                data_ws.update_cell(idx, 9, datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
                data_ws.update_cell(idx, 10, main_id)
                logger.info(f"Berhasil publish ke Threads: {main_id}")
                break
            except Exception as e:
                data_ws.update_cell(idx, 8, "FAILED")
                data_ws.update_cell(idx, 11, str(e))
                break

if __name__ == "__main__":
    main()
