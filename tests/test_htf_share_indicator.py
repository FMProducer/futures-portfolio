"""Virtual 5m portfolio share-skew indicator: stepwise LONG/SHORT/NEUTRAL target shifts."""
import json

import pandas as pd
import pytest

from futures_portfolio.backtest.backtest_rebalance import (
    apply_share_bias,
    run_backtest,
    share_signal_bias,
)
from test_backtest_rebalance import _flat_config

PROD_TARGETS = {
    "BASE_LONG": {"share": 0.25, "leverage": 3.0},
    "BASE_SHORT": {"share": 0.25, "leverage": 3.0},
    "VIRTUAL": {"share": 0.5, "leverage": 1.0},
}

SIGNAL_FIELDS = (
    "bar", "kind", "detail", "interval_start", "interval_end", "signal_available_at",
    "price_start", "price_end", "val_long", "val_short", "val_virt", "total",
    "w_long", "w_short", "excess_long_pp", "excess_short_pp", "threshold_pp",
    "signal", "bias_pp", "prev_bias_pp", "changed",
    "targets_long", "targets_short", "prev_long", "prev_short",
    "shares_after_long", "shares_after_short", "shares_after_virt", "first_decision_bar",
)


def _config(threshold=None, guards_off=False):
    config = _flat_config()
    portfolio = config["portfolios"][0]
    portfolio["targets"] = json.loads(json.dumps(PROD_TARGETS))
    if threshold is not None:
        portfolio["rebalance_threshold_surplus"] = threshold
        portfolio["rebalance_threshold_deficit"] = threshold
    if guards_off:
        portfolio["safety_guards"] = {
            "max_price_velocity_pct": 500.0, "velocity_window_sec": 3600,
            "net_move_block_pct": 500.0, "net_move_window_sec": 3600,
            "trend_min_move_pct": 999.0, "trend_eff_threshold": 999.0,
            "trend_lookback_bars": 30,
        }
    return config


def _write_prices(directory, prices, start="2026-10-10 00:00"):
    directory.mkdir(parents=True, exist_ok=True)
    times = pd.date_range(start, periods=len(prices), freq="min", tz="UTC")
    pd.DataFrame({
        "time": times,
        "open": prices, "high": prices, "low": prices, "close": prices,
        "volume": [1.0] * len(prices),
    }).to_feather(directory / "BTCUSDT_live.feather")


async def _run(directory, config, **kwargs):
    directory.mkdir(parents=True, exist_ok=True)
    config_file = directory / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    return await run_backtest(str(config_file), str(directory), live_mode=False, days=0.1,
                              commission=0.0, slippage=0.0, quiet=True, **kwargs)


def _signal_events(res):
    return [e for e in res["events"] if e["kind"] == "htf_share_signal"]


def _hand_values(p0, p_end, capital=1000.0, lev=3.0):
    # manual model: qty sized so leg value = 25% of capital at entry, value = margin + mtm
    qty = 0.25 * capital * lev / p0
    val_long = qty * (p0 / lev + p_end - p0)
    val_short = qty * (p0 / lev + p0 - p_end)
    total = val_long + val_short + 0.5 * capital
    return qty, val_long, val_short, total


@pytest.mark.parametrize("bias_pp, expected_long, expected_short", [
    (1, 0.26, 0.24),   # +1pp:  25/25/50 -> 26/24/50
    (-1, 0.24, 0.26),  # -1pp:  25/25/50 -> 24/26/50
    (2, 0.27, 0.23),   # +2pp:  25/25/50 -> 27/23/50
    (-2, 0.23, 0.27),  # -2pp:  25/25/50 -> 23/27/50
])
def test_apply_share_bias_percentage_points(bias_pp, expected_long, expected_short):
    biased = apply_share_bias(PROD_TARGETS, bias_pp)
    assert biased["BASE_LONG"]["share"] == pytest.approx(expected_long)
    assert biased["BASE_SHORT"]["share"] == pytest.approx(expected_short)
    assert biased["VIRTUAL"]["share"] == 0.5
    assert biased["BASE_LONG"]["share"] + biased["BASE_SHORT"]["share"] == pytest.approx(0.5)
    assert PROD_TARGETS["BASE_LONG"]["share"] == 0.25
    assert PROD_TARGETS["BASE_SHORT"]["share"] == 0.25


