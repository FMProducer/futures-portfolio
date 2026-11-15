"""Deterministic branch coverage for the offline accounting ledger."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from accounting.ledger import Ledger, Rejected, Conflict, amount, utc


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.sqlite")
    led.apply("init", "initialize", "2024-02-01T00:00:00Z", project="p", account="a",
              evidence="snap0", equity="1000.00", previous_period="2024-01")
    return led


def test_amount_rejects_bad_precision(ledger):
    with pytest.raises(Rejected):
        amount("1e100")
    with pytest.raises(Rejected):
        amount("-1")
    with pytest.raises(Rejected):
        amount(5)  # not a string


def test_utc_requires_zero_offset(ledger):
    with pytest.raises(Rejected):
        utc("2024-01-01T00:00:00+02:00")
    assert utc("2024-01-01T00:00:00Z") is not None


def test_apply_requires_key(ledger):
    with pytest.raises(Rejected):
        ledger.apply("", "snapshot", "2024-02-01T00:00:00Z", evidence="e", valid=True, equity="1.0")


def test_replay_same_key_same_payload_is_idempotent(ledger):
    a = ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    b = ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    assert a == b


def test_reuse_key_with_different_payload_conflicts(ledger):
    ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    with pytest.raises(Conflict):
        ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="DIFFERENT")


def test_out_of_order_event_rejected(ledger):
    ledger.apply("snap2", "snapshot", "2024-03-01T00:00:00Z", evidence="e2", valid=True, equity="1000.00")
    with pytest.raises(Rejected, match="Out-of-order"):
        ledger.apply("snap1", "snapshot", "2024-02-15T00:00:00Z", evidence="e3", valid=True, equity="1000.00")


def test_fund_b_requires_evidence(ledger):
    with pytest.raises((Rejected, KeyError)):
        ledger.apply("fb", "fund_b", "2024-02-03T00:00:00Z", usdt="5", gas="0")


def test_user_flow_needs_classified_direction(ledger):
    with pytest.raises(Rejected, match="Classified flow required"):
        ledger.apply("uf", "user_flow", "2024-02-03T00:00:00Z", direction="sideways",
                     value="5", evidence="e")


def test_close_requires_sequential_month_and_matching_equity(ledger):
    with pytest.raises(Rejected, match="Sequential month boundary required"):
        ledger.apply("close", "close", "2024-05-01T00:00:00Z", period="2024-04",
                     valid=True, evidence="e", equity="1000.00")


def test_reserve_payout_insufficient_liability(ledger):
    with pytest.raises(Rejected, match="Insufficient unreserved liability"):
        ledger.apply("pay", "reserve_payout", "2024-02-04T00:00:00Z", id="p1",
                     value="5.00", fee_cap="1.00")


def test_calendar_and_subscription_uniqueness(ledger):
    ledger.apply("cal", "calendar", "2024-02-05T00:00:00Z", id="cal1", value="100.00",
                  due="2024-03-01T00:00:00Z")
    with pytest.raises(Rejected, match="Unique positive calendar occurrence required"):
        ledger.apply("cal2", "calendar", "2024-02-05T00:00:00Z", id="cal1", value="100.00",
                     due="2024-03-01T00:00:00Z")
    with pytest.raises(Rejected):
        ledger.apply("cal0", "calendar", "2024-02-05T00:00:00Z", id="cal0", value="0",
                     due="2024-03-01T00:00:00Z")
    ledger.apply("sub", "subscription", "2024-02-06T00:00:00Z", id="sub1", value="30.00", days="30")
    with pytest.raises(Rejected, match="Unique subscription and positive cycle required"):
        ledger.apply("sub2", "subscription", "2024-02-06T00:00:00Z", id="sub1", value="30.00", days="30")


def test_gas_protection_updates_reserve(ledger):
    ledger.apply("gas", "gas_protection", "2024-02-07T00:00:00Z", value="10.00")
    state = ledger.read()
    assert state["reserve_gas"] == "10.00"


def test_reserve_order_rejections(ledger):
    with pytest.raises(Rejected, match="Unique classified order required"):
        ledger.apply("o", "reserve_order", "2024-02-08T00:00:00Z", id="o1", category="nope",
                     purpose="purchase", value="10.00", fee_usdt="0", fee_gas="0", gas_price="0")
    with pytest.raises(Rejected, match="Positive amount and gas valuation required"):
        ledger.apply("o", "reserve_order", "2024-02-08T00:00:00Z", id="o1", category="base",
                     purpose="purchase", value="0", fee_usdt="0", fee_gas="1", gas_price="0")


def test_unknown_and_settle_terminal_conflicts(ledger):
    ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    ledger.apply("o1", "reserve_order", "2024-02-08T00:00:00Z", id="o1", category="base",
                 purpose="purchase", value="50.00", fee_usdt="1.00", fee_gas="0", gas_price="0",
                 valuation_source="s")
    ledger.apply("o1u", "unknown_order", "2024-02-09T00:00:00Z", id="o1")
    ledger.apply("o1s", "settle_order", "2024-02-10T00:00:00Z", id="o1", status="SUCCESS",
                  evidence="e", fee_usdt="1.00", fee_gas="0", gas_price="0", valuation_source="s")
    with pytest.raises(Conflict, match="Terminal order already recorded"):
        ledger.apply("o1s2", "settle_order", "2024-02-10T01:00:00Z", id="o1", status="SUCCESS",
                     evidence="e2", fee_usdt="1.00", fee_gas="0", gas_price="0", valuation_source="s")
    with pytest.raises(Conflict, match="Terminal operation"):
        ledger.apply("o1u2", "unknown_order", "2024-02-10T02:00:00Z", id="o1")


def test_settle_order_requires_terminal_evidence(ledger):
    ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    ledger.apply("o2", "reserve_order", "2024-02-08T00:00:00Z", id="o2", category="extra",
                 purpose="purchase", value="20.00", fee_usdt="1.00", fee_gas="0", gas_price="0")
    with pytest.raises(Rejected, match="Proven terminal evidence required"):
        ledger.apply("o2s", "settle_order", "2024-02-10T00:00:00Z", id="o2", status="MAYBE",
                     fee_usdt="1.00", fee_gas="0", gas_price="0")


def test_refund_and_card_branches(ledger):
    ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    # purchase order -> settle creates the "order:o3" expense (fees > 0)
    ledger.apply("o3", "reserve_order", "2024-02-08T00:00:00Z", id="o3", category="base",
                 purpose="purchase", value="30.00", fee_usdt="2.00", fee_gas="0", gas_price="0")
    ledger.apply("o3s", "settle_order", "2024-02-09T00:00:00Z", id="o3", status="SUCCESS",
                  evidence="e", fee_usdt="2.00", fee_gas="0", gas_price="0")
    with pytest.raises(Rejected, match="Refund exceeds unrefunded expense"):
        ledger.apply("ref", "refund", "2024-02-11T00:00:00Z", expense_id="order:o3",
                     value="999999", evidence="e3")
    ledger.apply("ref2", "refund", "2024-02-11T00:30:00Z", expense_id="order:o3",
                 value="1.00", evidence="e3b")
    # card order -> settle gives card_remaining, then card purchase/return branches
    ledger.apply("oc", "reserve_order", "2024-02-12T00:00:00Z", id="oc", category="base",
                 purpose="card", value="40.00", fee_usdt="0", fee_gas="0", gas_price="0")
    ledger.apply("ocs", "settle_order", "2024-02-12T01:00:00Z", id="oc", status="SUCCESS",
                  evidence="e2c", fee_usdt="0", fee_gas="0", gas_price="0")
    with pytest.raises(Rejected, match="Confirmed amount exceeds outstanding card advance"):
        ledger.apply("cp", "card_purchase", "2024-02-13T00:00:00Z", id="oc", value="9999",
                     evidence="e4")
    ledger.apply("cp2", "card_purchase", "2024-02-13T00:00:00Z", id="oc", value="10.00",
                  expense_id="exp1", evidence="e5")
    with pytest.raises(Rejected, match="Confirmed amount exceeds outstanding card advance"):
        ledger.apply("cr", "card_return", "2024-02-13T01:00:00Z", id="oc", value="35.00",
                     evidence="e6")
    ledger.apply("cr2", "card_return", "2024-02-13T01:30:00Z", id="oc", value="5.00",
                  evidence="e6b")


def test_unsupported_kind_rejected(ledger):
    with pytest.raises(Rejected, match="Unsupported event kind"):
        ledger.apply("x", "teleport", "2024-02-12T00:00:00Z", evidence="e")


def test_second_initialize_rejected(ledger):
    with pytest.raises(Rejected, match="Initialization requires a new account"):
        ledger.apply("init2", "initialize", "2024-02-02T00:00:00Z", project="p", account="a",
                     evidence="snap1", equity="1.00", previous_period="2024-01")


def test_event_before_initialize_rejected(tmp_path):
    led = Ledger(tmp_path / "early.sqlite")
    with pytest.raises(Rejected, match="Initialize first"):
        led.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")


def test_fund_b_requires_evidence(ledger):
    with pytest.raises(Rejected, match="Evidence required"):
        ledger.apply("fb", "fund_b", "2024-02-03T00:00:00Z", usdt="10", gas="0", evidence="")
    ledger.apply("fb2", "fund_b", "2024-02-03T00:00:00Z", usdt="10", gas="0", evidence="f1")
    assert ledger.read()["b_usdt"] == "10"


def _close_ledger(tmp_path):
    """Ledger whose period 2024-01 is closable: previous_period 2023-12, snapshot profit."""
    led = Ledger(tmp_path / "close.sqlite")
    led.apply("init", "initialize", "2024-01-01T00:00:00Z", project="p", account="a",
              evidence="snap0", equity="1000.00", previous_period="2023-12")
    led.apply("snap", "snapshot", "2024-01-15T00:00:00Z", equity="1500.00",
              valid=True, evidence="s1")
    led.apply("close1", "close", "2024-02-01T00:00:00Z", period="2024-01", valid=True,
              evidence="c1", equity="1500.00")
    return led


def test_close_duplicate_period_conflicts(tmp_path):
    led = _close_ledger(tmp_path)
    with pytest.raises(Conflict, match="Period already closed"):
        led.apply("close1b", "close", "2024-02-01T00:00:00Z", period="2024-01", valid=True,
                  evidence="c2", equity="1500.00")


def test_close_bad_period_format_rejected(ledger):
    with pytest.raises(Rejected, match="YYYY-MM period required"):
        ledger.apply("badp", "close", "2024-03-01T00:00:00Z", period="2024-3", valid=True,
                     evidence="c", equity="1000.00")


def test_reserve_payout_fee_cap_exceeds_value(tmp_path):
    led = _close_ledger(tmp_path)
    with pytest.raises(Rejected, match="Fee cap exceeds payout"):
        led.apply("rp1", "reserve_payout", "2024-02-02T00:00:00Z", id="pay1",
                  value="30", fee_cap="40")
    led.apply("rp2", "reserve_payout", "2024-02-02T00:00:00Z", id="pay2",
              value="30", fee_cap="5")
    assert led.read()["payouts"]["pay2"]["status"] == "PREPARED"


def test_settle_payout_validations(tmp_path):
    led = _close_ledger(tmp_path)
    led.apply("rp", "reserve_payout", "2024-02-02T00:00:00Z", id="pay", value="30", fee_cap="5")
    base = dict(id="pay", debit="30", received="28", fee="2", category="extra")
    with pytest.raises(Rejected, match="Fee lines must be a list"):
        led.apply("sp1", "settle_payout", "2024-02-03T00:00:00Z", fee_lines="oops",
                  status="SUCCESS", evidence="p1", **base)
    with pytest.raises(Rejected, match="Non-USDT fee requires asset and valuation source"):
        led.apply("sp2", "settle_payout", "2024-02-03T00:00:00Z",
                  fee_lines=[{"asset": "BTC", "source": "", "quantity": "1", "price": "1"}],
                  status="SUCCESS", evidence="p1", **base)
    with pytest.raises(Rejected, match="Positive fee quantity and price required"):
        led.apply("sp3", "settle_payout", "2024-02-03T00:00:00Z",
                  fee_lines=[{"asset": "BTC", "source": "oracle", "quantity": "0", "price": "1"}],
                  status="SUCCESS", evidence="p1", **base)
    with pytest.raises(Rejected, match="Asset fees exceed total equivalent"):
        led.apply("sp4", "settle_payout", "2024-02-03T00:00:00Z",
                  fee_lines=[{"asset": "BTC", "source": "oracle", "quantity": "4", "price": "3"}],
                  status="SUCCESS", evidence="p1", **base)
    with pytest.raises(Rejected, match="Proven final result required"):
        led.apply("sp5", "settle_payout", "2024-02-03T00:00:00Z", fee_lines=[], status="WEIRD",
                  evidence="p2", **base)
    with pytest.raises(Rejected, match="Failed payout cannot have receipt"):
        led.apply("sp6", "settle_payout", "2024-02-03T00:00:00Z", fee_lines=[], status="FAILED",
                  evidence="p3", **base)
    led.apply("sp7", "settle_payout", "2024-02-03T00:00:00Z",
              fee_lines=[{"asset": "BTC", "source": "oracle", "quantity": "1", "price": "2"}],
              status="SUCCESS", evidence="p1", **base)
    state = led.read()
    assert state["payouts"]["pay"]["status"] == "SUCCESS"
    assert state["asset_outflows_a"]["BTC"] == "1"
    assert state["liability"] == "45.00"


def test_settle_payout_bad_expense_category(tmp_path):
    led = _close_ledger(tmp_path)
    led.apply("rp", "reserve_payout", "2024-02-02T00:00:00Z", id="pay", value="30", fee_cap="5")
    with pytest.raises(Rejected, match="Expense category"):
        led.apply("sp", "settle_payout", "2024-02-03T00:00:00Z",
                  id="pay", debit="30", received="28", fee="2", status="SUCCESS",
                  evidence="px", category="junk")


def test_calendar_reserves_guard_orders(tmp_path):
    led = Ledger(tmp_path / "cal.sqlite")
    led.apply("init", "initialize", "2024-02-01T00:00:00Z", project="p", account="a",
              evidence="snap0", equity="1000.00", previous_period="2023-12")
    led.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="adv1")
    led.apply("fb", "fund_b", "2024-02-03T00:00:00Z", usdt="500", gas="1", evidence="fund1")
    led.apply("cal1", "calendar", "2024-02-04T00:00:00Z", id="rent", value="40",
              due="2024-02-05T00:00:00Z")
    led.apply("o1", "reserve_order", "2024-02-05T00:00:00Z", id="o1", category="base",
              purpose="purchase", value="40", fee_usdt="0", fee_gas="0.002",
              gas_price="30", gas_received="0", bill="rent", valuation_source="v1")
    with pytest.raises(Rejected, match="Calendar occurrence paid, reserved, or amount mismatch"):
        led.apply("o1b", "reserve_order", "2024-02-05T01:00:00Z", id="o1b", category="base",
                  purpose="purchase", value="40", fee_usdt="0", fee_gas="0",
                  gas_price="0", bill="rent")
    led.apply("cal2", "calendar", "2024-02-05T01:00:00Z", id="rent2", value="41",
              due="2024-02-06T00:00:00Z")
    with pytest.raises(Rejected, match="Mandatory payment must be a purchase"):
        led.apply("o2", "reserve_order", "2024-02-05T02:00:00Z", id="o2", category="base",
                  purpose="gas", value="41", fee_usdt="0", fee_gas="0", gas_price="0",
                  bill="rent2")
    with pytest.raises(Rejected, match="All outgoing gas reserves require a current fixed valuation"):
        led.apply("o3", "reserve_order", "2024-02-05T03:00:00Z", id="o3", category="base",
                  purpose="purchase", value="1", fee_usdt="0", fee_gas="0", gas_price="0")


def test_settle_gas_order_requires_quantity(tmp_path):
    led = Ledger(tmp_path / "gas.sqlite")
    led.apply("init", "initialize", "2024-02-01T00:00:00Z", project="p", account="a",
              evidence="snap0", equity="1000.00", previous_period="2023-12")
    led.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="adv1")
    led.apply("fb", "fund_b", "2024-02-03T00:00:00Z", usdt="100", gas="0.1", evidence="fund1")
    led.apply("o1", "reserve_order", "2024-02-04T00:00:00Z", id="o1", category="extra",
              purpose="gas", value="10", fee_usdt="0", fee_gas="0.001", gas_price="30",
              gas_received="1.0", valuation_source="v1")
    with pytest.raises(Rejected, match="Gas valuation required"):
        led.apply("o1s", "settle_order", "2024-02-05T00:00:00Z", id="o1", status="SUCCESS",
                  evidence="s1", fee_usdt="0", fee_gas="0.001", gas_price="0")
    with pytest.raises(Rejected, match="Confirmed acquired gas quantity required"):
        led.apply("o1s", "settle_order", "2024-02-05T00:00:00Z", id="o1", status="SUCCESS",
                  evidence="s2", fee_usdt="0", fee_gas="0.001", gas_price="30",
                  valuation_source="v2", gas_received="0")


def test_state_journal_mismatch_blocks_apply(ledger):
    ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    with ledger.connect() as db:
        db.execute("UPDATE state SET body=? WHERE id=1",
                   (json.dumps({"equity": "1", "tampered": True}),))
    with pytest.raises(Rejected, match="State/journal mismatch"):
        ledger.apply("adv2", "advance", "2024-02-03T00:00:00Z", evidence="e2")


def test_audit_detects_posting_mismatch(ledger):
    ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    import sqlite3
    with ledger.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM postings")
    # 431-style divergence is unreachable externally: state/journal are pinned
    # by immutability triggers, so audit() only reports mismatches via 109/425.
    assert ledger.audit()["integrity"] == "ok"


def test_audit_verifies_journal_and_state(ledger):
    ledger.apply("adv", "advance", "2024-02-02T00:00:00Z", evidence="e1")
    report = ledger.audit()
    assert report["integrity"] == "ok"
    assert report["events"] >= 2


def test_audit_detects_state_journal_mismatch(ledger, tmp_path):
    led = Ledger(tmp_path / "mismatch.sqlite")
    led.apply("init", "initialize", "2024-02-01T00:00:00Z", project="p", account="a",
              evidence="snap0", equity="1000.00", previous_period="2024-01")
    # Corrupt the materialized state so it no longer matches the journal tail.
    with led.connect() as db:
        db.execute("UPDATE state SET body=? WHERE id=1", (json.dumps({"equity": "0", "tampered": True}),))
    with pytest.raises(Rejected, match="State/journal mismatch"):
        led.audit()
