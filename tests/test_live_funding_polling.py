"""Live source activation, overlap, durable exactly-once accounting and rejection."""
import copy
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest
from core.funding import (prepare_live_funding_snapshot, poll_live_funding,
    FundingJournal, FundingHalt, apply_funding_paper, save_funding_boundary,
    recover_funding_boundary, RECOVERY_RECOVERED)


def seed():
    state = {"base_ticker": "ZECUSDT", "virt_debt": 34., "funding_paid": 0.}
    wallet = {"base_ticker": "ZECUSDT", "balance": 100., "positions": {}}
    return prepare_live_funding_snapshot(state, wallet, "ZECUSDT", 1000, "100", {}), wallet


def fact(identity=1, timestamp=1500, amount="-0.000049"):
    return {"symbol": "ZECUSDT", "incomeType": "FUNDING_FEE", "asset": "USDT",
        "tranId": identity, "time": timestamp, "income": amount}


@pytest.mark.asyncio
async def test_overlap_commits_once_and_preserves_cash(tmp_path):
    state, wallet = seed()
    c = AsyncMock()
    c.get_funding_income.return_value = [fact()]
    journal = await poll_live_funding(c, state, "ZECUSDT", 2000)
    assert wallet["balance"] == 100 and state["virt_debt"] == 34
    before_cash = Decimal(str(wallet["balance"])) - Decimal(str(state["virt_debt"]))
    for event in journal.get_received():
        apply_funding_paper(event.signed_amount, wallet, state)
        journal.mark_applied(event.event_id)
    sp, wp = str(tmp_path / "state.json"), str(tmp_path / "wallet.json")
    save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())
    before = copy.deepcopy((state, wallet))
    received = await poll_live_funding(c, state, "ZECUSDT", 2500)
    assert not received.get_received()
    assert wallet == before[1]
    assert state["funding_paid"] == .000049
    assert abs(Decimal(str(wallet["balance"])) - Decimal(str(state["virt_debt"])) - before_cash) < Decimal("1e-12")


@pytest.mark.asyncio
async def test_pending_journal_survives_restart_before_application(tmp_path):
    state, wallet = seed(); c = AsyncMock()
    c.get_funding_income.return_value = [fact(amount="3")]
    journal = await poll_live_funding(c, state, "ZECUSDT", 2000)
    sp, wp = str(tmp_path / "state.json"), str(tmp_path / "wallet.json")
    save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())
    import json
    state = json.loads((tmp_path / "state.json").read_text())
    journal = await poll_live_funding(c, state, "ZECUSDT", 2500)
    assert len(journal.get_received()) == 1
    event = journal.get_received()[0]
    apply_funding_paper(event.signed_amount, wallet, state)
    assert wallet["balance"] == 103 and state["virt_debt"] == 37
    assert state["funding_paid"] == -3


@pytest.mark.asyncio
async def test_late_event_quarantined_without_wallet_change():
    state, wallet = seed(); c = AsyncMock()
    c.get_funding_income.return_value = []
    await poll_live_funding(c, state, "ZECUSDT", 2000)
    c.get_funding_income.return_value = [fact()]
    with pytest.raises(ValueError, match="Late"):
        await poll_live_funding(c, state, "ZECUSDT", 3000)
    assert state["funding_journal"][0]["status"] == "quarantined"
    assert state["live_funding"]["boundary_ms"] == 2000
    assert wallet["balance"] == 100


@pytest.mark.asyncio
async def test_api_error_does_not_advance_boundary():
    state, wallet = seed(); before = copy.deepcopy(state); c = AsyncMock()
    c.get_funding_income.side_effect = RuntimeError("API unavailable")
    with pytest.raises(RuntimeError):
        await poll_live_funding(c, state, "ZECUSDT", 2000)
    assert state == before and wallet["balance"] == 100


