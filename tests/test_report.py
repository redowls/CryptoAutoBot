from cryptoauto import report


def _t(pnl, gross=None, fees=0.0, reason="tp", **kw):
    base = {
        "symbol": "SOL", "qty": 1.0, "entry_price": 100.0, "exit_price": 100.0 + pnl,
        "initial_stop": 90.0, "reason": reason, "pnl": pnl,
        "pnl_gross": gross if gross is not None else pnl, "fees": fees,
    }
    base.update(kw)
    return base


def test_performance_reports_net_gross_and_the_fee_bill():
    trades = [_t(20.0, gross=21.0, fees=1.0), _t(-10.0, gross=-9.0, fees=1.0, reason="stop")]
    p = report.performance(trades)
    assert p["net_pnl"] == 10.0
    assert p["gross_pnl"] == 12.0
    assert p["fees"] == 2.0
    assert p["fee_drag_pct"] == 2.0 / 12.0 * 100


def test_performance_computes_profit_factor_and_payoff():
    trades = [_t(30.0), _t(30.0), _t(-20.0, reason="stop")]
    p = report.performance(trades)
    assert p["profit_factor"] == 3.0          # 60 won / 20 lost
    assert p["payoff"] == 1.5                 # avg win 30 / avg loss 20
    assert p["expectancy"] == 40.0 / 3


def test_performance_flags_concentration_of_profit():
    """Five trades carrying ~all the profit is the fragility signal."""
    trades = [_t(100.0), _t(1.0), _t(1.0), _t(1.0), _t(-1.0, reason="stop")]
    p = report.performance(trades)
    assert p["top1_share_pct"] == 100.0 / 102.0 * 100
    assert p["without_top3"] == 102.0 - 102.0


def test_performance_handles_empty_book():
    p = report.performance([])
    assert p["trades"] == 0
    assert p["net_pnl"] == 0.0
    assert p["profit_factor"] is None
    assert p["payoff"] is None


def test_performance_handles_a_book_with_no_losers():
    p = report.performance([_t(10.0), _t(5.0)])
    assert p["profit_factor"] is None          # undefined, not infinity
    assert p["net_pnl"] == 15.0


def test_render_carries_the_doctrine_beside_the_headline():
    """A win rate must never be printed without the true rate next to it."""
    trades = [_t(20.0, reason="tp"), _t(5.0, reason="stop")]
    out = report.render(trades)
    assert "true win rate" in out
    assert "stop rate" in out
    assert "fees" in out.lower()


def test_render_handles_empty_book():
    assert "no closed trades" in report.render([]).lower()
