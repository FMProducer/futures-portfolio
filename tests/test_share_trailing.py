import asyncio
import json
import os
from decimal import Decimal

import pandas as pd
import pytest

from core import share_trail
from core.calculator import PortfolioCalculator


def make_state():
    return share_trail.fresh_state()


def test_receipt_survives_restart_and_rejects_changed_cumulative_fill():
    fill = dict(execution_id="binance:ZECUSDT:123", symbol="ZECUSDT_LONG", type="LONG",
                side="BUY", qty=0.1, price=100, commission=0.004, trade_pnl=0, reduce_only=False)
    book = {"positions": {"ZECUSDT_LONG": 0.1}, "balance": 999.996}
    assert share_trail.fill_receipt(book, fill)
    share_trail.fill_receipt(book, fill, record=True)
    restored = json.loads(json.dumps(book))
    assert not share_trail.fill_receipt(restored, dict(fill))
    assert restored == book
    with pytest.raises(ValueError, match="reconciliation"):
        share_trail.fill_receipt(restored, dict(fill, qty=0.2))
    with pytest.raises(ValueError, match="identity"):
        share_trail.fill_receipt(restored, dict(fill, execution_id=None))


def test_restart_after_book_save_recovers_consumed_trail(tmp_path):
    trail = make_state()
    share_trail.consume(trail, "BASE_LONG", "ADD")
    spec = {"distance": 0.01}
    book = {"share_fill_receipts": {"paper:abc": {}}, "share_trail_checkpoint":
            {"execution_id": "paper:abc", "spec": spec, "trail": trail}}
    path = tmp_path / "paper.json"
    path.write_text(json.dumps(book))
    state = {"share_trail_spec": spec, "share_trail": make_state()}
    assert share_trail.restore_checkpoint(state, json.loads(path.read_text()))
    assert state["share_trail"]["BASE_LONG"]["trough"] is None
    assert not share_trail.restore_checkpoint(state, book)


def test_checkpoint_does_not_override_changed_spec():
    state = {"share_trail_spec": {"distance": .02}, "share_trail": make_state()}
    trail = make_state()
    share_trail.consume(trail, "BASE_LONG", "ADD")
    book = {"share_fill_receipts": {"id": {}}, "share_trail_checkpoint":
            {"execution_id": "id", "spec": {"distance": .01}, "trail": trail}}
    share_trail.restore_checkpoint(state, book)
    assert state["share_trail"] == make_state()


def test_equity_stop_posts_closing_fills_and_costs(tmp_path):
    cfg = _synthetic_config(tmp_path, "stop.json", distance=.01,
                            portfolio_over={"targets": {"BASE_LONG": {"share": .7, "leverage": 1},
                                                       "BASE_SHORT": {"share": .3, "leverage": 1},
                                                       "VIRTUAL": {"share": 0., "leverage": 1}}},
                            root_over={"equity_trailing_stop_activation_pct": .01,
                                       "equity_trailing_stop_pct": .1,
                                       "equity_trailing_stop_timeout_sec": 60,
                                       "cooldown_bars": 1000})
    prices = [100.] * 10 + list(range(101, 116)) + [114., 113., 112., 111.] * 3
    result = _run(tmp_path, cfg, prices)
    stops = [e for e in result["events"] if e["kind"] == "trailing_stop"]
    assert stops
    for stop in stops:
        fills = [f for f in result["fills"] if f["bar"] == stop["bar"] and
                 f["trigger"] == "equity_trailing_stop"]
        assert fills and all(f["commission"] > 0 for f in fills)
    assert result["accounting_ok"]


class TestPullbackRebound:
    @pytest.mark.parametrize("key", ["BASE_LONG", "BASE_SHORT"])
    def test_user_example_and_mirrored_add(self, key):
        st = make_state()
        def observe(value):
            return share_trail.step(st,key,value,".35",".36",".295",".005")[:2]
        assert observe(".35") == (False,False)
        assert observe(".36") == (False,False)
        assert observe(".38") == (False,False)
        assert observe(".375001") == (False,False)
        assert observe(".375") == (True,False)
        assert Decimal(".375") - Decimal(".35") == Decimal(".025")
        share_trail.consume(st,key,"REDUCE")
        assert observe(".375") == (False,False)
        assert observe(".295") == (False,False)
        assert observe(".27") == (False,False)
        assert observe(".274999") == (False,False)
        assert observe(".275") == (False,True)
        share_trail.consume(st,key,"ADD")
        assert observe(".275") == (False,False)

    def test_calculator_sizes_current_excess_on_signal(self):
        st = make_state()
        targets = {"BASE_LONG":{"share":.35,"leverage":7},
                   "BASE_SHORT":{"share":.35,"leverage":7},
                   "VIRTUAL":{"share":.3,"leverage":1}}
        for share in (".35", ".36", ".38", ".375"):
            calc = PortfolioCalculator(
                positions={"SYNTZ_LONG":Decimal(share)*70,"SYNTZ_SHORT":Decimal("24.5")},
                spot_price=100,real_equity=700,virt_qty=0,virt_debt=300,
                long_entry_price=100,short_entry_price=100,base_ticker="SYNTZ",
                targets=targets,initial_capital=1000)
            result = calc.calculate_rebalance(targets,.01,.055,Decimal(1000),
                     fuse_enabled=False,share_trail=st,trail_distance=.005)
            actions = [a for a in result["actions"] if a["key"] == "BASE_LONG"]
            if share != ".375":
                assert not actions
            else:
                assert len(actions) == 1 and actions[0]["is_reduction"]
                assert Decimal(str(actions[0]["diff_usdt"])) == Decimal("-175")

    def test_no_wrong_direction_or_flat_signal(self):
        st = make_state()
        share_trail.step(st,"BASE_LONG",".38",".35",".36",".295",".005")
        assert not share_trail.step(st,"BASE_LONG",".34",".35",".36",".295",".005")[0]
        assert share_trail.step(st,"BASE_LONG",0,".35",".36",".295",".005",flat=True)[2] == "FLAT"
        assert st == make_state()


