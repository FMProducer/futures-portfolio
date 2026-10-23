import json

import pandas as pd
import pytest
from decimal import Decimal

from core.calculator import PortfolioCalculator, book_vwap
from backtest.backtest_rebalance import run_backtest


def test_book_vwap():
    assert book_vwap([(Decimal("10"), Decimal("1")), (Decimal("9"), Decimal("1"))], Decimal("2")) == Decimal("9.5")
    assert book_vwap([(Decimal("10"), Decimal("1"))], Decimal("2")) is None
    assert book_vwap([], Decimal("1")) is None
    assert book_vwap([(Decimal("10"), Decimal("5"))], Decimal("0")) is None


@pytest.mark.parametrize("fill,allowed", [
    ({"bids": [(Decimal("100.2"), Decimal("8"))], "taker": None}, False),
    ({"bids": [(Decimal("99.9"), Decimal("8"))], "taker": Decimal(".0004")}, False),
    ({"bids": [(Decimal("100.2"), Decimal("8"))], "taker": Decimal(".0004")}, True),
    ({"bids": [(Decimal("100.2"), Decimal("1"))], "taker": Decimal(".0004")}, False),
])
def test_fuse_live_fill_gate_blocks_unconfirmed_or_unprofitable(fill, allowed):
    targets = {
        "BASE_LONG": {"share": .35, "leverage": 1},
        "BASE_SHORT": {"share": .35, "leverage": 1},
        "VIRTUAL": {"share": .30, "leverage": 1},
    }
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 8}, spot_price=102,
        real_equity=800, virt_qty=0, virt_debt=200,
        long_entry_price=100, short_entry_price=100, targets=targets,
        last_rebalance_price=90,
        fuse_fill={**fill, "asks": []},
    )
    actions = calc.calculate_rebalance(targets, .02, .04, calc.tpv)["actions"]
    assert any(a["key"] == "BASE_LONG" and a["is_reduction"] for a in actions) is allowed


def test_fuse_fill_validation():
    targets = {"BASE_LONG": {"share": .35, "leverage": 1},
               "BASE_SHORT": {"share": .35, "leverage": 1},
               "VIRTUAL": {"share": .30, "leverage": 1}}
    for bad in ({"bids": [(Decimal("-1"), Decimal("1"))]},
                {"bids": [], "taker": Decimal("1.0")},
                {"asks": [(Decimal("1"), Decimal("-5"))]}):
        with pytest.raises(ValueError):
            PortfolioCalculator(positions={}, spot_price=100, real_equity=100, virt_qty=0,
                                targets=targets, fuse_fill=bad)


@pytest.mark.parametrize("leg", ["LONG", "SHORT"])
@pytest.mark.parametrize("edge,allowed", [(0.0003, False), (0.0007, False), (0.002, True)])
@pytest.mark.parametrize("same_rebalance_price", [False, True])
def test_fuse_requires_positive_net_close(leg, edge, allowed, same_rebalance_price):
    price = 100 * (1 + edge if leg == "LONG" else 1 - edge)
    targets = {
        "BASE_LONG": {"share": .35, "leverage": 1},
        "BASE_SHORT": {"share": .35, "leverage": 1},
        "VIRTUAL": {"share": .30, "leverage": 1},
    }
    calc = PortfolioCalculator(
        positions={f"BTCUSDT_{leg}": 8}, spot_price=price,
        real_equity=800, virt_qty=0, virt_debt=200,
        long_entry_price=100, short_entry_price=100, targets=targets,
        last_rebalance_price=price if same_rebalance_price else (90 if leg == "LONG" else 110),
        commission_pct=.0004, slippage_pct=.0005,
    )
    actions = calc.calculate_rebalance(targets, .02, .04, calc.tpv)["actions"]
    assert any(a["key"] == f"BASE_{leg}" and a["is_reduction"] for a in actions) is allowed
    disabled = calc.calculate_rebalance(targets, .02, .04, calc.tpv, fuse_enabled=False)["actions"]
    assert any(a["key"] == f"BASE_{leg}" and a["is_reduction"] for a in disabled)


@pytest.mark.parametrize("leg", ["LONG", "SHORT"])
def test_fuse_rejects_exact_net_breakeven(leg):
    targets = {"BASE_LONG": {"share": .35, "leverage": 1},
               "BASE_SHORT": {"share": .35, "leverage": 1},
               "VIRTUAL": {"share": .30, "leverage": 1}}
    calc = PortfolioCalculator(
        positions={f"BTCUSDT_{leg}": 8}, spot_price=100,
        real_equity=800, virt_qty=0, virt_debt=200, targets=targets,
        long_entry_price=99.9, short_entry_price=100.1,
        last_rebalance_price=90 if leg == "LONG" else 110,
        commission_pct=.001,
    )
    assert not any(a["key"] == f"BASE_{leg}" and a["is_reduction"]
                   for a in calc.calculate_rebalance(targets, .02, .04, calc.tpv)["actions"])


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
