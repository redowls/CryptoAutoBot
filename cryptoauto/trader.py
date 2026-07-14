"""Hourly paper-trading entrypoint (cron :12, right after the :05 snapshot).

Order of operations: load snapshot → reconcile with Alpaca → exits →
circuit breaker → entries → persist ledger + notify. `--dry-run` logs every
decision but places no orders and mutates nothing.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

from . import broker, config, data, ledger, notify, policy, risk, strategy

DRY_RUN_EQUITY = 10000.0


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def load_current_snapshot(now=None):
    """Latest snapshot no older than SNAPSHOT_MAX_AGE_MIN, else None."""
    now = now or datetime.now(timezone.utc)
    for candidate in (now, now - timedelta(hours=1)):
        path = config.DATA_DIR / candidate.strftime("%Y-%m-%d") / f"{candidate.strftime('%H')}.json"
        if not path.exists():
            continue
        try:
            snap = json.loads(path.read_text())
            captured = datetime.fromisoformat(snap["captured_at"])
        except (ValueError, KeyError):
            continue
        if now - captured <= timedelta(minutes=config.SNAPSHOT_MAX_AGE_MIN):
            return snap
    return None


def fetch_extras(symbols, now=None):
    """Fields the snapshot lacks: day change %, last two 1H closes."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for sym in symbols:
        extras = {"day_change_pct": None, "last_1h_close": None, "prev_1h_close": None}
        try:
            d1 = data.fetch_bars(config.pair(sym), "1Day",
                                 start=(now - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            if len(d1) >= 2:
                prev, last = d1[-2]["c"], d1[-1]["c"]
                if prev:
                    extras["day_change_pct"] = (last / prev - 1) * 100
            h1 = data.fetch_bars(config.pair(sym), "1Hour",
                                 start=(now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            if len(h1) >= 2:
                extras["last_1h_close"] = h1[-1]["c"]
                extras["prev_1h_close"] = h1[-2]["c"]
        except data.FetchError as e:
            log(f"extras: {sym}: {e}")
        out[sym] = extras
    return out


def _coin_h1(snap, symbol):
    for coin in snap.get("symbols", []):
        if coin.get("symbol") == symbol:
            tf = coin.get("timeframes", {}).get("1H", {})
            return tf if tf.get("status") == "ok" else None
    return None


def _exit_position(led, pos, price_hint, reason, dry_run):
    sym = pos["symbol"]
    if dry_run:
        log(f"DRY-RUN exit {sym}: {reason} @ ~{price_hint}")
        return None
    order_id = broker.close_position(sym)
    exit_price = price_hint
    if order_id:
        status, fill_price, _ = broker.wait_for_fill(order_id)
        if fill_price:
            exit_price = fill_price
        log(f"exit {sym}: {reason}, close order {status} @ {exit_price}")
    else:
        log(f"exit {sym}: {reason}, close endpoint returned no order — using last close {exit_price}")
    trade = ledger.close_position(led, pos, exit_price, reason)
    notify.send(f"CryptoAutoBot EXIT {sym} ({reason}) @ {exit_price:.4f} "
                f"P&L ${trade['pnl']:+.2f}")
    return trade


def _enter_position(led, sym, coin, equity, reg, dry_run):
    h1 = coin["timeframes"]["1H"]
    price, atr = h1["last_close"], h1["atr14"]
    qty, stop, risk_d = risk.position_size(equity, price, atr, half=(reg == "risk_off"))
    if qty <= 0:
        log(f"entry {sym}: unsizable (price {price}, atr {atr})")
        return None
    if dry_run:
        log(f"DRY-RUN entry {sym}: qty {qty} @ ~{price}, stop {stop:.4f}, risk ${risk_d:.2f}")
        return None
    ledger.record_entry_attempt(led, sym)
    order_id = broker.submit_market_order(sym, qty, "buy")
    status, fill_price, filled_qty = broker.wait_for_fill(order_id)
    if status == "canceled" or not filled_qty:
        log(f"entry {sym}: order {status} unfilled — skipped (known crypto paper-fill flakiness)")
        return None
    entry_price = fill_price or price
    pos = ledger.open_position(led, sym, filled_qty, entry_price, atr, order_id,
                               half_size=(reg == "risk_off"))
    log(f"entry {sym}: {status} qty {filled_qty} @ {entry_price}, stop {pos['stop']:.4f}")
    notify.send(f"CryptoAutoBot ENTRY {sym} qty {filled_qty} @ {entry_price:.4f} "
                f"stop {pos['stop']:.4f} (regime {reg})")
    return pos


def run(dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    if not dry_run and not config.TRADING_ENABLED:
        log("TRADING_ENABLED is false — exiting (use --dry-run to preview decisions)")
        return
    snap = load_current_snapshot(now)
    if snap is None:
        log("no fresh snapshot (missing or older than "
            f"{config.SNAPSHOT_MAX_AGE_MIN} min) — skipping cycle")
        return

    have_keys = bool(config.ALPACA_KEY and config.ALPACA_SECRET)
    if have_keys:
        acct = broker.get_account()
        equity = acct["equity"]
        alpaca_positions = broker.get_positions()
        log(f"account equity ${equity:.2f}, {len(alpaca_positions)} Alpaca positions")
    elif dry_run:
        equity, alpaca_positions = DRY_RUN_EQUITY, None
        log(f"no Alpaca keys — dry-run with simulated equity ${equity:.2f}")
    else:
        log("no Alpaca keys — cannot trade")
        return

    led = ledger.load()
    if alpaca_positions is not None:
        for note in ledger.reconcile(led, alpaca_positions):
            log(note)

    pol = policy.load(now=now)
    computed = strategy.regime(snap)
    reg = strategy.effective_regime(computed, pol["regime_hint"])
    log(f"regime: computed {computed}, policy hint {pol['regime_hint']} -> {reg}")

    # exits first
    for pos in list(led["open"]):
        h1 = _coin_h1(snap, pos["symbol"])
        if h1 is None:
            log(f"exit check {pos['symbol']}: no 1H data this hour — holding")
            continue
        action, updated = strategy.check_exit(pos, h1, reg, ledger.hours_held(pos, now))
        if action:
            _exit_position(led, updated, h1["last_close"], action, dry_run)
        else:
            ledger.update_position(led, updated)
            if updated["stop"] != pos["stop"]:
                log(f"trail {pos['symbol']}: stop -> {updated['stop']:.4f}")

    if risk.circuit_breaker_tripped(led["closed"], equity, now=now):
        log(f"circuit breaker: 24h realized loss >= {config.CIRCUIT_BREAKER_PCT:.0%} "
            "of equity — no new entries")
        if not dry_run:
            ledger.save(led)
        return

    # entries
    open_syms = {p["symbol"] for p in led["open"]}
    slots = min(pol["max_positions"], config.MAX_POSITIONS) - len(open_syms)
    if slots <= 0:
        log(f"no entry slots ({len(open_syms)} open)")
    else:
        extras = fetch_extras([c["symbol"] for c in snap.get("symbols", [])], now=now)
        candidates, rejections = strategy.entry_candidates(
            snap, extras, open_syms, reg, blocked=set(pol["blocked_symbols"]))
        for sym, reason in rejections:
            log(f"reject {sym}: {reason}")
        entered = 0
        for sym, coin in candidates:
            if entered >= slots:
                break
            if ledger.throttled(led, sym, now=now):
                log(f"reject {sym}: re-entry throttle (24h)")
                continue
            if _enter_position(led, sym, coin, equity, reg, dry_run) or dry_run:
                entered += 1
        if not candidates:
            log("no entry candidates this hour")

    if not dry_run:
        ledger.save(led)
    log("cycle done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CryptoAutoBot hourly paper trader")
    parser.add_argument("--dry-run", action="store_true",
                        help="log decisions without placing orders or saving state")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
