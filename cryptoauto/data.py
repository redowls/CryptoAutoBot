import requests

from . import config


class FetchError(Exception):
    pass


def fetch_bars(pair: str, timeframe: str, limit: int = config.BARS_LIMIT):
    """Fetch OHLCV bars for one symbol/timeframe. Returns list (possibly empty)."""
    params = {"symbols": pair, "timeframe": timeframe, "limit": limit}
    headers = {}
    if config.ALPACA_KEY and config.ALPACA_SECRET:
        headers = {
            "APCA-API-KEY-ID": config.ALPACA_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_SECRET,
        }
    try:
        r = requests.get(config.BARS_URL, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:  # network, HTTP, JSON
        raise FetchError(f"{pair} {timeframe}: {e}") from e
    return payload.get("bars", {}).get(pair, [])
