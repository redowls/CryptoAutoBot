"""Alpaca paper-trading REST client. Paper URL only — no live path exists.

Crypto gotcha: orders take "SOL/USD" but positions come back as "SOLUSD";
everything here normalizes to the bare symbol ("SOL").
"""
import time

import requests

from . import config


class BrokerError(Exception):
    pass


def _headers():
    if not (config.ALPACA_KEY and config.ALPACA_SECRET):
        raise BrokerError("Alpaca keys not configured (APCA_API_KEY_ID/APCA_API_SECRET_KEY)")
    return {
        "APCA-API-KEY-ID": config.ALPACA_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET,
    }


def _request(method, path, **kwargs):
    url = f"{config.PAPER_BASE_URL}/v2{path}"
    try:
        r = requests.request(method, url, headers=_headers(), timeout=20, **kwargs)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json() if r.text else {}
    except requests.RequestException as e:
        raise BrokerError(f"{method} {path}: {e}") from e


def _bare(alpaca_symbol):
    return alpaca_symbol.replace("/", "").removesuffix("USD")


def get_account():
    acct = _request("GET", "/account")
    return {"equity": float(acct["equity"]), "cash": float(acct["cash"]),
            "status": acct.get("status")}


def get_positions():
    out = []
    for p in _request("GET", "/positions") or []:
        out.append({
            "symbol": _bare(p["symbol"]),
            "qty": float(p["qty"]),
            "avg_entry_price": float(p["avg_entry_price"]),
            "current_price": float(p["current_price"]),
        })
    return out


def submit_market_order(symbol, qty, side):
    order = _request("POST", "/orders", json={
        "symbol": config.pair(symbol),
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
    })
    return order["id"]


def get_order(order_id):
    o = _request("GET", f"/orders/{order_id}")
    if o is None:
        return {"status": "not_found", "filled_avg_price": None, "filled_qty": 0.0}
    return {
        "status": o["status"],
        "filled_avg_price": float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
        "filled_qty": float(o.get("filled_qty") or 0.0),
    }


def cancel_order(order_id):
    _request("DELETE", f"/orders/{order_id}")


def wait_for_fill(order_id, timeout_s=90, poll_s=3, sleep=time.sleep):
    """Poll until filled; on timeout cancel and report what (if anything) filled.

    Returns (status, filled_avg_price, filled_qty) — status: filled | canceled | partial.
    Crypto paper fills are known to be unreliable; never assume a fill.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        o = get_order(order_id)
        if o["status"] == "filled":
            return "filled", o["filled_avg_price"], o["filled_qty"]
        if o["status"] in ("canceled", "expired", "rejected", "not_found"):
            return "canceled", o["filled_avg_price"], o["filled_qty"]
        sleep(poll_s)
    try:
        cancel_order(order_id)
    except BrokerError:
        pass
    o = get_order(order_id)
    if o["filled_qty"] > 0:
        return "partial", o["filled_avg_price"], o["filled_qty"]
    return "canceled", None, 0.0


def close_position(symbol):
    """Liquidate one position via Alpaca's close endpoint. Returns order id or None."""
    o = _request("DELETE", f"/positions/{config.pair(symbol).replace('/', '')}")
    return o.get("id") if o else None
