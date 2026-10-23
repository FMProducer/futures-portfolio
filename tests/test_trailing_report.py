import importlib.util
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def report(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "experiments/trailing_share/report_tables.py"
    spec = importlib.util.spec_from_file_location("trailing_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CLOSES", np.array([100., 100.]))
    return module


def test_last_bar_fill_and_close_costs(report):
    res = {"equity_curve": [1000., 1000.], "fills": [
        {"bar": 1, "leg": "BASE_LONG", "side": "BUY", "qty": 2., "price": 100.}]}
    result = report.exposure_and_close(res, 1, 1)
    assert result["final_open"]["BASE_LONG"]["qty"] == 2.
    assert result["max_gross_exposure_eq"] == .2
    assert result["final_close_costs"] == pytest.approx(.17996)


def test_final_close_is_not_left_open(report):
    res = {"equity_curve": [1000., 1000.], "fills": [
        {"bar": 0, "leg": "BASE_LONG", "side": "BUY", "qty": 2., "price": 100.},
        {"bar": 1, "leg": "BASE_LONG", "side": "SELL", "qty": 2., "price": 100.}]}
    result = report.exposure_and_close(res, 1, 1)
    assert result["final_open"] == {}
    assert result["final_close_costs"] == 0


def test_fill_replay_detects_equity_corruption(report):
    res = {"initial_capital": 1000., "final_equity": 999.92, "equity_curve": [999.92, 999.92],
           "data_window": {"offset": 0}, "settings": {"funding_rate_per_day": 0},
           "fills": [{"bar": 0, "leg": "BASE_LONG", "side": "BUY", "qty": 2.,
                      "price": 100., "mid": 100., "commission": .08}]}
    assert report.replay_audit(res)["status"] == "pass"
    res["equity_curve"] = [1000., 1000.]
    assert report.replay_audit(res)["status"] == "fail"
