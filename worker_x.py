import os
import json
import time
import base64
import logging
from datetime import datetime
import pytz
import tweepy
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Worker_X")

TZ_JAKARTA = pytz.timezone("Asia/Jakarta")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def clean_ascii(text):
    return "".join(c for c in str(text) if 32 <= ord(c) <= 126).strip() if text else ""

def get_creds():
    b64_str = os.getenv("GCP_CREDS_BASE64", "").strip()
    if b64_str:
        try:
            return json.loads(base64.b64decode(b64_str).decode("utf-8"))
        except Exception as e:
            logger.error(f"Gagal mendekode creds: {e}")
            return None
    if os.path.exists("credentials.json"):
        with open("credentials.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def post_tweet_thread(client, main_text, replies):
    res = client.create_tweet(text=main_text[:280])
    main_id = res.data["id"]
    tweet_ids = [str(main_id)]
    curr_id = main_id

    for rep in replies:
        if str(rep).strip():
            time.sleep(2)
            rep_res = client.create_tweet(text=rep.strip()[:280], in_reply_to_tweet_id=curr_id)
            curr_id = rep_res.data["id"]
            tweet_ids.append(str(curr_id))

    return tweet_ids

def main():
    creds_dict = get_creds()
    sheet_id = clean_ascii(os.getenv("SPREADSHEET_ID", ""))

    if not creds_dict or not sheet_id:
        logger.error("Kredensial atau SPREADSHEET_ID tidak ditemukan.")
        return

    try:
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
    except Exception as e:
        logger.error(f"Gagal koneksi ke Spreadsheet: {e}")
        return

    # 1. Baca Akun dari tab Accounts_X
    try:
        ws_acc = sh.worksheet("Accounts_X")
        acc_rows = ws_acc.get_all_values()[1:]
        accounts = []
        for r in acc_rows:
            if len(r) >= 5 and str(r[0]).strip() and str(r[1]).strip():
                accounts.append({
                    "name": str(r[0]).strip(),
                    "ck": str(r[1]).strip(),
                    "cs": str(r[2]).strip(),
                    "at": str(r[3]).strip(),
                    "ats": str(r[4]).strip()
                })
    except Exception as e:
        logger.error(f"Gagal membaca tab Accounts_X: {e}")
        return

    if not accounts:
        logger.warning("Tidak ada akun terdaftar di tab Accounts_X.")
        return

    # 2. Baca Antrean dari tab data_x
    try:
        ws_data = sh.worksheet("data_x")
    except Exception as e:
        logger.error(f"Tab data_x tidak ditemukan: {e}")
        return

    all_v = ws_data.get_all_values()
    if len(all_v) <= 1:
        return

    now = datetime.now(TZ_JAKARTA)

    for idx, r in enumerate(all_v[1:], start=2):
        if len(r) < 8:
            continue
        d_str = str(r[0]).strip()
        t_str = str(r[1]).strip()
        tgt = str(r[2]).strip()
        main_txt = str(r[3]).strip()
        raw_rep = str(r[5]).strip()
        status = str(r[7]).strip()

        if status.upper() != "PENDING":
            continue

        try:
            sched_dt = TZ_JAKARTA.localize(datetime.strptime(f"{d_str} {t_str}", "%Y-%m-%d %H:%M"))
        except Exception:
            continue

        if now >= sched_dt:
            logger.info(f"Mengeksekusi tweet baris #{idx} ({tgt})...")
            replies = [x.strip() for x in raw_rep.split("|||") if x.strip()]
            selected_accs = accounts if tgt.upper() == "ALL" or not tgt else [a for a in accounts if a["name"] in [t.strip() for t in tgt.split(",")]]

            succ, fail = [], []
            for acc in selected_accs:
                try:
                    client = tweepy.Client(
                        consumer_key=acc["ck"],
                        consumer_secret=acc["cs"],
                        access_token=acc["at"],
                        access_token_secret=acc["ats"]
                    )
                    t_ids = post_tweet_thread(client, main_txt, replies)
                    succ.append(f"{acc['name']}: {','.join(t_ids)}")
                    logger.info(f"✅ Berhasil memposting tweet ke {acc['name']}")
                except Exception as ex:
                    fail.append(f"{acc['name']}: {ex}")
                    logger.error(f"❌ Gagal memposting tweet ke {acc['name']}: {ex}")

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
