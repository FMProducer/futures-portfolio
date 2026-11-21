"""Independent verification of the optimizer lookback candidate (2026-11-17).

Checks, without touching production files:
- the redundant lookback regex patch is removed from the patched-backtester generator
- trend_lookback_bars propagates natively through safety_guards, the key the engine reads
- the real backtest engine consumes two distinct lookback values differently
  (deterministic synthetic history, no mocked backtest, no parameter search)
- input config is unmutated, only the lookback value differs between the two runs,
  and import/temp side effects are cleaned up
"""
import copy
import importlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
OPTIMIZER = ROOT / "optimization" / "optimize_trend_guard.py"
ENGINE = ROOT / "backtest" / "backtest_rebalance.py"
N_BARS = 240


def _synthetic_closes(n: int = N_BARS) -> np.ndarray:
    # deterministic, RNG-free: 3% oscillation, then a one-way trend, then flat wobble
    closes = np.empty(n)
    for i in range(100):
        closes[i] = 100.0 + 3.0 * np.sin(2.0 * np.pi * i / 10.0)
    for i in range(100, 150):
        closes[i] = closes[i - 1] * 1.005
    base = closes[149]
    for i in range(150, n):
        closes[i] = base * (1.002 if (i - 150) % 2 == 0 else 0.998)
    return closes


def _synthetic_config() -> dict:
    return {
        "portfolios": [{
            "name": "LookbackVerify",
            "initial_capital": 1000.0,
            "paper_initial_capital": 1000.0,
            "paper_account_free_margin": 5000.0,
            "cash_reserve_usdt": 5000.0,
            "paper_min_free_margin_pct": 15.0,
            "margin_ratio_warning": 3.0,
            "margin_ratio_critical": 1.5,
            "liquidation_distance_warn_pct": 40.0,
            "liquidation_distance_crit_pct": 25.0,
            "post_factum_liquidation_check": True,
            "siphoning_threshold_pct": 0.0,
            # asymmetric legs: a one-way trend creates equity drift, so the trend
            # region generates rebalance actions for the lookback guard to block
            "targets": {
                "BASE_LONG": {"share": 0.30, "leverage": 3},
                "BASE_SHORT": {"share": 0.10, "leverage": 3},
                "VIRTUAL": {"share": 0.60, "leverage": 1},
            },
            "trend_add_block": {"pct": 0.0, "window_bars": 120},
            "safety_guards": {
                "max_spread_pct": 0.15,
                "max_price_velocity_pct": 50.0,  # velocity guard disabled
                "velocity_window_sec": 60,
                "trend_min_move_pct": 0.5,
                "trend_eff_threshold": 0.8,
                "net_move_block_pct": 2.2,
                "net_move_window_sec": 60,
                "trend_lookback_bars": 30,
            },
            "fuse_enabled": True,
            "rebalance_threshold_surplus": 0.005,
            "rebalance_threshold_deficit": 0.005,
            "ticker_thresholds": {},
        }],
        # trail disabled: trend drift is not absorbed, so the trend region keeps
        # producing rebalance actions for the lookback guard to act on
        "rebalance_share_trail_distance": 0.0,
        "base_ticker": "SYNUSDT",
        "funding_model": "disabled",
        "paper_mode": True,
    }


def _trend_eff_blocks(result: dict) -> int:
    return sum(1 for e in result.get("events", [])
               if e.get("kind") == "trend_guard_block" and str(e.get("detail", "")).startswith("trend move"))


