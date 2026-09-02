"""Replay harness: drives the REAL strategy/risk/ledger over stored snapshots."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from cryptoauto import config, replay

NOW = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)


def _tf(close, ema=(1.0, 0.9, 0.8), rsi=55.0, adx=30.0, atr=1.0):
    """A timeframe block; default EMA tuple stacks UP (8 > 20 > 55)."""
    return {
        "status": "ok", "last_close": close, "last_time": "x",
        "ema8": close * ema[0], "ema20": close * ema[1], "ema55": close * ema[2],
        "rsi14": rsi, "atr14": atr, "adx14": adx,
        "vol20": 1.0, "last_vol": 1.0, "bar_count": 240,
    }


def _coin(sym, close, **kw):
    return {
        "symbol": sym, "pair": f"{sym}/USD", "status": "ok",
        "timeframes": {
            "1H": _tf(close, **kw),
            "4H": _tf(close),
            "1D": _tf(close, adx=25.0),
        },
    }


def _snap(at, coins):
    return {"captured_at": at.isoformat(), "symbols": coins}


def _series(hours, coins_at_hour):
    """[(datetime, snapshot)] for `hours` consecutive hours."""
    return [(NOW + timedelta(hours=h), _snap(NOW + timedelta(hours=h), coins_at_hour(h)))
            for h in range(hours)]


# --- extras reconstruction (the snapshot does not store these) ---

def test_prev_1h_close_comes_from_the_preceding_snapshot():
    series = _series(3, lambda h: [_coin("BTC", 100.0 + h)])
    extras = replay.build_extras(series)
    assert extras[2]["BTC"]["last_1h_close"] == 102.0
    assert extras[2]["BTC"]["prev_1h_close"] == 101.0


def test_first_snapshot_has_no_previous_close():
    series = _series(2, lambda h: [_coin("BTC", 100.0 + h)])
    assert extras_first(series)["prev_1h_close"] is None


def extras_first(series):
    return replay.build_extras(series)[0]["BTC"]


def test_day_change_is_measured_against_the_previous_days_close():
    """Live reads it from DAILY bars: (last_daily_close / prev_daily_close - 1)."""
    day1 = [(datetime(2026, 8, 1, h, tzinfo=timezone.utc),
             _snap(datetime(2026, 8, 1, h, tzinfo=timezone.utc), [_coin("BTC", 100.0)]))
            for h in range(24)]
    day2 = [(datetime(2026, 8, 2, h, tzinfo=timezone.utc),
             _snap(datetime(2026, 8, 2, h, tzinfo=timezone.utc), [_coin("BTC", 110.0)]))
            for h in range(3)]
    extras = replay.build_extras(day1 + day2)
    # day 2 closes 110 against day 1's final 100 -> +10%
    assert extras[24]["BTC"]["day_change_pct"] == pytest.approx(10.0)


def test_day_change_is_none_on_the_first_day():
    series = _series(3, lambda h: [_coin("BTC", 100.0)])
    assert replay.build_extras(series)[0]["BTC"]["day_change_pct"] is None


# --- the engine ---
#
# Entry needs `day_change_pct`, which only exists once a PREVIOUS day is in the
# series, and needs the 1H close to be green. So engine fixtures prepend a full
# priming day and rise gently -- a flat series can never open a position, which
# is the strategy behaving correctly rather than the harness failing.

def _primed(hours, coins_at_hour):
    """A full priming day of history, then `hours` live hours."""
    day0 = datetime(2026, 7, 31, 0, tzinfo=timezone.utc)
    series = [(day0 + timedelta(hours=h), _snap(day0 + timedelta(hours=h),
                                                coins_at_hour(0)))
              for h in range(24)]
    return series + [(NOW + timedelta(hours=h), _snap(NOW + timedelta(hours=h),
                                                      coins_at_hour(h)))
                     for h in range(hours)]


# +0.01/h on a base of 10: green every hour, ~+2.4%/day (under the 5% late-entry
# cap), and 750h short of the 2.5R target -- so exits come from the time stop.
def _rise(h):
    return 10.0 + h * 0.01


def _entryable(h):
    """BTC sets a risk_on regime; UNI passes every entry filter, rising gently."""
    return [_coin("BTC", 1000.0, adx=30.0), _coin("UNI", _rise(h), adx=40.0)]


def test_replay_opens_a_position_through_the_real_sizing_path():
    res = replay.replay(_primed(4, _entryable), equity=10000.0)
    assert res.entries, "expected at least one entry"
    t = res.entries[0]
    assert t["symbol"] in {"BTC", "UNI"}
    # 1.5% of 10k risked over a 3xATR stop, capped at equity/MAX_POSITIONS notional
    assert t["qty"] > 0
    assert t["initial_stop"] < t["entry_price"]


def test_replay_charges_fees_so_a_slow_round_trip_still_pays_the_spread():
    """Same fee model as the live ledger -- replay P&L is directly comparable."""
    res = replay.replay(_primed(200, _entryable), equity=10000.0)
    assert res.closed, "expected the 120h time stop to close something"
    assert any(t["reason"] == "time" for t in res.closed)
    for t in res.closed:
        assert t["fees"] > 0
        assert t["pnl"] < t["pnl_gross"]


def test_replay_respects_max_positions():
    def many(h):
        return [_coin("BTC", 1000.0, adx=30.0)] + [
            _coin(s, _rise(h), adx=40.0) for s in ("UNI", "SOL", "LTC", "DOT", "LINK")
        ]
    res = replay.replay(_primed(6, many), equity=10000.0)
    assert res.entries, "fixture should open positions"
    assert res.max_concurrent <= config.MAX_POSITIONS


def test_replay_is_deterministic():
    a = replay.replay(_primed(8, _entryable), equity=10000.0)
    b = replay.replay(_primed(8, _entryable), equity=10000.0)
    assert a.closed == b.closed and a.entries == b.entries


def test_overrides_change_the_outcome_without_mutating_config():
    """The whole point: test a threshold change before shipping it."""
    before = config.ENTRY_ADX_MIN
    allowed = replay.replay(_primed(4, _entryable), equity=10000.0)
    blocked = replay.replay(_primed(4, _entryable), equity=10000.0,
                            overrides={"ENTRY_ADX_MIN": 99.0})
    assert allowed.entries and blocked.entries == []
    assert config.ENTRY_ADX_MIN == before, "config must be restored"


def test_overrides_are_restored_even_when_the_run_raises():
    before = config.MAX_POSITIONS
    with pytest.raises(ValueError):
        replay.replay(_primed(2, _entryable), overrides={"MAX_POSITIONS": 9, "NOPE": 1})
    assert config.MAX_POSITIONS == before


def test_overrides_reject_unknown_keys():
    with pytest.raises(ValueError, match="NOPE"):
        replay.replay(_primed(2, _entryable), overrides={"NOPE": 1})


def test_rejections_are_tallied_by_reason():
    res = replay.replay(_primed(4, _entryable), equity=10000.0,
                        overrides={"ENTRY_ADX_MIN": 99.0})
    assert any("ADX" in reason for reason in res.rejections)


# --- the stale-data guard (snapshots 2026-06-16..07-14 are untrustworthy) ---

def test_window_starting_in_the_stale_period_is_flagged():
    warns = replay.stale_window_warning("2026-06-20", "2026-08-01")
    assert warns and "2026-07-15" in warns


def test_window_after_the_pagination_fix_is_not_flagged():
    assert replay.stale_window_warning("2026-07-20", "2026-08-01") is None


def test_load_snapshots_reads_a_date_range(tmp_path):
    root = tmp_path / "snapshots"
    for day, hours in (("2026-08-01", (0, 1)), ("2026-08-02", (0,))):
        d = root / day
        d.mkdir(parents=True)
        for h in hours:
            at = datetime.fromisoformat(f"{day}T{h:02d}:00:00+00:00")
            (d / f"{h:02d}.json").write_text(json.dumps(_snap(at, [_coin("BTC", 100.0)])))
    series = replay.load_snapshots("2026-08-01", "2026-08-01", root=root)
    assert len(series) == 2
    assert all(ts.date().isoformat() == "2026-08-01" for ts, _ in series)


def test_rejection_reasons_are_normalised_so_they_aggregate():
    """'ADX 19.2 < 30' and 'ADX 22.7 < 30' must land in ONE bucket, not two."""
    res = replay.replay(_primed(6, _entryable), equity=10000.0,
                        overrides={"ENTRY_ADX_MIN": 99.0})
    adx_keys = [k for k in res.rejections if "ADX" in k]
    assert len(adx_keys) == 1, f"expected one ADX bucket, got {adx_keys}"
    assert res.rejections[adx_keys[0]] > 1
