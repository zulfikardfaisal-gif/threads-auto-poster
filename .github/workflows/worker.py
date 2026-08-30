import os
import json
import time
import base64
import logging
from datetime import datetime
import pytz
import requests
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Worker")

TZ_JAKARTA = pytz.timezone("Asia/Jakarta")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
HEADERS = [
    "schedule_date", "schedule_time", "target_accounts", "main_text", "main_image_url",
    "reply_text", "affiliate_link", "status", "posted_at", "threads_post_id", "error_log"
]

def clean_ascii(text):
    return "".join(c for c in str(text) if 32 <= ord(c) <= 126).strip() if text else ""

def get_creds():
    b64_str = os.getenv("GCP_CREDS_BASE64", "").strip()
    if b64_str:
        return json.loads(base64.b64decode(b64_str).decode("utf-8"))
    if os.path.exists("credentials.json"):
        with open("credentials.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return None

class ThreadsClient:
    def __init__(self, uid, token):
        self.uid = clean_ascii(uid)
        self.token = clean_ascii(token)
        self.base = "https://graph.threads.net/v1.0"

    def create(self, text="", img_url=None, reply_to=None):
        url = f"{self.base}/{self.uid}/threads"
        data = {"access_token": self.token}
        if text:
            data["text"] = text[:500]
        if img_url and str(img_url).strip().startswith("http"):
            data["media_type"] = "IMAGE"
            data["image_url"] = str(img_url).strip()
        else:
            data["media_type"] = "TEXT"
        if reply_to:
            data["reply_to_id"] = clean_ascii(reply_to)
        
        r = requests.post(url, data=data, timeout=20).json()
        if "id" in r:
            return r["id"]
        raise Exception(r.get("error", {}).get("message", str(r)))

    def publish(self, cid):
        time.sleep(3)
        url = f"{self.base}/{self.uid}/threads_publish"
        r = requests.post(url, data={"creation_id": cid, "access_token": self.token}, timeout=20).json()
        if "id" in r:
            return r["id"]
        raise Exception(r.get("error", {}).get("message", str(r)))

    def post_thread(self, main_txt, img_url, replies):
        p_ids = []
        cid = self.create(main_txt, img_url)
        pid = self.publish(cid)
        p_ids.append(pid)
        curr = pid
        for rep in replies:
            if str(rep).strip():
                time.sleep(3)
                rcid = self.create(rep.strip(), reply_to=curr)
                rpid = self.publish(rcid)
                p_ids.append(rpid)
                curr = rpid
        return p_ids

def main():
    creds_dict = get_creds()
    sheet_id = os.getenv("SPREADSHEET_ID", "").strip()
    sheet_name = os.getenv("SHEET_NAME", "data").strip()
    
    if not creds_dict or not sheet_id:
        logger.error("Kredensial atau Spreadsheet ID kosong.")
        return

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    # 1. Ambil daftar akun
    try:
        ws_acc = sh.worksheet("Accounts")
        acc_rows = ws_acc.get_all_values()[1:]
        accounts = [{"name": r[0], "user_id": r[1], "access_token": r[2]} for r in acc_rows if len(r) >= 3 and r[1]]
    except Exception as e:
        logger.error(f"Gagal memuat Accounts: {e}")
        return

    if not accounts:
        logger.warning("Tidak ada akun Threads terdaftar di Sheets.")
        return

    # 2. Ambil daftar antrean postingan
    ws_data = sh.worksheet(sheet_name)
    all_v = ws_data.get_all_values()
    if len(all_v) <= 1:
        logger.info("Antrean kosong.")
        return

    now = datetime.now(TZ_JAKARTA)
    logger.info(f"Worker aktif pada: {now.strftime('%Y-%m-%d %H:%M:%S WIB')}")

    for idx, r in enumerate(all_v[1:], start=2):
        if len(r) < 8:
            continue
        d_str, t_str, tgt, main_txt, img_url, raw_rep, link, status = r[:8]
        
        if str(status).strip().upper() != "PENDING":
            continue

        try:
            sched_dt = TZ_JAKARTA.localize(datetime.strptime(f"{d_str.strip()} {t_str.strip()}", "%Y-%m-%d %H:%M"))
        except Exception:
            continue

        if now >= sched_dt:
            logger.info(f"⏳ Mengeksekusi baris #{idx}...")
            replies = [x.strip() for x in raw_rep.split("|||") if x.strip()]
            
            selected_accs = accounts if tgt.upper() == "ALL" or not tgt else [a for a in accounts if a["name"] in [t.strip() for t in tgt.split(",")]]
            
            succ, fail = [], []
            for acc in selected_accs:
                try:
                    th = ThreadsClient(acc["user_id"], acc["access_token"])
                    res_ids = th.post_thread(main_txt, img_url, replies)
                    succ.append(f"{acc['name']}: {','.join(res_ids)}")
                except Exception as ex:
                    fail.append(f"{acc['name']}: {ex}")

            if succ:
                new_st = "POSTED" if not fail else "PARTIAL"
                ws_data.update_cell(idx, 8, new_st)
                ws_data.update_cell(idx, 9, now.strftime("%Y-%m-%d %H:%M:%S"))
                ws_data.update_cell(idx, 10, " | ".join(succ))
                ws_data.update_cell(idx, 11, " | ".join(fail) if fail else "")
            else:
                ws_data.update_cell(idx, 8, "FAILED")
                ws_data.update_cell(idx, 11, " | ".join(fail))

if __name__ == "__main__":
    main()
