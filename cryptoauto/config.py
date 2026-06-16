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
BARS_LIMIT = 200  # warms EMA55 / ADX(14) comfortably

EMA_PERIODS = (8, 20, 55)
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
VOL_PERIOD = 20

ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