class TestSafeLoad:
    def test_missing_state_is_fresh(self):
        st = share_trail.load_state(None)
        assert st == make_state()

    def test_corrupt_rows_fall_back(self):
        st = share_trail.load_state({"BASE_LONG": {"peak": "0.61", "trough": "garbage", "wait_red": "x"},
                                     "BASE_SHORT": {"peak": None, "trough": "0.44", "wait_add": 1}})
        assert Decimal(st["BASE_LONG"]["peak"]) == Decimal("0.61")
        assert st["BASE_LONG"]["trough"] is None
        assert Decimal(st["BASE_SHORT"]["trough"]) == Decimal("0.44")
        assert set(st["BASE_LONG"]) == {"peak", "trough"}

    def test_spec_mismatch(self):
        spec = share_trail.spec_fingerprint(0.02, {"BASE_LONG": {"share": 0.5}, "BASE_SHORT": {"share": 0.5}}, 0.05, 0.05)
        assert share_trail.spec_mismatch(None, spec) is False
        assert share_trail.spec_mismatch(spec, spec) is False
        changed = dict(spec, distance=0.03)
        assert share_trail.spec_mismatch(spec, changed) is True


class TestCalculatorZeroTrailEqualsBaseline:
    def _calc(self, price, trail, distance):
        positions = {"SYNTZ_LONG": 10.0, "SYNTZ_SHORT": 10.0}
        targets = {"BASE_LONG": {"share": 0.5, "leverage": 1},
                   "BASE_SHORT": {"share": 0.5, "leverage": 1},
                   "VIRTUAL": {"share": 0.0, "leverage": 1}}
        calc = PortfolioCalculator(
            positions=positions, spot_price=price, real_equity=100.0, virt_qty=0.0, virt_debt=0.0,
            long_entry_price=100.0, short_entry_price=100.0, base_ticker="SYNTZ",
            targets=targets, initial_capital=200.0, last_rebalance_price=price,
        )
        return calc.calculate_rebalance(targets, 0.05, 0.05, Decimal("200"), share_trail=trail, trail_distance=distance)

    def test_distance_zero_does_not_use_trail_state(self):
        # identical share conditions: long share above band -> baseline surplus action
        base = self._calc(110.0, None, 0.0)
        trailed = self._calc(110.0, make_state(), 0.0)
        assert [a["key"] for a in base["actions"]] == [a["key"] for a in trailed["actions"]]
        assert {a["key"]: a["diff_usdt"] for a in base["actions"]} == {a["key"]: a["diff_usdt"] for a in trailed["actions"]}


def _synthetic_config(tmp_path, name, distance=None, portfolio_over=None, root_over=None):
    cfg = {
        "base_ticker": "SYNTZ",
        "min_notional_usdt": 0.0,
        "toxic_cooldown_days": 0.001,
        "portfolios": [{
            "initial_capital": 1000.0,
            "targets": {
                "BASE_LONG": {"share": 0.5, "leverage": 1},
                "BASE_SHORT": {"share": 0.5, "leverage": 1},
                "VIRTUAL": {"share": 0.0, "leverage": 1},
            },
            "rebalance_threshold_surplus": 0.05,
            "rebalance_threshold_deficit": 0.05,
            "fuse_enabled": False,
        }],
    }
    if distance is not None:
        cfg["rebalance_share_trail_distance"] = distance
    if portfolio_over:
        cfg["portfolios"][0].update(portfolio_over)
    if root_over:
        cfg.update(root_over)
    path = tmp_path / name
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return str(path)


