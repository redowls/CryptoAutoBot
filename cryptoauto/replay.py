"""Historical replay harness (backtest) — IMP-B03.

Drives the bot's **real** engines — ``strategy.regime``, ``strategy.entry_candidates``,
``strategy.check_exit``, ``risk.position_size``, ``risk.circuit_breaker_tripped`` and
``ledger`` — over the stored hourly snapshots in ``data/snapshots/``, with a simulated
broker standing in for Alpaca. Nothing here reimplements the strategy, so a replay
result is a statement about the shipped logic rather than about a model of it.

The order of operations mirrors ``trader.run`` exactly: regime → exits (with trail) →
circuit breaker → entries.

Fidelity limits — read these before trusting a number:

* **Stale snapshot window.** Snapshots from 2026-06-16 to 2026-07-14 were captured
  before the pagination fix (f30cad9): 4H EMA55 was null and 1H data up to 72h stale.
  Any replay touching that window is measuring bad data, not bad strategy. The CLI
  refuses it unless ``--allow-stale`` is passed.
* **Reconstructed extras.** ``day_change_pct``, ``last_1h_close`` and ``prev_1h_close``
  are fetched live by the trader and are not stored in a snapshot. Replay rebuilds
  them from the snapshot series: ``prev_1h_close`` is the preceding hour's snapshot,
  and ``day_change_pct`` compares the current daily close to the previous day's final
  recorded close. A gap in the snapshot series therefore shifts ``prev_1h_close`` to
  whatever hour came before it.
* **Fills at the 1H close.** Exits fill at the close of the bar that breached, not at
  the stop price — there is no intrabar data in a snapshot. This matches the live bot,
  which also only checks hourly and exits at market, but it means a gap through the
  stop is modelled no worse (and no better) than live.
* **Fees** use ``config.FEE_RATE``, the same model the live ledger applies, so replay
  P&L is directly comparable to ``cryptoauto.report`` output.
* **No policy overlay.** Replay runs pure deterministic mode; the Claude overlay is
  not reconstructed. This matches the bot's behaviour since 2026-09-01.

Usage::

    python -m cryptoauto.replay --days 30
    python -m cryptoauto.replay --start 2026-07-15 --end 2026-09-01
    python -m cryptoauto.replay --days 30 --set ENTRY_ADX_MIN=25 --set MAX_POSITIONS=4
"""
import argparse
import contextlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config, ledger, report, risk, strategy

# The pagination fix (f30cad9) landed 2026-07-14; snapshots before it carry stale
# intraday data and cannot support a 1H/4H backtest.
TRUSTWORTHY_FROM = "2026-07-15"


def stale_window_warning(start, end):
    """Return a warning string when the window reaches into the stale period."""
    if str(start) >= TRUSTWORTHY_FROM:
        return None
    return (
        f"snapshots before {TRUSTWORTHY_FROM} predate the pagination fix (f30cad9): "
        f"4H EMA55 was null and 1H data up to 72h stale, so 1H/4H signals over "
        f"{start}..{end} are measuring bad data, not bad strategy"
    )


def load_snapshots(start, end, root=None):
    """Load ``[(hour_utc, snapshot)]`` for the inclusive date range."""
    root = Path(root) if root else config.DATA_DIR
    d0 = date.fromisoformat(str(start))
    d1 = date.fromisoformat(str(end))
    series = []
    day = d0
    while day <= d1:
        folder = root / day.isoformat()
        if folder.is_dir():
            for f in sorted(folder.glob("*.json")):
                try:
                    snap = json.loads(f.read_text())
                except ValueError:
                    continue
                try:
                    hour = int(f.stem)
                except ValueError:
                    continue
                ts = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
                series.append((ts, snap))
        day += timedelta(days=1)
    return series


_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def normalise_reason(reason):
    """Collapse the numbers out of a rejection reason so the tally aggregates.

    ``strategy`` embeds the measured value in each reason ("ADX 19.2 < 30"), which
    is right for a per-hour log and useless for a count -- every hour would be its
    own bucket. The tally wants the shape, not the reading.
    """
    return _NUMBER.sub("N", reason)


