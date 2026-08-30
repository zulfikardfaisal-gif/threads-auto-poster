import os
import sys
import json
import time
import re
import logging
from datetime import datetime, timedelta
import pytz
import pandas as pd
import streamlit as st
import requests
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv, set_key
from apscheduler.schedulers.background import BackgroundScheduler

# ==========================================
# 1. KONFIGURASI LOGGING & TIMEZONE
# ==========================================
LOG_FILE = "app_activity.log"
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("ThreadsHub")

load_dotenv(ENV_PATH, override=True)
TZ_JAKARTA = pytz.timezone("Asia/Jakarta")

def get_config_val(key: str, default: str = "") -> str:
    if key in st.secrets:
        return str(st.secrets[key]).strip()
    return os.getenv(key, default).strip()

def clean_private_key(raw_key: str) -> str:
    raw_key = str(raw_key).replace("\\n", "\n").replace("\r", "").strip()
    lines = [l.strip() for l in raw_key.split("\n") if l.strip()]
    body = "".join([l for l in lines if not l.startswith("-----")])
    body = re.sub(r"[^A-Za-z0-9+/=]", "", body)
    rem = len(body) % 4
    if rem > 0:
        body += "=" * (4 - rem)
    chunks = [body[i:i+64] for i in range(0, len(body), 64)]
    return "-----BEGIN PRIVATE KEY-----\n" + "\n".join(chunks) + "\n-----END PRIVATE KEY-----\n"

def get_gcp_credentials_dict():
    if "GCP_SERVICE_ACCOUNT" in st.secrets:
        raw = st.secrets["GCP_SERVICE_ACCOUNT"]
        cd = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if "private_key" in cd:
            cd["private_key"] = clean_private_key(cd["private_key"])
        return cd
    elif "gcp_service_account" in st.secrets:
        cd = dict(st.secrets["gcp_service_account"])
        if "private_key" in cd:
            cd["private_key"] = clean_private_key(cd["private_key"])
        return cd
    elif os.path.exists("credentials.json"):
        with open("credentials.json", "r") as f:
            cd = json.load(f)
            if "private_key" in cd:
                cd["private_key"] = clean_private_key(cd["private_key"])
            return cd
    return None

def extract_and_parse_json(raw_str: str, default_count: int = 3):
    """Pembersih JSON aman tanpa ketergantungan regex backtick rapuh"""
    if not raw_str or not str(raw_str).strip():
        return [{"angle": f"Variasi #{i+1}", "main_text": "Rekomendasi produk terbaik untukmu!", "replies": []} for i in range(default_count)]
    
    text = re.sub(r"<think>[\s\S]*?</think>", "", str(raw_str)).strip()
    text = text.replace("
