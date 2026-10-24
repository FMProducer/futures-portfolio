"""Live launch guard: an expected live snapshot pair must gate the real startup path.

Every test drives core.main.rebalance_loop with real files on disk (no load_json
patch): missing, corrupt and mismatched pairs, WAL recovery before fallback,
effective paper/real mode, restart idempotency and blocked-source increases.
"""
import asyncio
import copy
import json
import os
import time
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import futures_portfolio.core.main as main_mod
from futures_portfolio.core.main import rebalance_loop

M = "futures_portfolio.core.main"


class StartupBlocked(BaseException):
    """Raised by the patched asyncio.sleep(86400) zombie loop."""


class StopLoop(BaseException):
    """Raised once the allowed number of funding commits has run."""


class Run(NamedTuple):
    outcome: str
    state_path: Path
    wallet_path: Path
    connector: MagicMock
    calc: MagicMock
    kill: MagicMock
    reads: list


def _config(**overrides):
    from test_main_coverage import _make_config
    overrides.setdefault("paper_mode", False)
    return _make_config(**overrides)


def _state(**overrides):
    from test_main_coverage import _make_state
    s = _make_state(
        virt_qty=0.0, virt_debt=30.0, funding_paid=0.0, funding_model="signed_actual",
        funding_journal=[], funding_halt={}, share_trail=None, share_trail_spec=None,
        share_trail_receipts=[], initial_build_pending=None, cash_invariant_violation=None)
    s.update({"live_funding": {"symbol": "BTCUSDT", "snapshot_ms": 1000, "boundary_ms": 1000,
                               "opening_wallet": "100", "blocked": ""}, **overrides})
    return s


def _wallet(**overrides):
    from test_main_coverage import _make_paper_state
    w = _make_paper_state()
    w.update({"live_funding_snapshot": {"symbol": "BTCUSDT", "snapshot_ms": 1000},
              **overrides})
    return w


def _pair(snapshot_ms=1000, boundary_ms=1000):
    """A usable reciprocal pair with matching markers."""
    state = _state(live_funding={"symbol": "BTCUSDT", "snapshot_ms": snapshot_ms,
                                 "boundary_ms": boundary_ms, "opening_wallet": "100",
                                 "blocked": ""})
    wallet = _wallet(live_funding_snapshot={"symbol": "BTCUSDT",
                                            "snapshot_ms": snapshot_ms})
    return state, wallet


