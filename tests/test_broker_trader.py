import json
from datetime import datetime, timezone

from cryptoauto import broker, config, ledger, trader

NOW = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)


# --- broker ---

def test_bare_symbol_normalization():
    assert broker._bare("SOLUSD") == "SOL"
    assert broker._bare("SOL/USD") == "SOL"
    assert broker._bare("BTCUSD") == "BTC"


def test_get_positions_parses_floats(monkeypatch):
    monkeypatch.setattr(broker, "_request", lambda m, p, **k: [
        {"symbol": "SOLUSD", "qty": "2.5", "avg_entry_price": "100.1", "current_price": "101.0"}])
    pos = broker.get_positions()
    assert pos == [{"symbol": "SOL", "qty": 2.5, "avg_entry_price": 100.1, "current_price": 101.0}]


def test_wait_for_fill_filled(monkeypatch):
    monkeypatch.setattr(broker, "get_order",
                        lambda oid: {"status": "filled", "filled_avg_price": 99.5, "filled_qty": 2.0})
    assert broker.wait_for_fill("o1", timeout_s=1, sleep=lambda s: None) == ("filled", 99.5, 2.0)


def test_wait_for_fill_timeout_cancels(monkeypatch):
    cancels = []
    monkeypatch.setattr(broker, "get_order",
                        lambda oid: {"status": "new", "filled_avg_price": None, "filled_qty": 0.0})
    monkeypatch.setattr(broker, "cancel_order", lambda oid: cancels.append(oid))
    status, price, qty = broker.wait_for_fill("o1", timeout_s=0.05, poll_s=0.01,
                                              sleep=lambda s: None)
    assert status == "canceled" and qty == 0.0
    assert cancels == ["o1"]


def test_broker_requires_keys(monkeypatch):
    monkeypatch.setattr(config, "ALPACA_KEY", None)
    try:
        broker._headers()
        assert False, "expected BrokerError"
    except broker.BrokerError:
        pass


# --- trader helpers ---

def _tf(ema8=110, ema20=105, ema55=100, rsi=55, adx=35, atr=2.0, close=112):
    return {"status": "ok", "ema8": ema8, "ema20": ema20, "ema55": ema55,
            "rsi14": rsi, "adx14": adx, "atr14": atr, "last_close": close}


def _snapshot(now=NOW):
    def coin(sym, **kw):
        return {"symbol": sym, "status": "ok",
                "timeframes": {"1H": _tf(**kw), "4H": _tf(), "1D": _tf(adx=25)}}
    return {
        "captured_at": now.isoformat(),
        "symbols": [coin("BTC"), coin("SOL", adx=40), coin("DOGE", adx=10)],
    }


def _write_snapshot(tmp_path, snap, now=NOW):
    d = tmp_path / now.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{now.strftime('%H')}.json").write_text(json.dumps(snap))


def _good_extras(syms):
    return {s: {"day_change_pct": 1.0, "last_1h_close": 112, "prev_1h_close": 110}
            for s in syms}


def test_load_current_snapshot_fresh_and_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert trader.load_current_snapshot(NOW) is None
    _write_snapshot(tmp_path, _snapshot(), NOW)
    assert trader.load_current_snapshot(NOW) is not None
    stale = _snapshot(NOW.replace(hour=8))
    _write_snapshot(tmp_path, stale, NOW.replace(hour=8))
    assert trader.load_current_snapshot(NOW.replace(hour=10)) is None


def test_dry_run_enters_best_candidate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "snaps")
    monkeypatch.setattr(config, "TRADES_DIR", tmp_path / "trades")
    monkeypatch.setattr(config, "POLICY_PATH", tmp_path / "policy.json")
    monkeypatch.setattr(config, "ALPACA_KEY", None)
    monkeypatch.setattr(config, "ALPACA_SECRET", None)
    monkeypatch.setattr(trader, "fetch_extras", lambda syms, now=None: _good_extras(syms))
    _write_snapshot(tmp_path / "snaps", _snapshot(), NOW)

    trader.run(dry_run=True, now=NOW)
    out = capsys.readouterr().out
    assert "regime: computed risk_on" in out
    assert "DRY-RUN entry SOL" in out
    assert "reject DOGE" in out
    # dry-run must not persist a ledger
    assert not (tmp_path / "trades" / "trades.json").exists()


def test_dry_run_exit_on_stop_hit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "snaps")
    monkeypatch.setattr(config, "TRADES_DIR", tmp_path / "trades")
    monkeypatch.setattr(config, "POLICY_PATH", tmp_path / "policy.json")
    monkeypatch.setattr(config, "ALPACA_KEY", None)
    monkeypatch.setattr(config, "ALPACA_SECRET", None)
    monkeypatch.setattr(trader, "fetch_extras", lambda syms, now=None: _good_extras(syms))
    snap = _snapshot()
    snap["symbols"][1]["timeframes"]["1H"]["last_close"] = 90.0  # SOL below stop
    _write_snapshot(tmp_path / "snaps", snap, NOW)

    led = ledger.load(tmp_path / "trades" / "trades.json")
    ledger.open_position(led, "SOL", 2.0, 100.0, 2.0, "o1", now=NOW)
    ledger.save(led, tmp_path / "trades" / "trades.json")

    trader.run(dry_run=True, now=NOW)
    out = capsys.readouterr().out
    assert "DRY-RUN exit SOL: stop" in out


def test_run_requires_trading_enabled(monkeypatch, capsys):
    monkeypatch.setattr(config, "TRADING_ENABLED", False)
    trader.run(dry_run=False, now=NOW)
    assert "TRADING_ENABLED is false" in capsys.readouterr().out


def test_circuit_breaker_blocks_entries(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "snaps")
    monkeypatch.setattr(config, "TRADES_DIR", tmp_path / "trades")
    monkeypatch.setattr(config, "POLICY_PATH", tmp_path / "policy.json")
    monkeypatch.setattr(config, "ALPACA_KEY", None)
    monkeypatch.setattr(config, "ALPACA_SECRET", None)
    _write_snapshot(tmp_path / "snaps", _snapshot(), NOW)

    led = ledger.load(tmp_path / "trades" / "trades.json")
    led["closed"].append({"pnl": -500.0, "exit_time": NOW.isoformat()})
    ledger.save(led, tmp_path / "trades" / "trades.json")

    trader.run(dry_run=True, now=NOW)
    out = capsys.readouterr().out
    assert "circuit breaker" in out
    assert "DRY-RUN entry" not in out
