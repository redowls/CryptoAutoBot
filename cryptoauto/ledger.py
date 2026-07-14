"""Trade ledger: data/trades/trades.json.

Alpaca is the source of truth for what is held; the ledger adds the entry
context exits need (initial stop, high-water, timestamps) plus closed-trade
history and the per-coin re-entry throttle.
"""
import json
from datetime import datetime, timedelta, timezone

from . import config

EMPTY = {"open": [], "closed": [], "last_entry_attempt": {}}


def load(path=None):
    path = path or config.TRADES_DIR / "trades.json"
    try:
        led = json.loads(path.read_text())
    except (OSError, ValueError):
        return json.loads(json.dumps(EMPTY))
    for key, default in EMPTY.items():
        led.setdefault(key, json.loads(json.dumps(default)))
    return led


def save(led, path=None):
    path = path or config.TRADES_DIR / "trades.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(led, indent=2))


def open_position(led, symbol, qty, entry_price, atr, order_id, half_size=False, now=None):
    now = now or datetime.now(timezone.utc)
    stop = entry_price - config.STOP_ATR_MULT * atr
    pos = {
        "symbol": symbol,
        "qty": qty,
        "entry_price": entry_price,
        "entry_time": now.isoformat(),
        "initial_stop": stop,
        "stop": stop,
        "high_water": entry_price,
        "atr_at_entry": atr,
        "order_id": order_id,
        "half_size": half_size,
    }
    led["open"].append(pos)
    return pos


def close_position(led, pos, exit_price, reason, now=None):
    now = now or datetime.now(timezone.utc)
    led["open"] = [p for p in led["open"] if p is not pos and p["symbol"] != pos["symbol"]]
    trade = {
        "symbol": pos["symbol"],
        "qty": pos["qty"],
        "entry_price": pos["entry_price"],
        "exit_price": exit_price,
        "entry_time": pos["entry_time"],
        "exit_time": now.isoformat(),
        "pnl": round((exit_price - pos["entry_price"]) * pos["qty"], 2),
        "reason": reason,
    }
    led["closed"].append(trade)
    return trade


def update_position(led, pos):
    for i, p in enumerate(led["open"]):
        if p["symbol"] == pos["symbol"]:
            led["open"][i] = pos
            return


def hours_held(pos, now=None):
    now = now or datetime.now(timezone.utc)
    return (now - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 3600


def throttled(led, symbol, now=None):
    """True while the per-coin re-entry throttle window is active."""
    now = now or datetime.now(timezone.utc)
    last = led["last_entry_attempt"].get(symbol)
    if not last:
        return False
    return now - datetime.fromisoformat(last) < timedelta(hours=config.REENTRY_THROTTLE_HOURS)


def record_entry_attempt(led, symbol, now=None):
    now = now or datetime.now(timezone.utc)
    led["last_entry_attempt"][symbol] = now.isoformat()


def reconcile(led, alpaca_positions, now=None):
    """Sync ledger open positions with Alpaca's. Returns list of log lines.

    - ledger position missing on Alpaca → drop it (closed outside the bot)
    - Alpaca position unknown to ledger → adopt with synthetic stop so exits manage it
    - qty mismatch → Alpaca wins
    """
    now = now or datetime.now(timezone.utc)
    notes = []
    by_sym = {p["symbol"]: p for p in alpaca_positions}
    kept = []
    for pos in led["open"]:
        live = by_sym.pop(pos["symbol"], None)
        if live is None:
            notes.append(f"reconcile: {pos['symbol']} gone on Alpaca — dropped from ledger")
            continue
        if abs(live["qty"] - pos["qty"]) > 1e-9:
            notes.append(f"reconcile: {pos['symbol']} qty {pos['qty']} -> {live['qty']} (Alpaca wins)")
            pos["qty"] = live["qty"]
        kept.append(pos)
    for sym, live in by_sym.items():
        price = live["current_price"]
        stop = price * 0.97 if not live.get("atr") else price - config.STOP_ATR_MULT * live["atr"]
        kept.append({
            "symbol": sym,
            "qty": live["qty"],
            "entry_price": live.get("avg_entry_price", price),
            "entry_time": now.isoformat(),
            "initial_stop": stop,
            "stop": stop,
            "high_water": price,
            "atr_at_entry": live.get("atr"),
            "order_id": None,
            "half_size": False,
            "adopted": True,
        })
        notes.append(f"reconcile: adopted unknown Alpaca position {sym} qty {live['qty']}")
    led["open"] = kept
    return notes