def test_apply_share_bias_clamp_and_baseline_identity():
    up = apply_share_bias(PROD_TARGETS, 100)
    assert up["BASE_LONG"]["share"] == pytest.approx(0.5)
    assert up["BASE_SHORT"]["share"] == pytest.approx(0.0)
    down = apply_share_bias(PROD_TARGETS, -100)
    assert down["BASE_LONG"]["share"] == pytest.approx(0.0)
    assert down["BASE_SHORT"]["share"] == pytest.approx(0.5)
    assert apply_share_bias(PROD_TARGETS, 0) is PROD_TARGETS


def test_share_signal_bias_equality_and_both_legs_stop():
    # equality with the threshold triggers
    assert share_signal_bias(1.0, -1.0, 1.0) == 1.0
    assert share_signal_bias(-1.0, 1.0, 1.0) == -1.0
    # just below the threshold stays neutral
    assert share_signal_bias(0.999999, -0.999999, 1.0) == 0.0
    # fixed step, no accumulation: a larger excess still returns exactly the threshold
    assert share_signal_bias(5.5, -5.5, 1.0) == 1.0
    assert share_signal_bias(5.5, -5.5, 2.0) == 2.0
    # both legs >= threshold is unspecified: stop with diagnostics, never pick a side
    with pytest.raises(ValueError, match="both legs reached threshold"):
        share_signal_bias(1.0, 1.0, 1.0)


@pytest.mark.asyncio
async def test_hand_calculated_skew_to_signal(tmp_path):
    # T=1000, lev=3, p0=100, interval closes at 101.34 -> hand values compared with the event
    prices = [100.0, 100.0, 100.0, 100.0, 101.34, 101.34]
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(guards_off=True), htf_bias_pp=1)
    assert res is not None
    events = _signal_events(res)
    assert len(events) == 1
    ev = events[0]
    qty, val_long, val_short, total = _hand_values(100.0, 101.34)
    assert qty == pytest.approx(7.5)
    assert val_long == pytest.approx(260.05, rel=1e-9)
    assert val_short == pytest.approx(239.95, rel=1e-9)
    assert total == pytest.approx(1000.0, rel=1e-12)
    w_long = val_long / total
    excess = 100.0 * (w_long - 0.25)
    assert ev["val_long"] == pytest.approx(val_long, rel=1e-12)
    assert ev["val_short"] == pytest.approx(val_short, rel=1e-12)
    assert ev["total"] == pytest.approx(total, rel=1e-12)
    assert ev["w_long"] == pytest.approx(w_long, rel=1e-12)
    assert ev["excess_long_pp"] == pytest.approx(excess, rel=1e-9)
    assert ev["signal"] == "LONG"
    assert ev["targets_long"] == pytest.approx(0.26)
    assert ev["targets_short"] == pytest.approx(0.24)


@pytest.mark.asyncio
@pytest.mark.parametrize("p_end, signal, long_share, short_share", [
    (101.34, "LONG", 0.26, 0.24),   # excess_long >= 1pp
    (98.66, "SHORT", 0.24, 0.26),   # excess_short >= 1pp
])
async def test_long_and_short_publish_fixed_targets(tmp_path, p_end, signal, long_share, short_share):
    prices = [100.0, 100.0, 100.0, 100.0, p_end] + [p_end] * 6
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(guards_off=True), htf_bias_pp=1)
    assert res is not None
    events = _signal_events(res)
    assert events and events[0]["bar"] == 5
    first = events[0]
    assert first["signal"] == signal
    assert first["targets_long"] == pytest.approx(long_share)
    assert first["targets_short"] == pytest.approx(short_share)
    assert first["targets_long"] + first["targets_short"] + 0.5 == pytest.approx(1.0)
    # no movement after the reset: the next measurement is neutral and cancels the skew
    if len(events) > 1:
        assert events[1]["signal"] == "NEUTRAL"
        assert events[1]["bias_pp"] == 0.0
        assert events[1]["targets_long"] == pytest.approx(0.25)