def _tf(coin, key):
    tf = coin.get("timeframes", {}).get(key, {})
    return tf if tf.get("status") == "ok" else None


def _close(coin, prefer="1D"):
    tf = _tf(coin, prefer) or _tf(coin, "1H")
    return tf.get("last_close") if tf else None


def build_extras(series):
    """Rebuild the per-hour ``extras`` the trader fetches live.

    Returns a list parallel to ``series``: ``[{symbol: {day_change_pct,
    last_1h_close, prev_1h_close}}]``.
    """
    # final recorded daily close per date, so day N can be compared to day N-1
    daily_final = {}
    for ts, snap in series:
        for coin in snap.get("symbols", []):
            close = _close(coin, "1D")
            if close is not None:
                daily_final.setdefault(ts.date(), {})[coin["symbol"]] = close

    out = []
    prev_by_sym = {}
    for ts, snap in series:
        hour = {}
        yesterday = daily_final.get(ts.date() - timedelta(days=1), {})
        for coin in snap.get("symbols", []):
            sym = coin["symbol"]
            h1 = _tf(coin, "1H")
            last = h1.get("last_close") if h1 else None
            prev_daily = yesterday.get(sym)
            today = _close(coin, "1D")
            hour[sym] = {
                "day_change_pct": ((today / prev_daily - 1) * 100)
                                  if (prev_daily and today) else None,
                "last_1h_close": last,
                "prev_1h_close": prev_by_sym.get(sym),
            }
        out.append(hour)
        for coin in snap.get("symbols", []):
            h1 = _tf(coin, "1H")
            if h1 and h1.get("last_close") is not None:
                prev_by_sym[coin["symbol"]] = h1["last_close"]
    return out


