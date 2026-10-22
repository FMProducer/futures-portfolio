import json

import pandas as pd
import pytest

from core.calculator import PortfolioCalculator
from backtest.backtest_rebalance import run_backtest


@pytest.mark.parametrize("leg,price", [("LONG", 101), ("SHORT", 99)])
def test_fuse_switch_preserves_thresholds_and_other_guards(leg, price):
    targets = {
        "BASE_LONG": {"share": 0.35, "leverage": 1},
        "BASE_SHORT": {"share": 0.35, "leverage": 1},
        "VIRTUAL": {"share": 0.30, "leverage": 1},
    }
    calc = PortfolioCalculator(
        positions={f"BTCUSDT_{leg}": 8}, spot_price=price,
        real_equity=800, virt_qty=0, virt_debt=200,
        long_entry_price=100, short_entry_price=100,
        last_rebalance_price=100, targets=targets,
    )
    default = calc.calculate_rebalance(targets, .02, .04, calc.tpv)
    enabled = calc.calculate_rebalance(targets, .02, .04, calc.tpv, fuse_enabled=True)
    assert default == enabled
    assert not any(a["is_reduction"] for a in enabled["actions"])
    disabled = calc.calculate_rebalance(targets, .02, .04, calc.tpv, fuse_enabled=False)
    assert any(a["key"] == f"BASE_{leg}" and a["is_reduction"] for a in disabled["actions"])
    for option in ({"allow_surplus_sell": False}, {"force_block": True}):
        guarded = calc.calculate_rebalance(targets, .02, .04, calc.tpv, fuse_enabled=False, **option)
        assert not any(a["is_reduction"] for a in guarded["actions"])
    assert not calc.calculate_rebalance(targets, .99, .99, calc.tpv, fuse_enabled=False)["actions"]
    for invalid in ("false", "true", 0, 1, None):
        with pytest.raises(ValueError, match="fuse_enabled must be a boolean"):
            calc.calculate_rebalance(targets, .02, .04, calc.tpv, fuse_enabled=invalid)


@pytest.mark.asyncio
async def test_backtest_reads_and_records_fuse_switch(tmp_path, caplog):
    prices = [100., 101., 99., 102., 98.]
    pd.DataFrame({k: prices for k in ("open", "high", "low", "close", "volume")}).to_feather(
        tmp_path / "BTCUSDT_live.feather"
    )
    portfolio = {
        "initial_capital": 1000,
        "targets": {
            "BASE_LONG": {"share": .35, "leverage": 7},
            "BASE_SHORT": {"share": .35, "leverage": 7},
            "VIRTUAL": {"share": .30, "leverage": 1},
        },
        "rebalance_threshold_surplus": .02,
        "rebalance_threshold_deficit": .04,
        "safety_guards": {"max_price_velocity_pct": 100},
    }
    path = tmp_path / "config.json"
    results = {}
    for switch in (None, True, False, "false"):
        if switch is not None:
            portfolio["fuse_enabled"] = switch
        path.write_text(json.dumps({"portfolios": [portfolio]}), encoding="utf-8")
        result = await run_backtest(str(path), str(tmp_path), days=1)
        if switch == "false":
            assert result is None
            assert "fuse_enabled must be a boolean" in caplog.text
        else:
            assert result is not None
            assert result["settings"]["fuse_enabled"] is (switch is not False)
            assert result["accounting_ok"]
            results[switch] = result
    assert results[None]["equity_curve"] == results[True]["equity_curve"]
    assert results[False]["cycles"] > results[True]["cycles"]
