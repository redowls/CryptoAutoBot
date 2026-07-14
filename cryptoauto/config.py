import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "snapshots"
MEMORY_DIR = ROOT / "memory"
LOG_DIR = ROOT / "logs"
TRADES_DIR = ROOT / "data" / "trades"
POLICY_PATH = MEMORY_DIR / "policy.json"


def _load_dotenv(path=ROOT / ".env"):
    # cron runs without a login shell; pick up keys from .env ourselves
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Alpaca-tradable universe (top liquidity; BNB/ADA not on Alpaca → LTC/UNI)
WATCHLIST = ["BTC", "ETH", "SOL", "XRP", "DOGE", "AVAX", "LINK", "DOT", "LTC", "UNI"]


def pair(sym: str) -> str:
    return f"{sym}/USD"


TIMEFRAMES = {"1H": "1Hour", "4H": "4Hour", "1D": "1Day"}
BARS_URL = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
BARS_LIMIT = 1000  # max page; keyless intraday caps ~7 days regardless

# How far back to request per timeframe. Without an explicit start, the keyless
# feed returns only the current day — too few bars to warm EMA55/ADX. Intraday
# is capped ~7 days keyless (enough for 1H); daily goes back fully.
LOOKBACK_DAYS = {"1H": 10, "4H": 30, "1D": 200}

EMA_PERIODS = (8, 20, 55)
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
VOL_PERIOD = 20

ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")

# --- Phase B trading (paper only; no live endpoint exists in this codebase) ---
PAPER_BASE_URL = "https://paper-api.alpaca.markets"
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "false").lower() == "true"

MAX_POSITIONS = 3
RISK_PCT = 0.015            # equity fraction risked per trade
STOP_ATR_MULT = 3.0         # initial stop distance = 1R
TRAIL_ATR_MULT = 6.0        # trail distance once >= +1R
RISK_OFF_TRAIL_ATR_MULT = 3.0  # tighter trail while BTC regime is risk_off
TP_R = 2.5                  # hard take-profit in R multiples
TIME_STOP_HOURS = 120
CIRCUIT_BREAKER_PCT = 0.04  # rolling 24h realized loss halts new entries
REENTRY_THROTTLE_HOURS = 24
SNAPSHOT_MAX_AGE_MIN = 70   # never trade on a stale snapshot
POLICY_MAX_AGE_HOURS = 48   # stale policy.json is ignored

# entry filter thresholds (distilled from memory/insights.md)
ENTRY_ADX_MIN = 25.0
ENTRY_ADX_MIN_CAUTIOUS = 30.0  # when regime is neutral/risk_off
ENTRY_RSI_MIN = 45.0
ENTRY_RSI_MAX = 70.0
BLOWOFF_RSI = 80.0
LATE_ENTRY_DAY_PCT = 5.0    # skip coins already up more than this on the day

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
