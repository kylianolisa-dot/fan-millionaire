"""Environment-driven configuration. Secrets are NEVER hard-coded: set them in the environment."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("FM_DB", str(BASE / "data" / "fanmillionaire.db"))

# Paystack. With no secret key the app runs in MOCK mode (fake checkout, instant fake payouts).
PAYSTACK_SECRET = os.environ.get("PAYSTACK_SECRET_KEY", "").strip()
PAYSTACK_MODE = os.environ.get("PAYSTACK_MODE", "live" if PAYSTACK_SECRET else "mock").lower()
if PAYSTACK_MODE == "live" and not PAYSTACK_SECRET:
    raise SystemExit("PAYSTACK_MODE=live needs PAYSTACK_SECRET_KEY")

APP_URL = os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"  # set to 1 behind HTTPS

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@fanmillionaire.local").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # if empty a random one is generated and printed once

TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"  # 1 when behind nginx/Cloudflare/etc.

# Football data feed (football-data.org). Empty = demo data, nothing real is fetched.
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()

SESSION_DAYS = 14
