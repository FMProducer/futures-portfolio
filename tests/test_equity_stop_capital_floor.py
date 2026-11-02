import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["backtest", "paper", "real"])
@pytest.mark.parametrize("tpv,eligible", [(999.99, False), (1000.0, True), (1000.01, True)])
def test_equity_stop_floor_and_pending_timer(mode, tpv, eligible):
    path = "backtest/backtest_rebalance.py" if mode == "backtest" else "core/main.py"
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    variable = "tpv_f" if mode == "backtest" else "tpv_active"
    guard = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                 and isinstance(node.test.left, ast.Name) and node.test.left.id == variable
                 and isinstance(node.test.ops[0], ast.Lt))
    # Execute the production floor and timer reset without entering order execution.
    guard.orelse = ast.parse("eligible = True").body
    code = compile(ast.fix_missing_locations(ast.Module(body=[guard], type_ignores=[])), path, "exec")
    state = (SimpleNamespace(trailing_stop_violation_start=123.0) if mode == "backtest"
             else {"trailing_stop_violation_start": 123.0})
    namespace = dict(tpv_f=tpv, tpv_active=tpv, initial_capital=1000.0,
                     paper_mode=mode == "paper", state=state, state_dirty=False, eligible=False,
                     portfolio_cfg={"initial_capital": 2000.0 if mode == "paper" else 1000.0,
                                    "paper_initial_capital": 1000.0 if mode == "paper" else 500.0})
    exec(code, namespace)
    assert namespace["eligible"] is eligible
    timer = state.trailing_stop_violation_start if mode == "backtest" else state["trailing_stop_violation_start"]
    assert timer == (123.0 if eligible else 0.0)
    if mode != "backtest":
        assert namespace["state_dirty"] is (not eligible)