def _write(path: Path, data) -> Path:
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _connector():
    c = MagicMock()
    c.get_futures_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    c.get_mark_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    c.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ]}]})
    c.get_hedge_mode = AsyncMock(return_value=True)
    c.get_free_balance = AsyncMock(return_value=100.0)
    c.get_positions = AsyncMock(return_value={"BTCUSDT_LONG": {"qty": 0.0, "entry_price": 60000.0}})
    c.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 100.0})
    c.get_bnb_balance = AsyncMock(return_value=1.0)
    c.get_order_book = AsyncMock(return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
    c.set_leverage = AsyncMock()
    c.set_margin_type = AsyncMock()
    c.get_funding_income = AsyncMock(return_value=[])
    return c


async def _run(tmp_path, state_data, wallet_data, *, expect=True, paper_override=None,
               config=None, connector=None, limit=2, state_name="state.json",
               wallet_name="wallet.json", corruption_rename_fails=False):
    """Drive the real startup path against real files; returns a Run."""
    from core import funding as funding_mod

    cfg = config if config is not None else _config()
    cfg_path = _write(tmp_path / "config.json", cfg)
    state_path = tmp_path / state_name
    wallet_path = tmp_path / wallet_name
    if state_data is not None:
        _write(state_path, state_data)
    if wallet_data is not None:
        _write(wallet_path, wallet_data)

    connector = connector or _connector()
    notifier = MagicMock()
    for name in ("send_message", "send_alert", "send_status", "close"):
        setattr(notifier, name, AsyncMock())

    def bounded_sleep(secs, *args, **kwargs):
        if secs == 86400:
            raise StartupBlocked("startup blocked")
        return None

    real_load = main_mod.load_json
    reads = []

    async def counting_load(path, default, *args, **kwargs):
        reads.append(str(path))
        return await real_load(path, default, *args, **kwargs)

    # The blocked-source paths `continue` without sleeping, so bound the run by
    # funding commits instead of by loop iterations.
    real_save = funding_mod.save_funding_boundary
    commits = {"n": 0}

    def counting_save(*args, **kwargs):
        real_save(*args, **kwargs)
        commits["n"] += 1
        if commits["n"] > limit:
            raise StopLoop("loop finished")

    real_replace = os.replace

    def flaky_replace(src, dst):
        if ".corrupted" in os.path.basename(str(dst)):
            raise PermissionError("evidence preservation denied")
        return real_replace(src, dst)

    calc = MagicMock()
    calc.calculate_rebalance.return_value = {
        "total_tpv": 100.0, "tpv": 100.0, "actions": [], "total_pnl_pct": 0.0}
    calc.tpv = 100.0
    calc.val_cash = Decimal("0")

    kill = AsyncMock()
    replace_ctx = (patch("os.replace", side_effect=flaky_replace)
                   if corruption_rename_fails else nullcontext())
    outcome = "stopped"
    with patch(f"{M}.self_kill_pm2", kill), \
         patch(f"{M}.load_json", side_effect=counting_load), \
         patch(f"{M}.TelegramNotifier", return_value=notifier), \
         patch(f"{M}.PortfolioCalculator", return_value=calc), \
         patch(f"{M}.BinanceConnector", return_value=connector), \
         patch("core.funding.save_funding_boundary", side_effect=counting_save), \
         patch("asyncio.sleep", side_effect=bounded_sleep), \
         replace_ctx:
        try:
            await rebalance_loop(connector, str(cfg_path), str(state_path), str(wallet_path),
                                 MagicMock(), ticker_override="BTCUSDT",
                                 paper_mode_override=paper_override,
                                 expect_live_snapshot=expect)
        except StartupBlocked:
            outcome = "blocked"
        except StopLoop:
            outcome = "ran"
    return Run(outcome, state_path, wallet_path, connector, calc, kill, reads)


# ── C: lost / corrupt / mismatched pair blocks, durable ─────────────────────

@pytest.mark.asyncio
async def test_missing_pair_without_wal_blocks_and_survives_a_restart(tmp_path):
    run = await _run(tmp_path, None, None)
    assert run.outcome == "blocked"
    blocked = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert blocked["startup_blocked"] is True
    assert "missing" in blocked["startup_block_reason"]

    # A later launch without the flag must still refuse to start legacy fresh.
    run2 = await _run(tmp_path, blocked, None, expect=False)
    assert run2.outcome == "blocked"


@pytest.mark.asyncio
async def test_corrupt_strategy_snapshot_is_kept_aside_and_blocks(tmp_path):
    original = "{ this is not json"
    run = await _run(tmp_path, original, _wallet())
    assert run.outcome == "blocked"
    kept = tmp_path / "state.json.corrupted"
    assert kept.read_text(encoding="utf-8") == original
    blocked = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert blocked["startup_blocked"] is True
    assert "unreadable" in blocked["startup_block_reason"]


@pytest.mark.asyncio
async def test_corrupt_wallet_snapshot_blocks_without_touching_the_wallet(tmp_path):
    run = await _run(tmp_path, _state(), "{ broken")
    assert run.outcome == "blocked"
    assert run.wallet_path.read_text(encoding="utf-8") == "{ broken"
    blocked = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert blocked["startup_blocked"] is True
    assert "wallet snapshot unreadable" in blocked["startup_block_reason"]


@pytest.mark.asyncio
async def test_missing_wallet_blocks(tmp_path):
    run = await _run(tmp_path, _state(), None)
    assert run.outcome == "blocked"
    assert json.loads(run.state_path.read_text(encoding="utf-8"))["startup_blocked"] is True


@pytest.mark.parametrize("wallet_over", [
    {"live_funding_snapshot": {"symbol": "BTCUSDT", "snapshot_ms": 999}},
    {"live_funding_snapshot": None},
])
@pytest.mark.asyncio
async def test_mismatched_reciprocal_markers_block(tmp_path, wallet_over):
    run = await _run(tmp_path, _state(), _wallet(**wallet_over))
    assert run.outcome == "blocked"
    reason = json.loads(run.state_path.read_text(encoding="utf-8"))["startup_block_reason"]
    assert "marker" in reason


@pytest.mark.asyncio
async def test_legacy_state_without_live_boundary_blocks(tmp_path):
    state = _state()
    del state["live_funding"]
    run = await _run(tmp_path, state, _wallet())
    assert run.outcome == "blocked"
    reason = json.loads(run.state_path.read_text(encoding="utf-8"))["startup_block_reason"]
    assert "boundary" in reason


# ── C: WAL recovery happens before any fallback/reset ───────────────────────

@pytest.mark.asyncio
async def test_wal_intent_restores_the_pair_before_the_block(tmp_path):
    state, wallet = _pair()
    intent = {"strategy_state": copy.deepcopy(state), "paper_wallet": copy.deepcopy(wallet),
              "intent_time": time.time()}
    _write(tmp_path / "state.json", "{ crashed mid-commit")
    _write(tmp_path / "state.json.funding_intent", intent)

    run = await _run(tmp_path, None, None)
    assert run.outcome == "ran", "WAL recovery must precede the live snapshot gate"
    restored = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert restored["live_funding"]["snapshot_ms"] == 1000
    assert json.loads(run.wallet_path.read_text(encoding="utf-8"))["balance"] == 100.0
    assert not (tmp_path / "state.json.funding_intent").exists()


# ── C: effective mode decides, CLI override included ────────────────────────

@pytest.mark.asyncio
async def test_flag_with_config_paper_mode_blocks_before_state_read(tmp_path):
    run = await _run(tmp_path, None, None, config=_config(paper_mode=True))
    assert run.outcome == "blocked"
    assert not run.state_path.exists(), "paper refusal must happen before any state write"
    # refusal must not manage the running paper process nor talk to the exchange
    run.kill.assert_not_called()
    assert run.reads == []
    assert run.connector.method_calls == []


@pytest.mark.asyncio
async def test_flag_with_explicit_paper_override_blocks(tmp_path):
    run = await _run(tmp_path, None, None, paper_override=True)
    assert run.outcome == "blocked"
    assert not run.state_path.exists()
    run.kill.assert_not_called()
    assert run.reads == []
    assert run.connector.method_calls == []


@pytest.mark.asyncio
async def test_effective_real_mode_reaches_the_pair_gate(tmp_path):
    run = await _run(tmp_path, None, None, config=_config(paper_mode=True),
                     paper_override=False)
    assert run.outcome == "blocked"
    assert run.state_path.exists(), "real mode must fall through to the snapshot pair check"


@pytest.mark.asyncio
async def test_usable_pair_runs_the_real_startup(tmp_path):
    run = await _run(tmp_path, *_pair())
    assert run.outcome == "ran"
    assert "startup_blocked" not in json.loads(run.state_path.read_text(encoding="utf-8"))
    assert json.loads(run.wallet_path.read_text(encoding="utf-8"))["balance"] == 100.0


# ── E: restart after receipt / after apply never debits twice ───────────────

def _prepared():
    from core.funding import prepare_live_funding_snapshot
    state = {"base_ticker": "BTCUSDT", "virt_debt": 34.0, "funding_paid": 0.0}
    wallet = {"base_ticker": "BTCUSDT", "balance": 100.0, "positions": {}}
    return prepare_live_funding_snapshot(state, wallet, "BTCUSDT", 1000, "100", {}), wallet


def _fact(tran_id=7, timestamp=1500, income="-1"):
    return {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "asset": "USDT",
            "tranId": tran_id, "time": timestamp, "income": income}


@pytest.mark.asyncio
async def test_restart_after_receipt_applies_exactly_once(tmp_path):
    from core.funding import (FundingHalt, FundingJournal, apply_funding_paper,
                              poll_live_funding, save_funding_boundary)
    state, wallet = _prepared()
    c = AsyncMock()
    c.get_funding_income.return_value = [_fact()]
    journal = await poll_live_funding(c, state, "BTCUSDT", 2000)
    sp, wp = str(tmp_path / "state.json"), str(tmp_path / "wallet.json")
    save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())
    assert journal.get_received()

    # restart: reload from disk, apply the pending receipt once, then reload again
    for _ in range(2):
        state = json.loads(Path(sp).read_text(encoding="utf-8"))
        wallet = json.loads(Path(wp).read_text(encoding="utf-8"))
        journal = FundingJournal.from_list(state.get("funding_journal"))
        for event in journal.get_received():
            apply_funding_paper(event.signed_amount, wallet, state)
            journal.mark_applied(event.event_id)
        save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())
    assert wallet["balance"] == 99.0
    assert state["virt_debt"] == 33.0
    assert state["funding_paid"] == 1.0