def _write_feather(tmp_path, name, closes, start="2026-01-01 00:00:00"):
    n = len(closes)
    times = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    df = pd.DataFrame({"time": times, "open": closes, "high": closes, "low": closes,
                       "close": closes, "volume": [1.0] * n})
    path = tmp_path / name
    df.to_feather(str(path))
    return str(path)


def _run(tmp_path, config_path, closes, ticker="SYNTZ"):
    from backtest.backtest_rebalance import run_backtest
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    feather = _write_feather(data_dir, f"{ticker}_live.feather", closes)
    import math
    days = max(1.0, math.ceil(len(closes) / 1440.0))
    res = asyncio.run(run_backtest(config_path, str(data_dir), days=days,
                                   ticker_override=ticker, quiet=True))
    assert res is not None
    return res


def _ramp(n_up, n_down, step=0.5, start=100.0):
    up = [start + step * i for i in range(1, n_up + 1)]
    down = [up[-1] - step * i for i in range(1, n_down + 1)]
    return up + down


class TestBacktestTrailing:
    @pytest.mark.parametrize("distance", [1e-12, .005, .02])
    def test_backtest_matches_independent_reference_on_price_ramp(self, tmp_path, monkeypatch, distance):
        from types import SimpleNamespace
        from tests.fixtures.share_trailing_t_invest_reference import reference, update_extrema
        import core.calculator as calculator
        prices = _ramp(120, 120, step=.2)
        cfg = _synthetic_config(tmp_path,"trail.json",distance=distance)
        actual = _run(tmp_path,cfg,prices)

        def oracle(state,key,share,target,upper,lower,distance,flat=False):
            if flat:
                share_trail.reset_leg(state,key)
                return False,False,"FLAT"
            row = state[key]
            if share >= upper:
                row["peak"] = str(max(Decimal(row["peak"] or str(share)),share))
            if share <= lower:
                row["trough"] = str(min(Decimal(row["trough"] or str(share)),share))
            sell = row["peak"] is not None and share <= Decimal(row["peak"])-Decimal(str(distance)) and share > target
            buy = row["trough"] is not None and share >= Decimal(row["trough"])+Decimal(str(distance)) and share < target
            return sell,buy,None

        monkeypatch.setattr(calculator,"trail_step",oracle)
        expected=_run(tmp_path,cfg,prices)
        assert actual["fills"] == expected["fills"]
        assert actual["equity_curve"] == expected["equity_curve"]

    def test_unchanged_price_no_infinite_trading(self, tmp_path):
        prices = [100.0] * 600
        cfg = _synthetic_config(tmp_path, "flat.json", distance=0.02)
        res = _run(tmp_path, cfg, prices)
        fills_after_formation = [f for f in res["fills"] if f["trigger"] == "rebalance" and f["bar"] > 5]
        assert len(fills_after_formation) <= 1

    def test_zero_distance_matches_baseline_equity_and_fills(self, tmp_path):
        prices = _ramp(120, 120, step=0.2)
        base = _run(tmp_path, _synthetic_config(tmp_path, "base.json"), prices)
        zero = _run(tmp_path, _synthetic_config(tmp_path, "zero.json", distance=0.0), prices)
        assert base["equity_curve"] == zero["equity_curve"]
        assert base["fill_count"] == zero["fill_count"]
        assert base["fills"] == zero["fills"]

    def test_force_block_observes_extremum_without_consuming_it(self):
        state=make_state()
        targets={"BASE_LONG":{"share":.35,"leverage":1},"BASE_SHORT":{"share":.35,"leverage":1},
                 "VIRTUAL":{"share":.3,"leverage":1}}
        calc=PortfolioCalculator(positions={"SYNTZ_LONG":2,"SYNTZ_SHORT":2},spot_price=100,
             real_equity=700,virt_qty=0,virt_debt=300,long_entry_price=100,short_entry_price=100,
             base_ticker="SYNTZ",targets=targets,initial_capital=1000)
        blocked=calc.calculate_rebalance(targets,.01,.055,Decimal(1000),force_block=True,
                  fuse_enabled=False,share_trail=state,trail_distance=.005)
        assert not blocked["actions"]
        assert Decimal(state["BASE_LONG"]["trough"]) == Decimal('.2')
        allowed=calc.calculate_rebalance(targets,.01,.055,Decimal(1000),fuse_enabled=False,
                   share_trail=state,trail_distance=.005)
        assert not any(a["key"] == "BASE_LONG" for a in allowed["actions"])

    def test_full_close_resets_trail_state(self, tmp_path):
        # Predictive liquidation guard (crit distance wide enough for a lev-1 hedge)
        # closes both legs and must reset the whole trailing state.
        prices = [100.0] * 10 + [99.0] * 50
        cfg = _synthetic_config(tmp_path, "liq.json", distance=0.02,
                               portfolio_over={"liquidation_distance_crit_pct": 99.9})
        res = _run(tmp_path, cfg, prices)
        assert res["liquidations"] > 0
        assert res["share_trail"] == make_state()