@pytest.mark.asyncio
@pytest.mark.parametrize("p_end, signal, long_share, short_share", [
    (102.7, "LONG", 0.27, 0.23),    # excess ~2.02pp >= 2
    (97.3, "SHORT", 0.23, 0.27),    # excess_short ~2.02pp >= 2
    (101.34, "NEUTRAL", 0.25, 0.25),  # excess ~1.005pp is below the 2pp threshold
])
async def test_threshold_two_targets(tmp_path, p_end, signal, long_share, short_share):
    prices = [100.0, 100.0, 100.0, 100.0, p_end] + [p_end] * 6
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(guards_off=True), htf_bias_pp=2)
    assert res is not None
    events = _signal_events(res)
    assert events and events[0]["signal"] == signal
    assert events[0]["targets_long"] == pytest.approx(long_share)
    assert events[0]["targets_short"] == pytest.approx(short_share)


@pytest.mark.asyncio
async def test_fixed_step_does_not_accumulate(tmp_path):
    # excess ~3pp on threshold 1: the published target is still exactly 26/24/50
    prices = [100.0, 100.0, 100.0, 100.0, 104.0] + [104.0] * 6
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(guards_off=True), htf_bias_pp=1)
    assert res is not None
    ev = _signal_events(res)[0]
    assert ev["excess_long_pp"] > 2.0
    assert ev["targets_long"] == pytest.approx(0.26)
    assert ev["targets_short"] == pytest.approx(0.24)
    assert res["htf_bias"]["max_abs_pp"] == 1.0


@pytest.mark.asyncio
async def test_sub_threshold_stays_neutral(tmp_path):
    # +0.5% in 5m -> excess 0.75pp < 1pp: indicator runs, bias stays 0
    prices = [100.0, 100.0, 100.0, 100.0, 101.0] + [101.0] * 6
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(guards_off=True), htf_bias_pp=1)
    assert res is not None
    events = _signal_events(res)
    assert events and all(e["bias_pp"] == 0.0 for e in events)
    assert all(e["signal"] == "NEUTRAL" for e in events)
    summary = res["htf_bias"]
    assert summary["max_abs_pp"] == 0.0
    assert summary["nonzero_bars"] == 0
    assert summary["final_pp"] == 0.0


@pytest.mark.asyncio
async def test_measurement_before_reset_and_reset_invariants(tmp_path):
    prices = [100.0, 100.0, 100.0, 100.0, 101.34] + [101.34] * 16
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(guards_off=True), htf_bias_pp=1)
    assert res is not None
    events = _signal_events(res)
    assert len(events) >= 2
    skewed = events[0]
    # measured shares are taken BEFORE the reset: the skew is visible, post-reset shares are 25/25/50
    assert skewed["w_long"] != pytest.approx(0.25)
    assert skewed["shares_after_long"] == 0.25
    assert skewed["shares_after_short"] == 0.25
    assert skewed["shares_after_virt"] == 0.5
    # reset preserves the current portfolio value; VIRTUAL is price-neutral throughout
    totals = [e["total"] for e in events]
    assert all(t == pytest.approx(totals[0], rel=1e-12) for t in totals)
    assert all(e["val_virt"] == pytest.approx(0.5 * totals[0], rel=1e-12) for e in events)
    # after reset the next measurement with no price move is exactly neutral
    assert events[1]["w_long"] == pytest.approx(0.25, abs=1e-12)
    assert events[1]["signal"] == "NEUTRAL"


