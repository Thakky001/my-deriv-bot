import os
from dotenv import load_dotenv

load_dotenv()

DERIV_APP_ID = os.getenv("DERIV_APP_ID", "1089") # ใส่ App ID ของคุณ
DERIV_TOKEN = os.getenv("DERIV_TOKEN", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = "R_75"
RISK_PERCENT = 0.02
DAILY_PROFIT_TARGET = 0.05
MAX_LOSS_STREAK = 3