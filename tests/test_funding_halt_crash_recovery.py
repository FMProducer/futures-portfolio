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
from unittest.mock import AsyncMock, MagicMock

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


def _check_crash_after_fill(tmp_path, fraction):
    """Unsaved paper results are replayed from the durable pre-fill state."""
    scenario = _make_funding_scenario(tmp_path)
    config_path = _make_config(tmp_path, scenario)
    state_file = tmp_path / "state.json"
    paper_file = tmp_path / "paper_state.json"
    state = {
        "base_ticker": BASE_TICKER, "virt_qty": 0.0, "virt_debt": -1.0,
        "initial_tpv": 1000.0, "reference_tpv": 1000.0,
        "tpv_ath": 1000.0, "rebalance_cycles": 0,
        "funding_journal": [],
        "funding_halt": {"active": True, "reason": "funding_shortfall",
            "close_state": {f"BASE_{side}": {
                "leg": f"BASE_{side}", "close_event_id": f"close_{side}",
                "status": "pending", "qty_remaining": qty}
                for side, qty in (("LONG", 1.5), ("SHORT", 1.4))}},
    }
    paper = {"base_ticker": BASE_TICKER, "balance": 660.0,
        "positions": {"ZECUSDT_LONG": 1.5, "ZECUSDT_SHORT": 1.4},
        "last_price": 200.0, "long_entry_price": 200.0,
        "short_entry_price": 200.0}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    paper_file.write_text(json.dumps(paper), encoding="utf-8")
    child = tmp_path / "full_fill_child.py"
    child.write_text(_CHILD_SRC.split('with patch("core.executor.')[0] + r'''
import json
from pathlib import Path
from core import funding

mode = sys.argv[5]
fraction = float(sys.argv[6])
original_save = funding.save_funding_boundary

async def execute(actions, price, paper_mode, config, steps, wallet):
    action = actions[0]
    assert paper_mode and action["is_reduction"]
    side = action["position_side"]
    qty = wallet["positions"]["ZECUSDT_" + side]
    if mode == "crash":
        qty *= fraction
    return [{"status": "SUCCESS", "qty": qty,
        "side": "SELL" if side == "LONG" else "BUY", "price": price,
        "commission": .01, "trade_pnl": 0}]

def save(strategy_path, wallet_path, state, wallet, journal, halt):
    if mode == "crash":
        assert wallet["positions"]["ZECUSDT_LONG"] == 1.5 * (1 - fraction)
        assert wallet["balance"] == 659.99
        close = halt.close_state["BASE_LONG"]
        assert close.status == ("filled" if fraction == 1 else "partial")
        assert float(close.qty_closed) == 1.5 * fraction
        assert float(close.qty_remaining) == 1.5 * (1 - fraction)
        assert wallet["long_entry_price"] == (0 if fraction == 1 else 200)
        Path("processed_fill.json").write_text(json.dumps({"state": state, "wallet": wallet}))
        os._exit(99)
    original_save(strategy_path, wallet_path, state, wallet, journal, halt)

async def halted_sleep(seconds, *args, **kwargs):
    assert seconds == 86400
    raise SystemExit(0)

with patch("core.executor.PortfolioExecutor.execute_actions", side_effect=execute), \
     patch("core.funding.save_funding_boundary", side_effect=save), \
     patch("core.main.self_kill_pm2", new=AsyncMock()), \
     patch("core.main.emit_signal"), \
     patch("core.main.TelegramNotifier", return_value=MagicMock()), \
     patch("asyncio.sleep", side_effect=halted_sleep):
    asyncio.run(rebalance_loop(_connector(), CONFIG_PATH, STATE_PATH, PAPER_PATH,
        MagicMock(), ticker_override="ZECUSDT", paper_mode_override=True))
''', encoding="utf-8")
    for mode, expected_code in (("crash", 99), ("restart", 0)):
        proc = subprocess.run([sys.executable, "-I", str(child), str(ROOT),
            config_path, str(state_file), str(paper_file), mode, str(fraction)],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert proc.returncode == expected_code, proc.stdout + proc.stderr
        if mode == "crash":
            assert (tmp_path / "processed_fill.json").exists()
            assert _load_state_files(tmp_path) == (state, paper)
            assert not Path(str(state_file) + ".funding_intent").exists()
    restored, wallet = _load_state_files(tmp_path)
    assert wallet["positions"] == {"ZECUSDT_LONG": 0.0, "ZECUSDT_SHORT": 0.0}
    assert wallet["balance"] == 659.98
    assert wallet["long_entry_price"] == wallet["short_entry_price"] == 0
    assert restored["funding_halt"]["active"]
    for leg, qty in (("BASE_LONG", 1.5), ("BASE_SHORT", 1.4)):
        close = restored["funding_halt"]["close_state"][leg]
        assert close["status"] == "filled"
        assert close["qty_closed"] == qty
        assert close["commission"] == .01
    assert not Path(str(state_file) + ".funding_intent").exists()


@pytest.mark.asyncio
async def test_crash_after_full_fill(tmp_path):
    """Crash after full fill returned, before durable commit."""
    _check_crash_after_fill(tmp_path, 1.0)


@pytest.mark.asyncio
async def test_crash_after_partial_fill(tmp_path):
    """Crash after partial fill returned, before durable commit."""
    _check_crash_after_fill(tmp_path, 0.5)


@pytest.mark.asyncio
async def test_reconcile_grown_live_fill_accounted_once():
    """Same order filled more after the crash: actuals replace the record, no double count."""
    from futures_portfolio.core.main import _reconcile_funding_leg
    halt = FundingHalt.from_dict({
        "active": True,
        "close_state": {"BASE_LONG": {
            "leg": "BASE_LONG", "close_event_id": "close_LONG",
            "status": "partial", "qty_closed": 0.5, "qty_remaining": 1.0,
            "fill_price": 200.0, "commission": 0.01, "realized_pnl": 0.0,
            "execution_id": "binance:ZECUSDT:555"}},
    })
    paper = {"positions": {"ZECUSDT_LONG": 1.0}, "balance": 660.0}
    connector = MagicMock()
    connector.get_order_status = AsyncMock(return_value={
        "symbol": BASE_TICKER, "orderId": 555, "side": "SELL",
        "positionSide": "LONG", "origQty": "1.5",
        "status": "PARTIALLY_FILLED", "executedQty": "1.2", "avgPrice": "200.5"})
    connector.get_order_trades = AsyncMock(return_value=[
        {"id": 1, "orderId": 555, "symbol": BASE_TICKER, "side": "SELL",
         "positionSide": "LONG", "qty": "0.4", "price": "200.5",
         "commissionAsset": "USDT", "commission": "0.008", "realizedPnl": "0.01"},
        {"id": 2, "orderId": 555, "symbol": BASE_TICKER, "side": "SELL",
         "positionSide": "LONG", "qty": "0.8", "price": "200.5",
         "commissionAsset": "USDT", "commission": "0.022", "realizedPnl": "0.03"}])
    verdict = await _reconcile_funding_leg(connector, halt, "BASE_LONG", paper,
                                           BASE_TICKER, False, MagicMock())
    cs = halt.close_state["BASE_LONG"]
    assert verdict == "handled"
    assert cs.status == "partial"
    assert cs.qty_closed == D("1.2") and cs.qty_remaining == D("0.3")
    assert cs.commission == D("0.03") and cs.realized_pnl == D("0.04")
    assert cs.fill_price == D("200.5")
    assert paper["positions"]["ZECUSDT_LONG"] == 0.3
    assert paper["balance"] == 660.02  # +pnl delta .04, -commission delta .02
    # a second pass on the same actuals must be a no-op (exactly once)
    again = await _reconcile_funding_leg(connector, halt, "BASE_LONG", paper,
                                         BASE_TICKER, False, MagicMock())
    assert again == "handled"
    assert cs.qty_closed == D("1.2") and cs.commission == D("0.03")
    assert paper["positions"]["ZECUSDT_LONG"] == 0.3
    assert paper["balance"] == 660.02


@pytest.mark.asyncio
async def test_reconcile_inconsistent_live_fill_quarantines():
    """Actual fill outside the committed window: stop, keep facts, never re-order."""
    from futures_portfolio.core.main import _reconcile_funding_leg
    halt = FundingHalt.from_dict({
        "active": True,
        "close_state": {"BASE_LONG": {
            "leg": "BASE_LONG", "close_event_id": "close_LONG",
            "status": "partial", "qty_closed": 0.5, "qty_remaining": 1.0,
            "execution_id": "binance:ZECUSDT:555"}},
    })
    paper = {"positions": {"ZECUSDT_LONG": 1.0}, "balance": 660.0}
    connector = MagicMock()
    connector.get_order_status = AsyncMock(return_value={
        "status": "FILLED", "executedQty": "0.2", "avgPrice": "200.5"})
    connector.get_order_trades = AsyncMock(return_value=[])
    verdict = await _reconcile_funding_leg(connector, halt, "BASE_LONG", paper,
                                           BASE_TICKER, False, MagicMock())
    assert verdict == "handled"
    assert halt.close_state["BASE_LONG"].status == "quarantined"
    assert paper["positions"]["ZECUSDT_LONG"] == 1.0
    assert paper["balance"] == 660.0
    connector.get_order_status = AsyncMock(side_effect=Exception("api down"))
    halt2 = FundingHalt.from_dict({
        "active": True,
        "close_state": {"BASE_SHORT": {
            "leg": "BASE_SHORT", "close_event_id": "close_SHORT",
            "status": "pending", "qty_closed": 0.0, "qty_remaining": 1.4,
            "execution_id": "binance:ZECUSDT:556"}},
    })
    verdict2 = await _reconcile_funding_leg(connector, halt2, "BASE_SHORT", paper,
                                            BASE_TICKER, False, MagicMock())
    assert verdict2 == "handled"
    assert halt2.close_state["BASE_SHORT"].status == "quarantined"


@pytest.mark.asyncio
async def test_reconcile_missing_identity_stops_when_position_shrunk():
    """Missing durable identity quarantines even with unchanged exchange quantity."""
    from futures_portfolio.core.main import _reconcile_funding_leg
    def _seed_halt():
        return FundingHalt.from_dict({
            "active": True,
            "close_state": {"BASE_LONG": {
                "leg": "BASE_LONG", "close_event_id": "close_LONG",
                "status": "pending", "qty_closed": 0.0, "qty_remaining": 1.5}}})
    paper = {"positions": {"ZECUSDT_LONG": 1.5}, "balance": 660.0}
    connector = MagicMock()
    connector.get_positions = AsyncMock(return_value={"ZECUSDT_LONG": {"qty": 0.3}})
    verdict = await _reconcile_funding_leg(connector, _seed_halt(), "BASE_LONG", paper,
                                           BASE_TICKER, False, MagicMock())
    assert verdict == "handled"
    connector.get_positions = AsyncMock(return_value={"ZECUSDT_LONG": {"qty": 1.5}})
    halt = _seed_halt()
    verdict = await _reconcile_funding_leg(connector, halt, "BASE_LONG", paper,
                                           BASE_TICKER, False, MagicMock())
    assert verdict == "handled"
    assert halt.close_state["BASE_LONG"].status == "quarantined"
    assert "identity" in halt.close_state["BASE_LONG"].reconciliation_error
    assert paper == {"positions": {"ZECUSDT_LONG": 1.5}, "balance": 660.0}
    connector.get_positions.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
