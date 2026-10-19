
import pytest
from decimal import Decimal
from futures_portfolio.core.calculator import PortfolioCalculator

def test_rebalance_logic_v378():
    # Targets based on User's model
    targets = {
        "BASE_LONG": {"share": 0.29, "leverage": 5},
        "BASE_SHORT": {"share": 0.36, "leverage": 5},
        "VIRTUAL": {"share": 0.35, "leverage": 1}
    }
    initial_cap = 100.0
    threshold = 0.035 # 3.5%
    
    print("\n--- CASE 1: Perfect Balance ---")
    # Equity: L=29, S=36, V=35, C=0. TPV=100.
    # Prices match entry to have 0 PnL
    # Qty * Price / Lev = 145*1/5 = 29
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 145.0, "BTCUSDT_SHORT": -180.0},
        spot_price=1.0,
        real_equity=65.0, # 29 (L margin) + 36 (S margin) + 0 (Cash)
        virt_qty=0.0,
        virt_debt=35.0,  # VIRTUAL = fixed USDT
        long_entry_price=1.0,
        short_entry_price=1.0,
        base_ticker="BTCUSDT",
        initial_capital=initial_cap,
        targets=targets
    )
    res = calc.calculate_rebalance(targets, threshold, threshold, current_equity=calc.tpv)
    print(f"DEBUG: L:{res['share_long_pct']}% S:{res['share_short_pct']}% V:{res['share_virt_pct']}% C:{res['share_cash_pct']}%")
    if res["actions"]:
        print(f"DEBUG Actions: {res['actions']}")
    assert len(res["actions"]) == 0
    assert res["share_long_pct"] == 29.0
    assert res["share_short_pct"] == 36.0
    assert res["share_virt_pct"] == 35.0
    assert res["share_cash_pct"] == 0.0
    assert res["tpv"] == 100.0

    print("--- CASE 2: LONG Surplus (Sell to Cash) ---")
    # Price rises to 1.2 (+20%)
    # Long: 145 * (1.2 - 1.0) = 29 PnL, margin = 29 -> equity = 29 + 29 = 58
    # Short: -180 * (1.2 - 1.0) = -36 PnL, margin = 36 -> equity = 36 - 36 = 0
    # VIRTUAL = fixed USDT = 35 (no revaluation)
    # Cash: 0
    # TPV: 58 + 0 + 35 = 93 (not market-neutral anymore!)
    # Shares: L:62.4%, S:0%, V:37.6%
    # Long exceeds threshold: abs(62.4 - 29.0) = 33.4% > 3.5% -> SELL
    # VIRTUAL: abs(37.6 - 35.0) = 2.6% < 3.5% -> NO TRIGGER

    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 145.0, "BTCUSDT_SHORT": -180.0},
        spot_price=1.2,
        real_equity=65.0, # base margin (29 + 36)
        virt_qty=0.0,
        virt_debt=35.0,  # fixed USDT
        long_entry_price=1.0,
        short_entry_price=1.0,
        base_ticker="BTCUSDT",
        initial_capital=initial_cap,
        targets=targets,
        last_rebalance_price=1.0
    )
    # TPV: real_equity=65 + L_pnl=29 + S_pnl=-36 + virt_value=35 = 93
    # L val = 29 + 29 = 58 (margin + pnl)
    # S val = 36 - 36 = 0
    # V val = 35 (fixed)
    # TPV = 58 + 0 + 35 = 93
    # L share = 58/93 = 62.4%, S = 0%, V = 35/93 = 37.6%
    # L dev = +33.4% (breach), V dev = 37.6-35 = +2.6% (NO breach)

    res = calc.calculate_rebalance(targets, threshold, threshold, current_equity=calc.tpv)
    print(f"DEBUG Case 2: L:{res['share_long_pct']}% S:{res['share_short_pct']}% V:{res['share_virt_pct']}% C:{res['share_cash_pct']}%")
    actions = res["actions"]
    if actions:
        print(f"DEBUG Actions Case 2: {actions}")

    # Check: only LONG surplus should trigger (Priority 0 SELL)
    assert any(a["key"] == "BASE_LONG" and a["priority"] == 0 for a in actions)
    # VIRTUAL should NOT trigger (37.6% - 35% = 2.6% < 3.5%)
    assert not any(a["key"] == "VIRTUAL" and a["priority"] == 0 for a in actions)
    assert any(a["key"] == "BASE_SHORT" and a["priority"] == 2 for a in actions)
    
    # Sort order check (SELLs first)
    assert actions[0]["priority"] == 0
    assert actions[-1]["priority"] == 2

    print("--- CASE 3: Cash Guard (Insufficient funds for Short) ---")
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 145.0, "BTCUSDT_SHORT": -160.0},
        spot_price=1.0,
        real_equity=62.0, # 29 (L margin) + 32 (S margin) + 1 (cash)
        virt_qty=0.0,
        virt_debt=34.0,
        long_entry_price=1.0,
        short_entry_price=1.0,
        base_ticker="BTCUSDT",
        initial_capital=initial_cap,
        targets=targets,
        last_rebalance_price=1.0
    )
    calc.val_cash = Decimal('1.0') # Force low cash
    calc.tpv = calc.val_long + calc.val_short + calc.val_virt + calc.val_cash

    res = calc.calculate_rebalance(targets, threshold, threshold, current_equity=calc.tpv)
    assert len(res["actions"]) == 0 

    print("--- CASE 4: Priority BUY (Virtual gets cash first) ---")
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 145.0, "BTCUSDT_SHORT": -100.0}, # Short deficit: 100*1/5 = 20 (Target 36)
        spot_price=1.0,
        real_equity=50.0, # (29 L margin) + (20 S margin) + (1 Cash)
        virt_qty=0.0,
        virt_debt=20.0,  # fixed USDT
        long_entry_price=1.0,
        short_entry_price=1.0,
        base_ticker="BTCUSDT",
        initial_capital=initial_cap,
        targets=targets,
        last_rebalance_price=1.0
    )
    # TPV = 50 (real_equity) + 0 (PnL) + 20 (V) = 70.
    # Needs to be TPV=100 for easy math.
    calc.tpv = Decimal('100.0')
    calc.val_long = Decimal('29.0')
    calc.val_short = Decimal('20.0') # Deficit 16
    calc.val_virt = Decimal('20.0')  # Deficit 15
    calc.val_cash = Decimal('31.0') # Total sum = 100

    # Force deviations > 3.5%
    calc.share_long_raw = Decimal('0.29')
    calc.share_short_raw = Decimal('0.20')
    calc.share_virt_raw = Decimal('0.20')

    # Available Cash is 7. V needs 15, S needs 16.
    calc.val_cash = Decimal('7.0')
    res = calc.calculate_rebalance(targets, threshold, threshold, current_equity=calc.tpv)
    actions = res["actions"]
    print(f"DEBUG Case 4 Shares: L:{res['share_long_pct']}% S:{res['share_short_pct']}% V:{res['share_virt_pct']}% C:{res['share_cash_pct']}%")
    print(f"DEBUG Case 4 Actions: {actions}")

    # V should get 7.0 (partial, but first). S should get 0 (no money left).
    v_act = next(a for a in actions if a["key"] == "VIRTUAL")
    assert v_act["diff_usdt"] == 7.0
    assert not any(a["key"] == "BASE_SHORT" for a in actions)
    
    print("--- CASE 5: Heartbeat Summation ---")
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 100.0, "BTCUSDT_SHORT": -50.0},
        spot_price=1.2,
        real_equity=50.0,
        virt_qty=0.0,
        virt_debt=10.0,  # fixed USDT
        long_entry_price=1.0,
        short_entry_price=1.0,
        base_ticker="BTCUSDT",
        initial_capital=100.0,
        targets=targets
    )
    res = calc.calculate_rebalance(targets, threshold, threshold, current_equity=calc.tpv)
    sum_vals = res["val_long"] + res["val_short"] + res["val_virt"] + res["val_cash"]
    assert abs(sum_vals - res["tpv"]) < 0.001
    
    print("\n✅ ALL TESTS PASSED!")

if __name__ == "__main__":
    test_rebalance_logic_v378()