@pytest.mark.asyncio
async def test_restart_after_apply_never_debits_again(tmp_path):
    from core.funding import (FundingHalt, FundingJournal, apply_funding_paper,
                              poll_live_funding, save_funding_boundary)
    state, wallet = _prepared()
    c = AsyncMock()
    c.get_funding_income.return_value = [_fact()]
    journal = await poll_live_funding(c, state, "BTCUSDT", 2000)
    for event in journal.get_received():
        apply_funding_paper(event.signed_amount, wallet, state)
        journal.mark_applied(event.event_id)
    sp, wp = str(tmp_path / "state.json"), str(tmp_path / "wallet.json")
    save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())

    for _ in range(2):
        state = json.loads(Path(sp).read_text(encoding="utf-8"))
        wallet = json.loads(Path(wp).read_text(encoding="utf-8"))
        journal = FundingJournal.from_list(state.get("funding_journal"))
        assert not journal.get_received()
        replay = await poll_live_funding(c, state, "BTCUSDT", 3000)
        assert not replay.get_received()
        save_funding_boundary(sp, wp, state, wallet, replay, FundingHalt())
    assert wallet["balance"] == 99.0
    assert state["virt_debt"] == 33.0
    assert state["funding_paid"] == 1.0


# ── E: API failure / conflict / late event block further increases ──────────

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["api_error", "conflict", "late", "already_blocked"])
async def test_source_failures_block_increases_at_runtime(tmp_path, failure):
    from core.funding import FundingEvent, FundingJournal

    now = int(time.time() * 1000)
    if failure == "late":
        state, wallet = _pair(snapshot_ms=now - 30000, boundary_ms=now - 5000)
    elif failure == "conflict":
        state, wallet = _pair(snapshot_ms=now - 30000, boundary_ms=now - 5000)
        journal = FundingJournal()
        ev = FundingEvent(event_id="binance:FUNDING_FEE:1",
                          signed_amount=Decimal("-5.0"), event_time=(now - 500) / 1000,
                          source="binance_income", source_ref="1", symbol="BTCUSDT",
                          raw_payload_hash="x")
        journal.receive(ev)
        journal.mark_applied(ev.event_id)
        state["funding_journal"] = journal.to_list()
    else:
        state, wallet = _pair(snapshot_ms=now - 30000, boundary_ms=now - 5000)
        if failure == "already_blocked":
            state["live_funding"]["blocked"] = "operator hold"

    connector = _connector()
    if failure == "api_error":
        connector.get_funding_income.side_effect = RuntimeError("API unavailable")
    elif failure == "late":
        connector.get_funding_income.return_value = [_fact(timestamp=now - 15000)]
    elif failure == "conflict":
        connector.get_funding_income.return_value = [_fact(tran_id=1, timestamp=now - 500,
                                                           income="-1")]
    elif failure == "already_blocked":
        connector.get_funding_income.side_effect = AssertionError("must not be polled")

    run = await _run(tmp_path, state, wallet, connector=connector)
    assert run.outcome == "ran", failure

    persisted = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert persisted["live_funding"]["blocked"], failure
    assert persisted.get("startup_blocked") is not True
    # no increase may be issued while the source is blocked
    assert json.loads(run.wallet_path.read_text(encoding="utf-8"))["balance"] == 100.0
    run.calc.calculate_rebalance.assert_not_called()


