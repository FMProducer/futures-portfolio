"""
Funding-halt close recovery crash tests.

Tests three crash points in the Phase 2 close-recovery path:
1. Before executor call (crash before any fill)
2. After full fill returned, before durable commit (save_funding_boundary)
3. After partial fill returned, before durable commit

Each crash is simulated by os._exit(99) in a child process.
After restart, on-disk state is checked for correct reconciliation.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal as D
from pathlib import Path

import pytest

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.funding import (
    FundingHalt, CloseLegState, record_close_result,
    commit_funding_halt_with_intents, get_pending_close_legs,
    save_funding_boundary, RECOVERY_ABSENT, RECOVERY_RECOVERED,
    FundingJournal, FundingEvent,
)
from backtest.backtest_rebalance import run_backtest
from core.executor import PortfolioExecutor
from core.funding import apply_funding_backtest
from backtest.backtest_rebalance import BacktestState


# ── Test constants ────────────────────────────────────────────────────────

BASE_TICKER = "ZECUSDT"
INITIAL_CAPITAL = D("1000")
PAPER_INITIAL_CAPITAL = D("1000")
TARGET_INITIAL_CAP = D("1000")


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_funding_scenario(tmp_path: Path) -> str:
    """Generate the same 30-event funding scenario used in run_zec_replay.py"""
    events = []
    eid = 0
    for day_offset in range(10):
        for hour in (0, 8, 16):
            ts = 1791590400 + day_offset * 86400 + hour * 3600
            if ts > 1792454400:
                continue

            day_in_window = day_offset
            if day_in_window <= 2:
                rate = -0.000025 * (1.0 + 0.5 * day_in_window)
            elif day_in_window <= 5:
                rate = 0.000015
            elif day_in_window <= 7:
                rate = -0.000080 * (1.0 + (day_in_window - 5))
            else:
                rate = -0.000010

            notional = 2450.0
            signed_amount = notional * rate

            if day_offset == 6 and hour == 8:
                signed_amount = -0.000049
            if day_offset == 8 and hour == 0:
                signed_amount = -4500.0

            events.append({
                "event_id": f"zec_fund:{eid:04d}",
                "signed_amount": round(signed_amount, 10),
                "event_time": float(ts),
                "source": "scenario",
                "source_ref": f"zec_replay_{day_offset}_{hour}",
                "portfolio_id": "default",
                "symbol": "ZECUSDT",
                "asset": "USDT",
                "raw_payload_hash": "",
                "status": "received",
                "applied_at": None,
                "kind": "FUNDING_FEE",
            })
            eid += 1

    scenario_path = tmp_path / "zec_funding_scenario_crash.json"
    scenario_path.write_text(json.dumps(events, indent=2))
    return str(scenario_path)


def _make_config(tmp_path: Path, scenario_path: str) -> str:
    """Create config for paper mode with funding scenario"""
    config = {
        "paper_mode": True,
        "base_ticker": BASE_TICKER,
        "equity_trailing_stop_activation_pct": 9000.0,
        "max_drawdown_limit": 200.0,
        "portfolios": [{
            "initial_capital": 1000.0,
            "paper_initial_capital": 1000.0,
            "paper_account_free_margin": 1000.0,
            "paper_min_free_margin_pct": 15.0,
            "margin_ratio_warning": 3.0,
            "margin_ratio_critical": 1.5,
            "liquidation_distance_warn_pct": 0.0,
            "liquidation_distance_crit_pct": 0.0,
            "post_factum_liquidation_check": False,
            "siphoning_threshold_pct": 1000.0,
            "targets": {
                "BASE_LONG": {"share": 0.35, "leverage": 7},
                "BASE_SHORT": {"share": 0.35, "leverage": 7},
                "VIRTUAL": {"share": 0.30, "leverage": 1},
            },
            "safety_guards": {
                "max_spread_pct": 0.15,
                "max_price_velocity_pct": 2.0,
                "velocity_window_sec": 60,
                "trend_min_move_pct": 0.5,
                "trend_eff_threshold": 0.55,
                "net_move_block_pct": 2.14,
                "net_move_window_sec": 30,
            },
            "rebalance_threshold_surplus": 0.02,
            "rebalance_threshold_deficit": 0.02,
            "check_interval_sec": 15,
        }],
        "funding_model": "signed_actual",
        "funding_scenario": scenario_path,
    }
    cfg_path = tmp_path / "config_crash.json"
    cfg_path.write_text(json.dumps(config, indent=2))
    return str(cfg_path)


def _copy_feather_data(tmp_path: Path) -> Path:
    """Copy ZECUSDT feather data to temp dir"""
    src = ROOT / "data" / "ZECUSDT_12m.feather"
    dst = tmp_path / "ZECUSDT_live.feather"
    shutil.copy2(src, dst)
    return tmp_path


def _load_state_files(tmp_path: Path):
    """Load state and paper_state JSON files from disk."""
    state_file = tmp_path / "state.json"
    paper_file = tmp_path / "paper_state.json"

    state = {}
    paper_state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text())
    if paper_file.exists():
        paper_state = json.loads(paper_file.read_text())
    return state, paper_state


# ── Child-process crash harness ─────────────────────────────────────────────
# The crash must be a real process death (os._exit skips finally/atexit), so
# rebalance_loop runs in a child process — patching os._exit in-process would
# kill the pytest runner itself instead of the bot under test.

_CHILD_SRC = r'''
import asyncio, logging, os, sys
from unittest.mock import AsyncMock, MagicMock, patch

ROOT, CONFIG_PATH, STATE_PATH, PAPER_PATH = sys.argv[1:5]
sys.path.insert(0, ROOT)

from core.main import rebalance_loop


def _connector():
    c = MagicMock()
    c.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"symbol": "ZECUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"}
        ]}]
    })
    c.get_order_book = AsyncMock(return_value={
        "bids": [["200.0", "100"]], "asks": [["200.1", "100"]]
    })
    c.get_mark_prices = AsyncMock(return_value={"ZECUSDT": 200.0})
    c.get_futures_balance = AsyncMock(return_value=1000.0)
    c.get_futures_positions = AsyncMock(return_value=[])
    c.get_hedge_mode = AsyncMock(return_value=True)
    c.set_leverage = AsyncMock()
    c.set_margin_type = AsyncMock()
    return c


logging.basicConfig(level=logging.CRITICAL)

with patch("core.executor.PortfolioExecutor.execute_actions") as mock_execute:
    mock_execute.side_effect = lambda *a, **kw: os._exit(99)
    asyncio.run(rebalance_loop(
        connector=_connector(),
        config_path=CONFIG_PATH,
        state_file_path=STATE_PATH,
        paper_state_file_path=PAPER_PATH,
        logger=MagicMock(),
        ticker_override="ZECUSDT",
        paper_mode_override=True,
    ))
'''


def _run_crash_child(tmpdir: Path, config_path: str, state_file: Path,
                     paper_file: Path, timeout: int = 120) -> int:
    """Run rebalance_loop in a child process that crashes before the executor.

    Returns the child's exit code (99 = the injected crash was reached)."""
    child_path = tmpdir / "_crash_child.py"
    child_path.write_text(_CHILD_SRC)

    env = dict(os.environ)
    # Keep the child from touching a real deployment's PM2 instance.
    env["TELEGRAM_BOT_TOKEN"] = ""
    env["TELEGRAM_CHAT_ID"] = ""

    proc = subprocess.run(
        [sys.executable, "-I", str(child_path), str(ROOT), config_path,
         str(state_file), str(paper_file)],
        capture_output=True, text=True, timeout=timeout, cwd=str(tmpdir),
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc.returncode


# ── Test Cases ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crash_before_executor():
    """Crash before executor call - no fill, disk unchanged."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        scenario_path = _make_funding_scenario(tmpdir)
        config_path = _make_config(tmpdir, scenario_path)
        _copy_feather_data(tmpdir)

        # Create initial state files with funding halt active
        state = {
            "virt_qty": 0.0,
            "base_ticker": "ZECUSDT",
            "siphoning_reserve": 0.0,
            "balance": 1000.0,
            "initial_tpv": 0.0,
            "reference_tpv": 0.0,
            "tpv_ath": 1000.0,
            "trailing_stop_violation_start": 0.0,
            "trailing_stop_paper_timeout_end": 0.0,
            "rebalance_cycles": 0,
            "last_rebalance_price": 0.0,
            "cash_invariant_violation": None,
            "initial_build_pending": None,
            "started_at": 0.0,
            "funding_model": "legacy_constant",
            "funding_journal": [],
            "funding_halt": {
                "active": True,
                "reason": "funding_shortfall",
                "event_id": "test_halt",
                "halted_at": 100.0,
                "shortfall_amount": 50.0,
                "increases_blocked": True,
                "close_state": {
                    "BASE_LONG": {
                        "leg": "BASE_LONG",
                        "close_event_id": "close_LONG",
                        "status": "pending",
                        "qty_closed": 0.0,
                        "qty_remaining": 1.5,
                        "fill_price": 0.0,
                        "commission": 0.0,
                        "realized_pnl": 0.0,
                        "filled_at": None
                    },
                    "BASE_SHORT": {
                        "leg": "BASE_SHORT",
                        "close_event_id": "close_SHORT",
                        "status": "pending",
                        "qty_closed": 0.0,
                        "qty_remaining": 1.4,
                        "fill_price": 0.0,
                        "commission": 0.0,
                        "realized_pnl": 0.0,
                        "filled_at": None
                    }
                }
            },
            "funding_paid": 0.0,
        }

        paper_state = {
            "balance": 660.0,
            "positions": {"ZECUSDT_LONG": 1.5, "ZECUSDT_SHORT": 1.4},
            "last_price": 200.0,
            "base_ticker": "ZECUSDT",
            "long_entry_price": 200.0,
            "short_entry_price": 200.0,
        }

        state_file = tmpdir / "state.json"
        paper_file = tmpdir / "paper_state.json"

        with open(state_file, "w") as f:
            json.dump(state, f)
        with open(paper_file, "w") as f:
            json.dump(paper_state, f)

        # Create config
        cfg_path = tmpdir / "config_crash.json"
        with open(config_path, "w") as f:
            json.dump({
                "paper_mode": True,
                "base_ticker": "ZECUSDT",
                "equity_trailing_stop_activation_pct": 9000.0,
                "max_drawdown_limit": 200.0,
                "portfolios": [{
                    "initial_capital": 1000.0,
                    "paper_initial_capital": 1000.0,
                    "paper_account_free_margin": 1000.0,
                    "paper_min_free_margin_pct": 15.0,
                    "margin_ratio_warning": 3.0,
                    "margin_ratio_critical": 1.5,
                    "liquidation_distance_warn_pct": 0.0,
                    "liquidation_distance_crit_pct": 0.0,
                    "post_factum_liquidation_check": False,
                    "siphoning_threshold_pct": 1000.0,
                    "targets": {
                        "BASE_LONG": {"share": 0.35, "leverage": 7},
                        "BASE_SHORT": {"share": 0.35, "leverage": 7},
                        "VIRTUAL": {"share": 0.30, "leverage": 1},
                    },
                    "safety_guards": {
                        "max_spread_pct": 0.15,
                        "max_price_velocity_pct": 2.0,
                        "velocity_window_sec": 60,
                        "trend_min_move_pct": 0.5,
                        "trend_eff_threshold": 0.55,
                        "net_move_block_pct": 2.14,
                        "net_move_window_sec": 30,
                    },
                    "rebalance_threshold_surplus": 0.02,
                    "rebalance_threshold_deficit": 0.02,
                    "check_interval_sec": 15,
                }],
                "funding_model": "signed_actual",
                "funding_scenario": scenario_path,
            }, f, indent=2)

        # Copy feather
        src = ROOT / "data" / "ZECUSDT_12m.feather"
        dst = tmpdir / "ZECUSDT_live.feather"
        shutil.copy2(src, dst)

        # Write state files
        state_file = tmpdir / "state.json"
        paper_file = tmpdir / "paper_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f)
        with open(paper_file, "w") as f:
            json.dump(paper_state, f)

        # Create funding scenario
        scenario_path = _make_funding_scenario(tmpdir)

        # Crash in a child process: the executor must be reached, then the
        # child dies with exit code 99 before any fill is recorded.
        exit_code = _run_crash_child(tmpdir, config_path, state_file, paper_file)
        assert exit_code == 99, f"child did not reach the injected crash (exit={exit_code})"

        # Verify disk state is pre-crash (no commit happened)
        state, paper = _load_state_files(tmpdir)

        # Funding halt should still be active
        halt = state.get("funding_halt", {})
        assert halt.get("active") is True
        # Positions unchanged
        assert paper.get("positions", {}).get("ZECUSDT_LONG", 0) == 1.5
        assert paper.get("positions", {}).get("ZECUSDT_SHORT", 0) == 1.4
        # Wallet unchanged
        assert paper.get("balance", 0) == 660.0
        # Halt close_state still pending
        close_state = halt.get("close_state", {})
        assert close_state.get("BASE_LONG", {}).get("status") == "pending"
        assert close_state.get("BASE_SHORT", {}).get("status") == "pending"

        # WAL intent should NOT exist (not reached)
        intent_file = tmpdir / "state.json.funding_intent"
        assert not intent_file.exists(), "WAL intent should not exist before executor"


@pytest.mark.asyncio
async def test_crash_after_full_fill():
    """Crash after full fill returned, before durable commit."""
    # TODO: Implement
    pass


@pytest.mark.asyncio
async def test_crash_after_partial_fill():
    """Crash after partial fill returned, before durable commit."""
    # TODO: Implement
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])