@pytest.mark.asyncio
async def test_snapshot_history_excluded_and_same_id_conflict_rejected():
    state, wallet = seed(); c = AsyncMock()
    c.get_funding_income.return_value = [fact(9, 1000), fact()]
    j = await poll_live_funding(c, state, "ZECUSDT", 2000)
    assert len(j) == 1
    c.get_funding_income.return_value = [fact(amount="2")]
    with pytest.raises(ValueError, match="conflict"):
        await poll_live_funding(c, state, "ZECUSDT", 2500)
    assert wallet["balance"] == 100


@pytest.mark.parametrize("change", [{"exchange_wallet": "101"}, {"snapshot_ms": 0},
    {"positions": {"ZECUSDT_LONG": {"qty": 1}}}])
def test_mismatched_snapshot_rejected(change):
    state = {"base_ticker": "ZECUSDT", "virt_debt": 34.}
    wallet = {"base_ticker": "ZECUSDT", "balance": 100., "positions": {}}
    kwargs = dict(state=state, wallet=wallet, symbol="ZECUSDT", snapshot_ms=1000,
                  exchange_wallet="100", positions={})
    kwargs.update(change)
    with pytest.raises(ValueError):prepare_live_funding_snapshot(**kwargs)
    assert "live_funding" not in state


@pytest.mark.asyncio
@pytest.mark.parametrize("api_error", [False, True])
async def test_real_loop_polls_and_commits_before_calculator(api_error):
    import time
    from unittest.mock import patch
    from test_main_coverage import _make_config, _make_state, _make_paper_state, _run_rebalance
    from core.funding import poll_live_funding as real_poll

    now = int(time.time() * 1000)
    state = _make_state(virt_debt=34., funding_model="signed_actual", funding_journal=[],
        live_funding={"symbol": "BTCUSDT", "snapshot_ms": now - 10000,
                      "boundary_ms": now - 10000, "blocked": ""})
    wallet = _make_paper_state()
    saves = []

    async def poll(connector, current, symbol, end_ms):
        item = dict(fact(timestamp=now - 1000), symbol="BTCUSDT")
        connector.get_funding_income = AsyncMock(return_value=[item])
        if api_error:connector.get_funding_income.side_effect = RuntimeError("API unavailable")
        return await real_poll(connector, current, symbol, end_ms)

    def save(sp, wp, current, shadow, journal, halt):
        from core.funding import save_funding_state
        save_funding_state(journal, halt, current)
        saves.append(copy.deepcopy((current, shadow)))
        if current["live_funding"].get("blocked") or current.get("funding_paid", 0):
            raise BaseException("ProcessExit")

    with patch("core.funding.poll_live_funding", side_effect=poll), \
         patch("core.funding.save_funding_boundary", side_effect=save):
        await _run_rebalance(_make_config(paper_mode=False), state, wallet, loop_limit=2)
    assert saves
    current, shadow = saves[-1]
    if api_error:
        assert current["live_funding"]["blocked"]
        assert shadow["balance"] == 100
    else:
        assert current["funding_journal"][0]["status"] == "applied"
        assert current["funding_paid"] == .000049
        assert shadow["balance"] == pytest.approx(99.999951)


@pytest.mark.asyncio
async def test_wallet_write_failure_restores_event_and_wallet_together(tmp_path, monkeypatch):
    import core.funding as funding
    state, wallet = seed(); c = AsyncMock()
    c.get_funding_income.return_value = [fact(amount="-1")]
    journal = await poll_live_funding(c, state, "ZECUSDT", 2000)
    event = journal.get_received()[0]
    apply_funding_paper(event.signed_amount, wallet, state)
    journal.mark_applied(event.event_id)
    sp, wp = str(tmp_path / "state.json"), str(tmp_path / "wallet.json")
    original = funding.atomic_save_json
    def fail_wallet(path, data):
        if path == wp:raise OSError("injected wallet write failure")
        original(path, data)
    with monkeypatch.context() as scoped:
        scoped.setattr(funding, "atomic_save_json", fail_wallet)
        with pytest.raises(OSError):
            save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())
    status, recovered, shadow = recover_funding_boundary(sp, wp)
    assert status == RECOVERY_RECOVERED
    assert shadow["balance"] == 99 and recovered["virt_debt"] == 33
    assert shadow["live_funding_snapshot"] == {"symbol": "ZECUSDT", "snapshot_ms": 1000}
    assert recovered["funding_paid"] == 1
    assert not (await poll_live_funding(c, recovered, "ZECUSDT", 2500)).get_received()


