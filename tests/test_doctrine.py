"""Stop-exit doctrine: a stop is a failed trade whatever the P&L sign."""
from cryptoauto import doctrine


def _trade(**kw):
    base = {
        "symbol": "SOL", "qty": 1.0,
        "entry_price": 100.0, "exit_price": 110.0,
        "initial_stop": 90.0,          # 1R = 10.0
        "reason": "tp", "pnl": 10.0,
    }
    base.update(kw)
    return base


# --- the rule the doctrine exists to enforce ---

def test_profitable_stop_is_a_fail_not_a_win():
    """The anti-gaming rule: a +0.0% break-even stop is NOT a win.

    The live book has exactly one of these (+$127.27 on a stop) that the old
    `pnl > 0` scoring counted as a win.
    """
    v = doctrine.classify(_trade(exit_price=101.0, reason="stop", pnl=1.0))
    assert v.bucket == doctrine.FAIL
    assert v.stop_driven is True
    assert v.headline_win is True  # what pnl > 0 would have said
    assert v.profit_r == 0.1


def test_take_profit_exit_is_a_win():
    v = doctrine.classify(_trade(exit_price=125.0, reason="tp", pnl=25.0))
    assert v.bucket == doctrine.WIN
    assert v.stop_driven is False


def test_stop_above_fail_threshold_is_a_scratch_not_a_fail():
    """A stop that banked more than +0.25R gave something back, but not a win."""
    v = doctrine.classify(_trade(exit_price=105.0, reason="stop", pnl=5.0))
    assert v.profit_r == 0.5
    assert v.bucket == doctrine.SCRATCH


def test_full_stop_and_be_scratch_are_distinguished():
    """Which kind of failure it was decides what to fix."""
    full = doctrine.classify(_trade(exit_price=90.0, reason="stop", pnl=-10.0))
    assert full.bucket == doctrine.FAIL and full.fail_kind == doctrine.FULL_STOP

    scratched = doctrine.classify(_trade(exit_price=99.0, reason="stop", pnl=-1.0))
    assert scratched.bucket == doctrine.FAIL and scratched.fail_kind == doctrine.BE_SCRATCH


def test_time_stop_giving_back_real_money_is_a_fail():
    v = doctrine.classify(_trade(exit_price=94.0, reason="time", pnl=-6.0))
    assert v.profit_r == -0.6
    assert v.bucket == doctrine.FAIL


def test_time_stop_near_flat_is_a_scratch():
    v = doctrine.classify(_trade(exit_price=101.0, reason="time", pnl=1.0))
    assert v.bucket == doctrine.SCRATCH


def test_non_stop_exit_reaching_1r_is_a_win_on_its_own_merits():
    v = doctrine.classify(_trade(exit_price=112.0, reason="time", pnl=12.0))
    assert v.profit_r >= doctrine.WIN_MIN_R
    assert v.bucket == doctrine.WIN


# --- historical rows predating IMP-B01 have no initial_stop ---

def test_row_without_initial_stop_scores_by_reason():
    v = doctrine.classify(_trade(initial_stop=None, reason="stop", pnl=50.0))
    assert v.bucket == doctrine.FAIL      # still a failure, still no R
    assert v.profit_r is None
    assert v.fail_kind == doctrine.UNKNOWN_STOP


def test_row_without_initial_stop_tp_is_still_a_win():
    v = doctrine.classify(_trade(initial_stop=None, reason="tp", pnl=50.0))
    assert v.bucket == doctrine.WIN and v.profit_r is None


def test_row_without_initial_stop_losing_time_exit_is_a_fail():
    v = doctrine.classify(_trade(initial_stop=None, reason="time", pnl=-5.0))
    assert v.bucket == doctrine.FAIL


# --- rollup ---

def test_summary_separates_true_from_headline_win_rate():
    trades = [
        _trade(exit_price=125.0, reason="tp", pnl=25.0),      # WIN
        _trade(exit_price=101.0, reason="stop", pnl=1.0),     # FAIL, headline win
        _trade(exit_price=90.0, reason="stop", pnl=-10.0),    # FAIL full stop
        _trade(exit_price=101.0, reason="time", pnl=1.0),     # SCRATCH, headline win
    ]
    s = doctrine.summarize([doctrine.classify(t) for t in trades])
    assert s.trades == 4
    assert s.stops == 2
    assert s.wins == 1 and s.fails == 2 and s.scratches == 1
    assert s.full_stops == 1 and s.be_scratches == 1
    assert s.true_win_rate == 0.25
    assert s.headline_win_rate == 0.75   # the gap the doctrine exists to expose
    assert s.stop_rate == 0.5
    assert s.fail_scratch_rate == 0.75


def test_summary_of_empty_book_does_not_divide_by_zero():
    s = doctrine.summarize([])
    assert s.trades == 0
    assert s.stop_rate == 0.0 and s.true_win_rate == 0.0


def test_format_reports_true_beside_headline():
    s = doctrine.summarize([doctrine.classify(_trade(exit_price=101.0, reason="stop", pnl=1.0))])
    out = doctrine.format_stop_exits(s)
    assert "stop rate: 1/1 (100%)" in out
    assert "true win rate: 0%" in out
    assert "headline 100%" in out


def test_format_handles_no_trades():
    assert "n/a" in doctrine.format_stop_exits(doctrine.summarize([]))
