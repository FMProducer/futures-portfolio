from futures_portfolio.backtest.dashboard_attribution import reconstruct_attribution


def test_reconstruct_mid_pnl_slippage_and_commissions():
    closes = [100.0, 110.0, 120.0]
    equity = [1000.0, 1089.5, 1179.0]
    fills = [
        {"bar": 0, "side": "BUY", "leg": "BASE_LONG", "qty": 10.0, "price": 101.0, "mid": 100.0, "commission": 0.5},
        {"bar": 2, "side": "SELL", "leg": "BASE_LONG", "qty": 10.0, "price": 119.0, "mid": 120.0, "commission": 0.5},
    ]
    out = reconstruct_attribution(closes, equity, fills, initial_capital=1000.0)
    totals = out["totals"]
    assert totals["realized"] == 200.0
    assert totals["slippage"] == 20.0
    assert totals["commissions"] == 1.0
    assert totals["unrealized"] == 0.0
    assert abs(totals["residual"]) < 1e-9
    assert out["series"]["net"][-2] == 89.5


def test_cooldown_marks_open_positions_to_market():
    closes = [100.0, 90.0]
    equity = [1000.0, 900.0]
    fills = [
        {"bar": 0, "side": "BUY", "leg": "BASE_LONG", "qty": 10.0, "price": 100.0, "mid": 100.0, "commission": 0.0},
    ]
    events = [
        {"bar": 1, "kind": "cooldown_start", "until_bar": 5},
    ]
    out = reconstruct_attribution(closes, equity, fills, initial_capital=1000.0, events=events)
    assert out["totals"]["realized"] == -100.0
    assert out["totals"]["unrealized"] == 0.0
    assert abs(out["totals"]["residual"]) < 1e-9