@pytest.mark.asyncio
async def test_no_signal_before_first_full_interval(tmp_path):
    _write_prices(tmp_path / "short", [100.0] * 5)
    res_short = await _run(tmp_path / "short", _config(), htf_bias_pp=1)
    assert res_short is not None
    assert _signal_events(res_short) == []

    _write_prices(tmp_path / "exact", [100.0] * 6)
    res_exact = await _run(tmp_path / "exact", _config(), htf_bias_pp=1)
    assert res_exact is not None
    events = _signal_events(res_exact)
    # bars are opens: the first full [00:00, 00:05) interval closes at bar 5
    assert [e["bar"] for e in events] == [5]
    assert events[0]["signal_available_at"] == events[0]["interval_end"]


@pytest.mark.asyncio
async def test_future_prices_do_not_change_published_signal(tmp_path):
    base = [100.0] * 4 + [101.34] + [101.34] * 55
    # (b) alter only the unclosed tail (bars 55..59): no event may read it
    _write_prices(tmp_path / "a", base)
    altered = base[:55] + [p * 1.1 for p in base[55:]]
    _write_prices(tmp_path / "b", altered)
    res_a = await _run(tmp_path / "a", _config(), htf_bias_pp=1)
    res_b = await _run(tmp_path / "b", _config(), htf_bias_pp=1)
    assert res_a is not None and res_b is not None
    ev_a, ev_b = _signal_events(res_a), _signal_events(res_b)
    assert ev_a and ev_b
    # (c) alter only the execution price of bar 5 (close[5]): an input of no event yet
    altered2 = base[:5] + [base[5] * 1.05] + base[6:]
    _write_prices(tmp_path / "c", altered2)
    res_c = await _run(tmp_path / "c", _config(), htf_bias_pp=1)
    assert res_c is not None
    ev_c = _signal_events(res_c)
    pick = lambda evs: [{k: e[k] for k in SIGNAL_FIELDS} for e in evs]
    assert pick(ev_a)[0] == pick(ev_c)[0]
    # (d) changing the closed interval's last price changes the signal: no look-ahead
    altered3 = base[:4] + [base[4] * 1.1] + base[5:]
    _write_prices(tmp_path / "d", altered3)
    res_d = await _run(tmp_path / "d", _config(), htf_bias_pp=1)
    assert res_d is not None
    ev_d = _signal_events(res_d)
    assert ev_d and ev_d[0]["excess_long_pp"] != ev_a[0]["excess_long_pp"]
    # unclosed tail never reaches any event
    assert pick(ev_a) == pick(ev_b)


@pytest.mark.asyncio
async def test_signal_is_independent_of_1m_bot_state(tmp_path):
    prices = [100.0] * 4 + [101.34] + [101.34] * 55
    _write_prices(tmp_path / "quiet", prices)
    _write_prices(tmp_path / "busy", prices)
    # threshold 10: the 1m bot never rebalances after init; threshold 0: it trades every deviation
    res_quiet = await _run(tmp_path / "quiet", _config(threshold=10.0), htf_bias_pp=1)
    res_busy = await _run(tmp_path / "busy", _config(threshold=0.0), htf_bias_pp=1)
    assert res_quiet is not None and res_busy is not None
    pick = lambda evs: [{k: e[k] for k in SIGNAL_FIELDS} for e in evs]
    assert _signal_events(res_quiet), "expected signals in the quiet run"
    assert _signal_events(res_busy), "expected signals in the busy run"
    # identical signals although the 1m bot's fills/tpv differ
    assert res_quiet["fill_count"] != res_busy["fill_count"]
    assert pick(_signal_events(res_quiet)) == pick(_signal_events(res_busy))


