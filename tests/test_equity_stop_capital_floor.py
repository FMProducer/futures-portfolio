import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _find_floor_guard(tree, variable):
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            if (isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Name)
                    and sub.left.id == variable and sub.ops and isinstance(sub.ops[0], ast.Lt)):
                return node
    raise AssertionError(f"capital floor guard for {variable} not found")


@pytest.mark.parametrize("mode", ["backtest", "paper", "real"])
@pytest.mark.parametrize("tpv,eligible", [(999.99, False), (1000.0, True), (1000.01, True)])
def test_equity_stop_floor_and_pending_timer(mode, tpv, eligible):
    path = "backtest/backtest_rebalance.py" if mode == "backtest" else "core/main.py"
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    variable = "tpv_f" if mode == "backtest" else "tpv_active"
    guard = _find_floor_guard(tree, variable)
    # Execute the production floor and timer reset without entering order execution.
    guard.orelse = []  # the elif chain is order execution; only the if-body is under test
    if mode == "backtest":
        namespace = dict(tpv_f=tpv, initial_capital=1000.0,
                         trailing_stop_activation_pct=1, trailing_stop_pct=0,
                         state=SimpleNamespace(trailing_stop_violation_start=123.0, tpv_ath=0.0))
        exec(compile(ast.Module(body=[guard], type_ignores=[]), path, "exec"), namespace)
        assert namespace["state"].trailing_stop_violation_start == (123.0 if eligible else 0.0)
        return
    namespace = dict(tpv_active=tpv, paper_mode=(mode == "paper"),
                     portfolio_cfg={"initial_capital": 2000.0 if mode == "paper" else 1000.0,
                                    "paper_initial_capital": 1000.0 if mode == "paper" else 500.0},
                     equity_trailing_stop_activation_pct=1, equity_trailing_stop_pct=0, tpv_ath=0.0,
                     state={"trailing_stop_violation_start": 123.0}, state_dirty=False)
    exec(compile(ast.Module(body=[guard], type_ignores=[]), path, "exec"), namespace)
    assert namespace["state"]["trailing_stop_violation_start"] == (123.0 if eligible else 0.0)
    assert namespace["state_dirty"] is (not eligible)