# -- P1: non-object snapshots must reach WAL recovery, then fail closed ------

NON_OBJECT_STRATEGIES = ["[]", "null", "5"]


@pytest.mark.parametrize("payload", NON_OBJECT_STRATEGIES)
@pytest.mark.asyncio
async def test_non_object_strategy_with_valid_wal_recovers_before_the_gate(tmp_path, payload):
    state, wallet = _pair()
    intent = {"strategy_state": copy.deepcopy(state), "paper_wallet": copy.deepcopy(wallet),
              "intent_time": time.time()}
    _write(tmp_path / "state.json", payload)
    _write(tmp_path / "state.json.funding_intent", intent)

    run = await _run(tmp_path, None, None)
    assert run.outcome == "ran", "a valid WAL must restore the pair before the gate"
    restored = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert isinstance(restored, dict)
    assert restored["live_funding"]["snapshot_ms"] == 1000
    assert json.loads(run.wallet_path.read_text(encoding="utf-8"))["balance"] == 100.0
    assert not (tmp_path / "state.json.funding_intent").exists()


@pytest.mark.parametrize("payload", NON_OBJECT_STRATEGIES)
@pytest.mark.asyncio
async def test_non_object_strategy_without_wal_durable_blocks_and_keeps_bytes(tmp_path, payload):
    run = await _run(tmp_path, payload, _wallet())
    assert run.outcome == "blocked"
    blocked = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert blocked["startup_blocked"] is True
    assert "not a JSON object" in blocked["startup_block_reason"]
    assert (tmp_path / "state.json.corrupted").read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("payload", NON_OBJECT_STRATEGIES)