@pytest.mark.asyncio
async def test_indicator_creates_no_own_fills_or_costs(tmp_path):
    # unreachable rebalance threshold: only the initial build can ever fill
    prices = [100.0] * 4 + [101.34] + [101.34] * 55
    _write_prices(tmp_path / "off", prices)
    _write_prices(tmp_path / "on", prices)
    res_off = await _run(tmp_path / "off", _config(threshold=10.0, guards_off=True))
    res_on = await _run(tmp_path / "on", _config(threshold=10.0, guards_off=True), htf_bias_pp=1)
    assert res_off is not None and res_on is not None
    assert res_on["htf_bias"]["updates"] > 0
    assert res_on["fill_count"] == res_off["fill_count"]
    assert res_on["fills"] == res_off["fills"]
    assert res_on["commission_paid"] == res_off["commission_paid"]
    assert res_on["slippage_paid"] == res_off["slippage_paid"]


@pytest.mark.asyncio
async def test_settings_only_differ_by_indicator_param(tmp_path):
    prices = [100.0] * 4 + [101.34] + [101.34] * 55
    _write_prices(tmp_path / "off", prices)
    _write_prices(tmp_path / "on", prices)
    res_off = await _run(tmp_path / "off", _config())
    res_on = await _run(tmp_path / "on", _config(), htf_bias_pp=1)
    assert res_off is not None and res_on is not None
    s_off, s_on = res_off["settings"], res_on["settings"]
    assert set(s_off) == set(s_on)
    for key in s_off:
        if key == "htf_bias_pp":
            continue
        assert s_off[key] == s_on[key], f"settings.{key} diverged"
    assert s_off["htf_bias_pp"] == 0.0
    assert s_on["htf_bias_pp"] == 1.0
    # guards/trailing/commission inputs are untouched by the indicator path
    assert s_on["commission_pct"] == s_off["commission_pct"]
    assert s_on["slippage_pct"] == s_off["slippage_pct"]


@pytest.mark.asyncio
async def test_baseline_identity_when_indicator_disabled(tmp_path):
    prices = [100.0] * 4 + [101.34] + [101.34] * 55
    _write_prices(tmp_path / "default", prices)
    _write_prices(tmp_path / "explicit", prices)
    res_default = await _run(tmp_path / "default", _config())
    res_explicit = await _run(tmp_path / "explicit", _config(), htf_bias_pp=0)
    assert res_default is not None and res_explicit is not None
    assert res_default == res_explicit


@pytest.mark.asyncio
async def test_rebalance_log_records_effective_targets(tmp_path):
    prices = [100.0] * 4 + [101.34] + [101.34] * 55
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(), htf_bias_pp=1)
    assert res is not None
    rebalances = res["rebalances"]
    assert rebalances, "expected rebalance decisions"
    assert all("target_bias_pp" in rb for rb in rebalances)
    biased = [rb for rb in rebalances if rb["target_bias_pp"] != 0.0]
    assert biased, "expected decisions taken under the published biased target"
    for rb in biased:
        # effective targets visible in the log match the signal rule
        assert rb["targets"]["BASE_LONG"] in (0.26, 0.24)
        assert rb["targets"]["BASE_LONG"] + rb["targets"]["BASE_SHORT"] == pytest.approx(0.5)
        assert rb["targets"]["VIRTUAL"] == 0.5


@pytest.mark.asyncio
async def test_summary_and_accounting(tmp_path):
    prices = [100.0] * 4 + [101.34] + [101.34] * 55
    _write_prices(tmp_path, prices)
    res = await _run(tmp_path, _config(guards_off=True), htf_bias_pp=1)
    assert res is not None
    events = _signal_events(res)
    summary = res["htf_bias"]
    assert summary["enabled"] and summary["signal_source"] == "virtual_5m_portfolio"
    assert summary["interval_minutes"] == 5
    assert summary["updates"] == len(events)
    assert summary["signal_counts"]["LONG"] >= 1
    assert summary["signal_counts"]["NEUTRAL"] >= 1
    assert summary["nonzero_bars"] > 0
    assert res["accounting_ok"] and not res["cash_invariant_violation"]
    # timing correspondence: signal is available at the bar that first uses it
    for ev in events:
        assert ev["signal_available_at"] == ev["interval_end"]
        assert ev["first_decision_bar"] == ev["bar"]
