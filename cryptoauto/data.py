import requests

from . import config


class FetchError(Exception):
    pass


def fetch_bars(pair: str, timeframe: str, limit: int = config.BARS_LIMIT, start: str = None,
               max_pages: int = 10):
    """Fetch OHLCV bars for one symbol/timeframe. Returns list (possibly empty).

    The feed serves ~7 days per page regardless of `limit`, so we must follow
    next_page_token or intraday bars come back weeks stale (bug found 2026-07-14:
    every 4H snapshot since launch lagged ~3 weeks).
    """
    params = {"symbols": pair, "timeframe": timeframe, "limit": limit}
    if start:
        params["start"] = start
    headers = {}
    if config.ALPACA_KEY and config.ALPACA_SECRET:
        headers = {
            "APCA-API-KEY-ID": config.ALPACA_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_SECRET,
        }
    bars = []
    for _ in range(max_pages):
        try:
            r = requests.get(config.BARS_URL, params=params, headers=headers, timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:  # network, HTTP, JSON
            raise FetchError(f"{pair} {timeframe}: {e}") from e
        bars.extend(payload.get("bars", {}).get(pair, []))
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    return bars