@pytest.mark.asyncio
async def test_live_snapshot_zero_virtual_qty_never_adds_virtual_again():
    import time
    from unittest.mock import patch
    from test_main_coverage import _make_config, _make_state, _make_paper_state, _run_rebalance
    now = int(time.time() * 1000)
    state = _make_state(virt_qty=0, virt_debt=34., funding_model="signed_actual",
        live_funding={"symbol": "BTCUSDT", "snapshot_ms": now-1000, "boundary_ms": now-1000, "blocked": ""})
    observed = []
    async def poll(c, current, symbol, end_ms):
        observed.append(current["virt_debt"])
        raise BaseException("ProcessExit")
    with patch("core.funding.poll_live_funding", side_effect=poll):
        await _run_rebalance(_make_config(paper_mode=False), state, _make_paper_state())
    assert observed == [34.]


@pytest.mark.asyncio
async def test_live_snapshot_missing_wallet_blocks_without_fallback():
    from test_main_coverage import _make_config, _make_state, _run_rebalance
    state = _make_state(live_funding={"symbol": "BTCUSDT", "snapshot_ms": 1000, "boundary_ms": 1000})
    with pytest.raises(ValueError, match="cannot be migrated"):
        await _run_rebalance(_make_config(paper_mode=False), state, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["blacklist", "capital", "blocked"])
async def test_live_snapshot_runtime_rebase_cannot_mutate_wallet(reason):
    import time
    from unittest.mock import patch
    from test_main_coverage import _make_config, _make_state, _make_paper_state, _run_rebalance
    state = _make_state(virt_debt=34., initial_tpv=99. if reason == "capital" else 100.,
        live_funding={"symbol": "BTCUSDT", "snapshot_ms": 1000, "boundary_ms": 1000,
                      "blocked": "prior error" if reason == "blocked" else ""})
    config = _make_config(paper_mode=False)
    if reason == "blacklist":config["toxic_blacklist_real"] = {"BTCUSDT": time.time()+1000}
    captured = []
    def save(sp, wp, current, wallet, journal, halt):
        captured.append(copy.deepcopy((current, wallet)))
        raise BaseException("ProcessExit")
    with patch("core.funding.save_funding_boundary", side_effect=save):
        await _run_rebalance(config, state, _make_paper_state(balance=90.))
    assert captured and captured[-1][1]["balance"] == 90.
    assert captured[-1][0]["virt_debt"] == 34.
    assert captured[-1][0]["live_funding"]["blocked"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_wallet_marker_blocks_missing_or_legacy_strategy(missing):
    from test_main_coverage import _make_config, _make_state, _make_paper_state, _run_rebalance
    wallet = _make_paper_state(balance=6.99,
        live_funding_snapshot={"symbol": "BTCUSDT", "snapshot_ms": 1000})
    state = {} if missing else _make_state()
    with pytest.raises(ValueError, match="matching strategy"):
        await _run_rebalance(_make_config(paper_mode=False), state, wallet)


def test_wal_persists_reciprocal_snapshot_identity(tmp_path):
    import json
    state, wallet = seed()
    sp, wp = str(tmp_path / "state.json"), str(tmp_path / "wallet.json")
    save_funding_boundary(sp, wp, state, wallet, FundingJournal(), FundingHalt())
    saved = json.loads((tmp_path / "wallet.json").read_text())
    assert saved["live_funding_snapshot"] == {"symbol": "ZECUSDT", "snapshot_ms": 1000}
    wallet["live_funding_snapshot"]["snapshot_ms"] = 999
    with pytest.raises(ValueError, match="identity mismatch"):
        save_funding_boundary(sp, wp, state, wallet, FundingJournal(), FundingHalt())