@pytest.fixture()
def opt(tmp_path, monkeypatch):
    """Fresh optimizer import with side-effect dirs redirected to tmp_path."""
    real_root = ROOT
    sys.path_before = list(sys.path)
    sys.modules.pop("optimization.optimize_trend_guard", None)
    module = importlib.import_module("optimization.optimize_trend_guard")
    patched_file = real_root / f"{module.patched_module_name}.py"
    monkeypatch.setattr(module, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(module, "CONFIG_PATH", tmp_path / "config.json")
    yield module
    # cleanup_patched_module resolves PROJECT_DIR at call time (now tmp_path), so
    # remove this import's own generated file from the real root explicitly
    if patched_file.exists():
        patched_file.unlink()
    sys.modules.pop(module.patched_module_name, None)
    sys.modules.pop("optimization.optimize_trend_guard", None)
    for entry in list(sys.path):
        if entry not in sys.path_before:
            sys.path.remove(entry)


def test_source_regex_removed_and_engine_key_untouched():
    src = OPTIMIZER.read_text(encoding="utf-8")
    # the removed regex: pattern and its replacement are both gone
    assert r"tg_trend_bars\s*=\s*30" not in src
    assert "'lookback_bars'" not in src
    assert '"lookback_bars"' not in src
    # the old regex target does not exist in the engine: it was dead code
    eng = ENGINE.read_text(encoding="utf-8")
    assert not re.search(r"tg_trend_bars\s*=\s*30", eng)
    # the engine reads the lookback window only from safety_guards
    assert re.search(r'tg_trend_bars\s*=\s*int\(guards_cfg\.get\("trend_lookback_bars",\s*30\)\)', eng)


def test_generated_patched_backtester_has_no_lookback_patch(opt):
    text = (ROOT / f"{opt.patched_module_name}.py").read_text(encoding="utf-8")
    # the replacement string of the removed regex must not appear
    assert "trend_guard_cfg.get('lookback_bars', 30)" not in text
    assert "'lookback_bars'" not in text
    # the surviving net-move regex patch must still fire
    assert "trend_guard_cfg.get('net_move_window_sec', 60) // 60" in text


def test_real_engine_two_lookback_values(tmp_path, monkeypatch, opt):
    """Same real engine run, two lookback values; only that field may differ."""
    closes = _synthetic_closes()
    df = pd.DataFrame({
        "open": np.concatenate([[closes[0]], closes[:-1]]),
        "high": np.maximum(closes, np.concatenate([[closes[0]], closes[:-1]])) * 1.001,
        "low": np.minimum(closes, np.concatenate([[closes[0]], closes[:-1]])) * 0.999,
        "close": closes,
        "volume": np.full(N_BARS, 1000.0),
    })
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.to_feather(data_dir / "SYNUSDT_live.feather")

    cfg_in = _synthetic_config()
    cfg_snapshot = copy.deepcopy(cfg_in)
    (tmp_path / "config.json").write_text(json.dumps(cfg_in), encoding="utf-8")

    # capture the configs the engine actually receives, then delegate to the REAL run_backtest
    real_run = opt.patched_backtest.run_backtest
    captured = []

    async def spy(config_path, *args, **kwargs):
        captured.append(json.loads(Path(config_path).read_text(encoding="utf-8")))
        return await real_run(config_path, *args, **kwargs)

    monkeypatch.setattr(opt.patched_backtest, "run_backtest", spy)

    base_params = {"net_move_block_pct": 2.2, "net_move_window_sec": 60,
                   "trend_min_move_pct": 0.5, "trend_eff_threshold": 0.8}
    res_lo = opt.evaluate_ticker(copy.deepcopy(cfg_in), "SYNUSDT", N_BARS / 1440.0,
                                 {**base_params, "trend_lookback_bars": 10})
    res_hi = opt.evaluate_ticker(copy.deepcopy(cfg_in), "SYNUSDT", N_BARS / 1440.0,
                                 {**base_params, "trend_lookback_bars": 30})

    # both runs completed on the real engine and consumed the full synthetic history
    assert res_lo is not None and res_hi is not None
    assert res_lo["data_window"]["bars"] == N_BARS
    assert res_hi["data_window"]["bars"] == N_BARS
    assert "history" in res_lo and "history" in res_hi  # generator's sortino patch applied

    # native propagation: the values reached the engine's actual key
    lo_cfg, hi_cfg = captured
    assert lo_cfg["portfolios"][0]["safety_guards"]["trend_lookback_bars"] == 10
    assert hi_cfg["portfolios"][0]["safety_guards"]["trend_lookback_bars"] == 30
    # the removed key is absent from the trend_guard dict
    assert "lookback_bars" not in lo_cfg["trend_guard"]
    assert "lookback_bars" not in hi_cfg["trend_guard"]
    # only the lookback value differs between the two engine configs
    assert lo_cfg != hi_cfg
    lo_stripped = copy.deepcopy(lo_cfg)
    hi_stripped = copy.deepcopy(hi_cfg)
    lo_stripped["portfolios"][0]["safety_guards"].pop("trend_lookback_bars")
    hi_stripped["portfolios"][0]["safety_guards"].pop("trend_lookback_bars")
    assert lo_stripped == hi_stripped

    # the parameter demonstrably changes engine behavior: the short window sees the
    # pure trend (eff ~1.0) and blocks; the long window mixes in oscillation (eff < 0.8)
    blocks_lo = _trend_eff_blocks(res_lo)
    blocks_hi = _trend_eff_blocks(res_hi)
    print(f"lookback=10 trend-eff blocks={blocks_lo} (total tg={res_lo['trend_guard_blocks']}), "
          f"lookback=30 trend-eff blocks={blocks_hi} (total tg={res_hi['trend_guard_blocks']})")
    assert blocks_lo > blocks_hi > 0
    assert blocks_lo - blocks_hi >= 5  # non-marginal regression strength

    # the input config must be unmutated
    assert cfg_in == cfg_snapshot
    # temp files created by evaluate_ticker are removed
    assert list(tmp_path.glob("tmp_cfg_*")) == []
    # no optuna study created by import
    assert not (ROOT / "optuna_trend_guard.db").exists()


def test_import_creates_one_patched_file_and_cleanup_removes_it():
    created_before = set(ROOT.glob("_patched_backtest_*.py"))
    sys.modules.pop("optimization.optimize_trend_guard", None)
    sys.path_before = list(sys.path)
    module = importlib.import_module("optimization.optimize_trend_guard")
    try:
        created = set(ROOT.glob("_patched_backtest_*.py")) - created_before
        assert len(created) == 1, f"import created {created}"
        assert not (ROOT / "optuna_trend_guard.db").exists()  # no study at import
        module.cleanup_patched_module()
        assert not (ROOT / f"{module.patched_module_name}.py").exists()
        assert module.patched_module_name not in sys.modules
    finally:
        sys.modules.pop("optimization.optimize_trend_guard", None)
        for entry in list(sys.path):
            if entry not in sys.path_before:
                sys.path.remove(entry)
