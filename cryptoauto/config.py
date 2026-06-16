import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "snapshots"
MEMORY_DIR = ROOT / "memory"
LOG_DIR = ROOT / "logs"

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
