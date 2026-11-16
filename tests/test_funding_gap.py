"""Gap coverage for core/funding.py branches (offline, tmp-only paths)."""
import json
from decimal import Decimal as D

import pytest

from core import funding
from tests.test_funding_archive import apply as archive_apply
from tests.test_funding_archive import setup as archive_setup


def test_payload_key_is_a_stable_fingerprint():
    event = funding.FundingEvent(event_id="e1", signed_amount=D("-3"), event_time=100,
                                 source_ref="7", symbol="T")
    assert event.payload_key == (D("-3"), 100, "7", "default", "T")


@pytest.mark.parametrize("state,match", [
    ({"funding_journal_archived": True, "funding_archive_sequence": 0}, "missing or invalid"),
    ({"funding_journal_archived": True, "funding_archive_sequence": "5"}, "missing or invalid"),
    ({"funding_archive_sequence": 5}, "requires its archive marker"),
])
def test_from_list_validates_the_archive_checkpoint(state, match):
    with pytest.raises(ValueError, match=match):
        funding.FundingJournal.from_list([], config_path=None, state=state)


def test_archived_journal_without_ledger_context_fails_closed():
    state = {"funding_journal_archived": True, "funding_archive_sequence": 5, "base_ticker": "T"}
    journal = funding.FundingJournal.from_list([], config_path=None, state=state)
    event = funding.FundingEvent(event_id="x", signed_amount=D("-1"), event_time=1)
    with pytest.raises(ValueError, match="ledger context"):
        journal.receive(event)
    with pytest.raises(ValueError, match="ledger context"):
        journal.get_received()


def test_receive_returns_quarantined_status_of_existing_event():
    journal = funding.FundingJournal()
    first = funding.FundingEvent(event_id="q", signed_amount=D("-1"), event_time=1)
    assert journal.receive(first) == "new"
    journal.mark_quarantined("q", "late")
    assert journal.receive(funding.FundingEvent(event_id="q", signed_amount=D("-1"), event_time=1)) == "quarantined"


def _applied_journal_item(tmp_path):
    item = archive_setup(tmp_path)
    archive_apply(item, 1)
    config, state, wallet, journal, halt, sp, wp = item
    # Archive the applied event so get_received validates against the SQLite ledger
    funding.save_funding_boundary(sp, wp, state, wallet, journal, halt)
    return config, state, wallet, journal, halt, sp, wp


def test_get_received_flags_conflict_with_archived_application(tmp_path):
    config, state, wallet, journal, halt, sp, wp = _applied_journal_item(tmp_path)
    entry = dict(state["funding_journal"][-1], signed_amount="-2", status="received", applied_at=None)
    state["funding_journal"][-1] = entry
    journal = funding.FundingJournal.from_list(state["funding_journal"], config_path=str(config), state=state)
    with pytest.raises(ValueError, match="conflicts with archived application"):
        journal.get_received()


def test_get_received_restores_the_archived_canonical_event(tmp_path):
    config, state, wallet, journal, halt, sp, wp = _applied_journal_item(tmp_path)
    entry = dict(state["funding_journal"][-1], status="received", applied_at=None)
    state["funding_journal"][-1] = entry
    journal = funding.FundingJournal.from_list(state["funding_journal"], config_path=str(config), state=state)
    received = journal.get_received()
    assert len(received) == 1
    assert received[0].signed_amount == D("-1") and received[0].status == "received"


def test_latest_applied_time_tracks_marks_and_ignores_unmarked():
    journal = funding.FundingJournal()
    first = funding.FundingEvent(event_id="a", signed_amount=D("1"), event_time=1)
    journal.receive(first)
    journal.mark_applied("a")
    assert journal.latest_applied_time() == journal.events["a"].applied_at > 0
    unmarked = funding.FundingEvent(event_id="b", signed_amount=D("1"), event_time=2)
    unmarked.status = "applied"
    unmarked.applied_at = None
    journal.events["b"] = unmarked
    journal._order.append("b")
    assert journal.latest_applied_time() == journal.events["a"].applied_at


@pytest.mark.parametrize("mode,symbol", [("live", "T"), ("paper", "")])
def test_settle_requires_mode_symbol_and_identity(tmp_path, mode, symbol):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"portfolios": [{}]}))
    with pytest.raises(ValueError, match="mode, symbol and event identity"):
        funding.settle_funding_reserve(str(config), mode, symbol,
                                       funding.FundingEvent(event_id="1", signed_amount=D("-1"), event_time=1))


@pytest.mark.parametrize("state,amount", [
    ({"funding_reserve_shortfall": -1, "paper_account_free_margin": 1000}, D("-1")),
    ({"paper_account_free_margin": 1000}, D("NaN")),
])
def test_apply_funding_paper_rejects_bad_counters(state, amount):
    wallet = {"balance": 1000, "positions": {}}
    with pytest.raises(ValueError, match="finite and shortfall nonnegative"):
        funding.apply_funding_paper(amount, wallet, state)


def test_apply_funding_paper_rejects_a_mismatched_reserve_receipt():
    wallet = {"balance": 1000, "positions": {}}
    state = {"paper_account_free_margin": 1000}
    receipt = {"amount": "2", "settled": "-2", "reserve_before": "1000",
               "reserve_after": "998", "shortfall": "0"}
    with pytest.raises(ValueError, match="reserve amount mismatch"):
        funding.apply_funding_paper(D("-3"), wallet, state, receipt)


def test_record_close_result_creates_missing_leg_and_keeps_execution_id():
    halt = funding.FundingHalt()
    funding.record_close_result(halt, "BASE_LONG", "filled", D("1"), D("100"), D("0.1"), D("0"),
                                execution_id="exec-1")
    leg = halt.close_state["BASE_LONG"]
    assert leg.execution_id == "exec-1" and leg.qty_closed == D("1") and leg.status == "filled"


def _opening_inputs():
    state = {"base_ticker": "ZECUSDT", "virt_debt": "10", "initial_tpv": "100"}
    wallet = {"base_ticker": "ZECUSDT", "balance": "100", "positions": {}}
    return state, wallet


def test_prepare_live_snapshot_rejects_a_non_dict_allocation():
    state, wallet = _opening_inputs()
    with pytest.raises(ValueError, match="Invalid REAL allocation receipt"):
        funding.prepare_live_funding_snapshot(state, wallet, "ZECUSDT", 1000, "100", {}, allocation="nope")


def test_prepare_live_snapshot_rejects_an_unreconciled_allocation():
    state, wallet = _opening_inputs()
    allocation = dict(ticker="OTHERUSDT", snapshot_ms=1000, capital="100", account_wallet="100", reserve="0")
    with pytest.raises(ValueError, match="does not reconcile"):
        funding.prepare_live_funding_snapshot(state, wallet, "ZECUSDT", 1000, "100", {}, allocation=allocation)


def test_prepare_live_snapshot_rejects_virtual_exceeding_wallet():
    state, wallet = _opening_inputs()
    state["virt_debt"] = "150"
    with pytest.raises(ValueError, match="Invalid opening capital or VIRTUAL allocation"):
        funding.prepare_live_funding_snapshot(state, wallet, "ZECUSDT", 1000, "100", {})


@pytest.mark.asyncio
async def test_poll_live_funding_blocked_source_requires_operator():
    state = {"live_funding": {"symbol": "T", "snapshot_ms": 1000, "boundary_ms": 2000, "blocked": "operator"},
             "funding_model": "signed_actual", "funding_journal": []}
    with pytest.raises(ValueError, match="operator reconciliation"):
        await funding.poll_live_funding(object(), state, "T", 3000)