@pytest.mark.asyncio
async def test_non_object_strategy_without_the_flag_refuses_without_mutation(tmp_path, payload):
    with pytest.raises(ValueError, match="not a JSON object"):
        await _run(tmp_path, payload, _wallet(), expect=False)
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == payload
    assert not (tmp_path / "state.json.corrupted").exists()


@pytest.mark.parametrize("payload", NON_OBJECT_STRATEGIES)
@pytest.mark.asyncio
async def test_non_object_wallet_blocks_and_keeps_the_wallet_bytes(tmp_path, payload):
    run = await _run(tmp_path, _state(), payload)
    assert run.outcome == "blocked"
    blocked = json.loads(run.state_path.read_text(encoding="utf-8"))
    assert blocked["startup_blocked"] is True
    assert "not a JSON object" in blocked["startup_block_reason"]
    assert run.wallet_path.read_text(encoding="utf-8") == payload


@pytest.mark.parametrize("payload", ["[]", "null"])
@pytest.mark.asyncio
async def test_non_object_wallet_without_the_flag_refuses_without_mutation(tmp_path, payload):
    with pytest.raises(ValueError, match="not a JSON object"):
        await _run(tmp_path, _state(), payload, expect=False)
    assert (tmp_path / "wallet.json").read_text(encoding="utf-8") == payload


# -- P2: corruption evidence is never overwritten, I/O failure fails closed --

@pytest.mark.asyncio
async def test_second_corruption_incident_keeps_the_first_evidence(tmp_path):
    first = "{ first incident"
    _write(tmp_path / "state.json.corrupted", first)
    second = "{ second incident"
    run = await _run(tmp_path, second, _wallet())
    assert run.outcome == "blocked"
    assert (tmp_path / "state.json.corrupted").read_text(encoding="utf-8") == first
    assert (tmp_path / "state.json.corrupted.1").read_text(encoding="utf-8") == second
    assert json.loads(run.state_path.read_text(encoding="utf-8"))["startup_blocked"] is True


@pytest.mark.asyncio
async def test_preservation_failure_keeps_the_original_and_fails_closed(tmp_path):
    original = "{ unreadable and immovable"
    run = await _run(tmp_path, original, _wallet(), corruption_rename_fails=True)
    assert run.outcome == "blocked"
    assert run.state_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "state.json.corrupted").exists()
