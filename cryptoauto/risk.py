"""Position sizing and account-level risk controls."""
from datetime import datetime, timedelta, timezone

from . import config


def position_size(equity, entry_price, atr, half=False):
    """Risk RISK_PCT of equity with a STOP_ATR_MULT*ATR stop.

    Returns (qty, initial_stop, risk_dollars) or (0, None, 0) if unsizable.
    """
    if not atr or atr <= 0 or not entry_price or entry_price <= 0 or equity <= 0:
        return 0.0, None, 0.0
    stop_dist = config.STOP_ATR_MULT * atr
    if stop_dist >= entry_price:
        return 0.0, None, 0.0  # stop below zero — volatility too wide to size
    risk_dollars = equity * config.RISK_PCT
    if half:
        risk_dollars /= 2
    qty = risk_dollars / stop_dist
    # never exceed the cash a single position may use (cap notional at 1/MAX_POSITIONS)
    max_notional = equity / config.MAX_POSITIONS
    if qty * entry_price > max_notional:
        qty = max_notional / entry_price
    return round(qty, 8), entry_price - stop_dist, risk_dollars


def circuit_breaker_tripped(closed_trades, equity, now=None):
    """True when realized losses over the trailing 24h reach CIRCUIT_BREAKER_PCT."""
    if equity <= 0:
        return True
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    realized = 0.0
    for t in closed_trades:
        try:
            exit_time = datetime.fromisoformat(t["exit_time"])
        except (KeyError, ValueError):
            continue
        if exit_time >= cutoff:
            realized += t.get("pnl", 0.0)
    return realized <= -config.CIRCUIT_BREAKER_PCT * equity
