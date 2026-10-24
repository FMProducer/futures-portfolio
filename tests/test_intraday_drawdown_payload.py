import json

import pandas as pd
import pytest

from scripts import visualize_zec_dashboard as dashboard


def test_dashboard_drawdown_includes_initial_costs_and_later_peak(tmp_path, monkeypatch):
    equity = [990.0, 980.0, 1010.0, 1000.0]
    result = {"ticker": "AUDITUSDT", "initial_capital": 1000.0, "profit_pct": 0.0,
              "max_dd_pct": 2.0, "data_window": {"feather": "AUDITUSDT_live.feather", "offset": 0}}
    (tmp_path / "audit_backtest_result.json").write_text(json.dumps(result), encoding="utf-8")
    (tmp_path / "audit_equity.json").write_text(json.dumps(equity), encoding="utf-8")
    pd.DataFrame({"time": pd.date_range("2026-07-26", periods=4, freq="min", tz="UTC"),
                  "open": [100.0] * 4, "high": [100.0] * 4, "low": [100.0] * 4,
                  "close": [100.0] * 4, "volume": [1.0] * 4}).to_feather(tmp_path / "AUDITUSDT_live.feather")
    monkeypatch.setattr(dashboard, "REPORTS", str(tmp_path))
    monkeypatch.setattr(dashboard, "DATA", str(tmp_path))
    payload = dashboard.build_payload("audit")
    assert payload["drawdown"]["values"] == pytest.approx([1.0, 2.0, 0.0, 0.9901], abs=1e-4)
