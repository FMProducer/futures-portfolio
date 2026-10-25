import json

from futures_portfolio.dashboard.swarm_history import reconstruct


def test_history_deduplicates_archives_and_preserves_capital_across_reset(tmp_path):
    history = tmp_path / "history"
    core = tmp_path / "core"
    monitor = tmp_path / "reports/paper_swarm_monitor_12h"
    for path in (history, core, monitor):
        path.mkdir(parents=True, exist_ok=True)
    opening = {"base_ticker": "TUSDT", "started_at": 100, "last_update": 101,
               "initial_tpv": 1000, "last_tpv": 998, "rebalance_cycles": 1}
    (monitor / "20261025T000000Z.json").write_text(json.dumps({"states": [dict(opening, ticker="TUSDT")]}))
    terminal = dict(opening, last_update=200, initial_tpv=1100, last_tpv=1100, rebalance_cycles=4)
    wallet = {"balance": 1100, "positions": {"TUSDT_LONG": 0},
              "share_fill_receipts": {"fill1": {"trade_pnl": 102, "commission": 2}}}
    (history / "archive_TUSDT_200.json").write_text(json.dumps({"state": terminal, "paper_state": wallet}))
    (history / "archive_TUSDT_201_paper_state_TUSDT.json").write_text(json.dumps(terminal))
    (history / "archive_TUSDT_201_paper_shadow_TUSDT.json").write_text(json.dumps(wallet))
    current = dict(opening, started_at=300, last_update=350, last_tpv=990)
    (core / "paper_state_TUSDT.json").write_text(json.dumps(current))
    result = reconstruct(tmp_path, {"paper-t"})
    assert result["session_count"] == 2
    assert result["reported_closed_net"] == 100
    assert result["open_net"] == -10
    assert result["reported_cumulative_net"] == 90
    assert result["fees_recorded"] == 2
    assert result["capital_allocated"] == 2000
    assert result["sessions"][0]["wallet_receipt_residual"] == 0
    assert result["verified_net"] is None


def test_incomplete_endpoint_is_not_presented_as_verified_closed_net(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    state = {"base_ticker": "TUSDT", "started_at": 100, "initial_tpv": 1000,
             "last_tpv": 1050, "rebalance_cycles": 2}
    (history / "archive_TUSDT_200_paper_state_TUSDT.json").write_text(json.dumps(state))
    (history / "broken.json").write_text("{")
    result = reconstruct(tmp_path)
    assert result["reported_closed_net"] == 0
    assert result["interrupted_net"] == 50
    assert result["sessions"][0]["fees_recorded"] is None
    assert result["verified_net"] is None
    assert result["errors"]