@contextlib.contextmanager
def _overridden(overrides):
    """Temporarily set config attributes; always restore, even on error."""
    overrides = overrides or {}
    for key in overrides:
        if not hasattr(config, key):
            raise ValueError(f"unknown config key: {key}")
    saved = {k: getattr(config, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(config, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(config, k, v)


@dataclass
class ReplayResult:
    entries: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    open_at_end: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    rejections: Counter = field(default_factory=Counter)
    max_concurrent: int = 0
    hours: int = 0
    start_equity: float = 0.0
    end_equity: float = 0.0
    circuit_breaker_hours: int = 0


def replay(series, equity=10000.0, overrides=None):
    """Run the real engines over ``series``. Returns a :class:`ReplayResult`."""
    with _overridden(overrides):
        return _run(series, equity)


def _run(series, start_equity):
    extras_by_hour = build_extras(series)
    led = {"open": [], "closed": [], "last_entry_attempt": {}}
    cash = float(start_equity)
    res = ReplayResult(start_equity=float(start_equity), hours=len(series))

    for i, (now, snap) in enumerate(series):
        by_sym = {c.get("symbol"): c for c in snap.get("symbols", [])}

        def h1_for(sym):
            coin = by_sym.get(sym)
            return _tf(coin, "1H") if coin else None

        # mark to market on this hour's closes
        def equity_now():
            held = 0.0
            for p in led["open"]:
                tf = h1_for(p["symbol"])
                price = tf["last_close"] if tf else p["entry_price"]
                held += p["qty"] * price
            return cash + held

        reg = strategy.regime(snap)

        # --- exits first (same order as trader.run) ---
        for pos in list(led["open"]):
            tf = h1_for(pos["symbol"])
            if tf is None:
                continue  # no data this hour -> hold, as live does
            action, updated = strategy.check_exit(
                pos, tf, reg, ledger.hours_held(pos, now))
            if action:
                exit_price = tf["last_close"]
                trade = ledger.close_position(led, updated, exit_price, action, now=now)
                cash += updated["qty"] * exit_price * (1 - config.FEE_RATE)
                res.closed.append(trade)
            else:
                ledger.update_position(led, updated)

        equity = equity_now()
        res.equity_curve.append((now.isoformat(), round(equity, 2)))

        # --- circuit breaker ---
        if risk.circuit_breaker_tripped(led["closed"], equity, now=now):
            res.circuit_breaker_hours += 1
            res.max_concurrent = max(res.max_concurrent, len(led["open"]))
            continue

        # --- entries ---
        open_syms = {p["symbol"] for p in led["open"]}
        slots = config.MAX_POSITIONS - len(open_syms)
        if slots > 0:
            candidates, rejections = strategy.entry_candidates(
                snap, extras_by_hour[i], open_syms, reg)
            for _sym, reason in rejections:
                res.rejections[normalise_reason(reason)] += 1
            entered = 0
            for sym, coin in candidates:
                if entered >= slots:
                    break
                if ledger.throttled(led, sym, now=now):
                    res.rejections["re-entry throttle (24h)"] += 1
                    continue
                tf = coin["timeframes"]["1H"]
                price, atr = tf["last_close"], tf["atr14"]
                qty, _stop, _risk_d = risk.position_size(
                    equity, price, atr, half=(reg == "risk_off"))
                cost = qty * price * (1 + config.FEE_RATE)
                if qty <= 0 or cost > cash:
                    res.rejections["unsizable or insufficient cash"] += 1
                    continue
                ledger.record_entry_attempt(led, sym, now=now)
                pos = ledger.open_position(led, sym, qty, price, atr, f"replay-{i}",
                                           half_size=(reg == "risk_off"), now=now)
                cash -= cost
                res.entries.append(dict(pos))
                entered += 1

        res.max_concurrent = max(res.max_concurrent, len(led["open"]))

    res.open_at_end = list(led["open"])
    res.end_equity = round(
        cash + sum(p["qty"] * p["entry_price"] for p in led["open"]), 2)
    return res


def render(res, label=""):
    """Replay header + the same performance block ``cryptoauto.report`` prints."""
    head = [
        f"REPLAY {label}".rstrip(),
        f"hours {res.hours} · entries {len(res.entries)} · closed {len(res.closed)} "
        f"· still open {len(res.open_at_end)} · peak concurrent {res.max_concurrent}"
        + (f" · circuit breaker halted {res.circuit_breaker_hours}h"
           if res.circuit_breaker_hours else ""),
        f"equity ${res.start_equity:,.2f} → ${res.end_equity:,.2f} "
        f"({(res.end_equity / res.start_equity - 1) * 100:+.2f}%)",
        "",
    ]
    body = report.render(res.closed)
    tail = ["", "top rejection reasons:"] + [
        f"  {n:6} {reason}" for reason, n in res.rejections.most_common(8)
    ]
    return "\n".join(head + [body] + tail)


def main():
    ap = argparse.ArgumentParser(description="CryptoAutoBot replay / backtest")
    ap.add_argument("--days", type=int, default=None, help="replay the last N days")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--equity", type=float, default=10000.0)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value, e.g. --set ENTRY_ADX_MIN=25")
    ap.add_argument("--allow-stale", action="store_true",
                    help=f"replay snapshots from before {TRUSTWORTHY_FROM} anyway")
    args = ap.parse_args()

    end = args.end or datetime.now(timezone.utc).date().isoformat()
    if args.start:
        start = args.start
    else:
        days = args.days if args.days is not None else 30
        start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()

    warning = stale_window_warning(start, end)
    if warning and not args.allow_stale:
        raise SystemExit(f"refusing: {warning}\n(pass --allow-stale to override)")
    if warning:
        print(f"⚠️  {warning}\n")

    overrides = {}
    for item in args.set:
        key, _, raw = item.partition("=")
        try:
            overrides[key] = int(raw) if raw.isdigit() else float(raw)
        except ValueError:
            overrides[key] = raw

    series = load_snapshots(start, end)
    if not series:
        raise SystemExit(f"no snapshots found for {start}..{end}")

    label = f"{start}..{end}"
    if overrides:
        label += "  [" + ", ".join(f"{k}={v}" for k, v in overrides.items()) + "]"
    print(render(replay(series, equity=args.equity, overrides=overrides), label))


if __name__ == "__main__":
    main()
