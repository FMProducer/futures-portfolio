import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from pathlib import Path

from accounting import Conflict, Ledger, Rejected

T = "2026-01-01T00:00:00Z"
FEB = "2026-02-01T00:00:00Z"
MAR = "2026-03-01T00:00:00Z"


class Scenarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.sqlite3"
        self.l = Ledger(self.path)
        self.event("init", "initialize", equity="10000", project="fixed-project",
                   account="fixed-account", previous_period="2025-12", evidence="fixture:opening")

    def event(self, key, kind, at=T, **d):
        return self.l.apply(key, kind, at, **d)

    def advance(self):
        self.event("advance", "advance", evidence="fixture:advance")

    def snapshot(self, value, at=T):
        self.event("snapshot:" + at, "snapshot", at, equity=value, valid=True, evidence="fixture:snapshot")

    def close(self, period="2026-01", equity="12000", at=FEB):
        return self.event("close:" + period, "close", at, period=period, equity=equity,
                          valid=True, evidence="fixture:boundary")

    def order(self, id="o1", value="20", fee_usdt="0", fee_gas="0", at=T, **overrides):
        data = dict(id=id, value=value, fee_usdt=fee_usdt, fee_gas=fee_gas, gas_price="2",
                    valuation_source="fixture:gas", category="base", purpose="purchase")
        data.update(overrides)
        return self.event("reserve:" + id, "reserve_order", at, **data)

    def settle(self, id="o1", at=T, status="SUCCESS", fee_usdt="0", fee_gas="0"):
        return self.event("settle:" + id, "settle_order", at, id=id, status=status,
                          fee_usdt=fee_usdt, fee_gas=fee_gas, gas_price="2",
                          valuation_source="fixture:gas", evidence="fixture:receipt:" + id)

    def reward(self):
        self.snapshot("12000")
        self.close()

    def payout(self, id="p1", value="50", fee_cap="1"):
        self.event("reserve:" + id, "reserve_payout", FEB, id=id, value=value, fee_cap=fee_cap)

    def pay(self, id="p1", debit="49", received="48", fee="1", status="SUCCESS"):
        return self.event("paid:" + id, "settle_payout", FEB, id=id, debit=debit,
                          received=received, fee=fee, status=status, category="extra", evidence="fixture:withdrawal:" + id)

    def test_decimal_half_even_and_float_rejection(self):
        self.snapshot("10000.30")
        result = self.close(equity="10000.30")
        self.assertEqual(result["detail"]["reward"], "0.04")
        with self.assertRaises(Rejected):
            self.event("bad", "fund_b", FEB, usdt=1.0, gas="0", evidence="fixture")

    def test_decimal_context_is_independent_of_caller(self):
        with localcontext() as ctx:
            ctx.prec = 4
            self.snapshot("12000.123456789")
            r = self.close(equity="12000.123456789")
        self.assertEqual(r["detail"]["reward"], "325.02")

    def test_advance_is_financing_and_not_an_expense(self):
        self.advance()
        s = self.l.read()
        self.assertEqual((s["debt"], s["b_usdt"], s["expenses"]), ("200.00", "200.00", "0"))
        with self.assertRaises(Rejected):
            self.event("another-advance", "advance", evidence="fixture")

    def test_close_withholding_and_atomic_hwm(self):
        self.advance()
        self.snapshot("12000")
        r = self.close()
        self.assertEqual(r["detail"], dict(gain="2000", covered="0", profit="2000", reward="325.00",
                         withholding="200.00", net="125.00", evidence="fixture:boundary"))
        s = r["state"]
        self.assertEqual((s["debt"], s["liability"], s["hwm_reward"], s["hwm_risk"]),
                         ("0.00", "125.00", "11875.00", "11875.00"))
        self.assertFalse(s["rewarded"])

    def test_full_withholding_does_not_end_advance_period(self):
        self.advance()
        self.snapshot("11000")
        self.close(equity="11000")
        s = self.l.read()
        self.assertEqual((s["debt"], s["liability"], s["hwm_reward"]), ("50.00", "0.00", "11000.00"))
        self.assertFalse(s["rewarded"])

    def test_upper_reward_tiers_and_market_loss_preserve_liability(self):
        self.snapshot("20000")
        r = self.close(equity="20000")
        self.assertEqual(r["detail"]["reward"], "2175.00")
        self.snapshot("17000", FEB)
        r = self.close(period="2026-02", equity="17000", at=MAR)
        self.assertEqual(r["detail"]["reward"], "0.00")
        self.assertEqual(r["state"]["liability"], "2175.00")

    def test_invalid_snapshot_cannot_change_hwms_or_equity(self):
        before = self.l.read()
        with self.assertRaises(Rejected):
            self.event("invalid", "snapshot", equity="99999", valid=False, evidence="fixture")
        self.assertEqual(self.l.read(), before)

    def test_partial_payout_preserves_trading_capital_and_hwms(self):
        self.reward()
        self.payout()
        before = self.l.read()
        result = self.pay()
        after = result["state"]
        self.assertEqual(Ledger.trading(before), Decimal("11675"))
        self.assertEqual(Ledger.trading(after), Decimal("11675"))
        self.assertEqual((after["equity"], after["liability"], after["b_usdt"]), ("11951", "276.00", "48"))
        self.assertEqual((after["hwm_reward"], after["hwm_risk"]), (before["hwm_reward"], before["hwm_risk"]))
        self.assertTrue(after["rewarded"])
        # Entire reservation is released; the unused one USDT remains a liability.
        self.payout("p2", "276", "0")

    def test_unknown_payout_survives_restart_and_holds_reserve(self):
        self.reward()
        self.payout(value="325", fee_cap="1")
        self.event("timeout", "unknown_payout", FEB, id="p1")
        self.l = Ledger(self.path)
        self.assertEqual(self.l.read()["payouts"]["p1"]["status"], "UNKNOWN")
        with self.assertRaises(Rejected):
            self.payout("p2", "1", "0")
        self.pay()
        self.assertEqual(self.l.read()["liability"], "276.00")

    def test_failed_payout_fee_keeps_capital_constant_without_receipt(self):
        self.reward()
        self.payout()
        self.pay(debit="1", received="0", fee="1", status="FAILED")
        s = self.l.read()
        self.assertEqual((s["equity"], s["liability"], s["expenses"]), ("11999", "324.00", "1"))
        self.assertFalse(s["rewarded"])
        self.assertEqual(Ledger.trading(s), Decimal("11675"))

    def test_disputed_payout_retains_unknown_and_all_registers(self):
        self.reward()
        self.payout()
        self.event("timeout", "unknown_payout", FEB, id="p1")
        before = self.l.read()
        with self.assertRaises(Rejected):
            self.pay(debit="50", received="40", fee="1")
        self.assertEqual(self.l.read(), before)

    def test_event_idempotency_and_payload_conflict(self):
        first = self.event("fund", "fund_b", usdt="3.001", gas="0", evidence="fixture")
        self.assertEqual(self.event("fund", "fund_b", usdt="3.001", gas="0", evidence="fixture"), first)
        with self.assertRaises(Conflict):
            self.event("fund", "fund_b", usdt="4", gas="0", evidence="fixture")
        self.assertEqual(self.l.read()["b_usdt"], "3.001")
        self.assertEqual(self.event("different-local-id", "fund_b", usdt="3.001", gas="0", evidence="fixture"), first)
        with self.assertRaises(Conflict):
            self.event("different-provider-payload", "fund_b", usdt="9", gas="0", evidence="fixture")

    def test_terminal_payout_cannot_be_recorded_under_second_key(self):
        self.reward()
        self.payout()
        r = self.pay()
        self.assertEqual(self.pay(), r)
        self.assertEqual(self.event("second-result", "settle_payout", FEB, id="p1", debit="49", received="48", fee="1",
                         status="SUCCESS", category="extra", evidence="fixture:withdrawal:p1"), r)
        with self.assertRaises(Conflict):
            self.event("different-result", "settle_payout", FEB, id="p1", debit="49", received="48", fee="1",
                       status="SUCCESS", category="extra", evidence="fixture:another-withdrawal")

    def test_calendar_own_reserve_and_failed_fee(self):
        self.advance()
        self.event("bill", "calendar", id="bill1", value="100", due=T)
        self.order(value="100", fee_usdt="1", bill="bill1")
        r = Ledger.reserves(self.l.read(), T)
        self.assertEqual(r, dict(protected=Decimal(0), usdt=Decimal(101), gas=Decimal(0)))
        self.settle(status="FAILED", fee_usdt="1")
        s = self.l.read()
        self.assertFalse(s["calendar"]["bill1"]["paid"])
        self.assertEqual((s["b_usdt"], s["expenses"]), ("199.00", "1"))
        self.assertEqual(Ledger.reserves(s, T)["protected"], Decimal(100))

    def test_calendar_paid_only_one_occurrence(self):
        self.advance()
        for id in ("one", "two"):
            self.event("bill:" + id, "calendar", id=id, value="50", due=T)
        self.order(value="50", bill="one")
        self.settle()
        s = self.l.read()
        self.assertTrue(s["calendar"]["one"]["paid"])
        self.assertFalse(s["calendar"]["two"]["paid"])
        self.assertEqual(Ledger.reserves(s, T)["protected"], Decimal(50))

    def test_calendar_weekly_annual_and_overdue_exact_instances(self):
        for i, (value, due) in enumerate((("10", "2025-12-25T00:00:00Z"), ("10", T),
                ("10", "2026-01-08T00:00:00Z"), ("10", "2026-01-15T00:00:00Z"),
                ("10", "2026-01-22T00:00:00Z"), ("10", "2026-01-29T00:00:00Z"),
                ("120", T), ("120", T), ("999", FEB))):
            self.event("bill:" + str(i), "calendar", id=str(i), value=value, due=due)
        self.event("weekly", "subscription", id="weekly", value="10", days="7")
        self.assertEqual(Ledger.reserves(self.l.read(), T)["protected"], Decimal(300))

    def test_concurrent_budget_reservation_only_one_wins(self):
        self.advance()
        def reserve(i):
            try:
                Ledger(self.path).apply("reserve:" + str(i), "reserve_order", T, id=str(i),
                    value="120", fee_usdt="0", fee_gas="0", gas_price="2", valuation_source="fixture",
                    purpose="purchase", category="extra")
                return True
            except Rejected:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(reserve, range(2))), [False, True])
        self.assertEqual(Ledger.reserves(self.l.read(), T)["usdt"], Decimal(120))

    def test_gas_physical_reserve_and_advance_budget(self):
        self.advance()
        self.event("gas", "fund_b", usdt="0", gas="1", evidence="fixture")
        self.event("protect-gas", "gas_protection", value="0.1")
        self.order(value="10", fee_gas="0.6")
        before = self.l.read()
        with self.assertRaises(Rejected):
            self.order(id="o2", value="10", fee_gas="0.4")
        self.assertEqual(self.l.read(), before)
        self.settle(fee_gas="0.3")
        s = self.l.read()
        self.assertEqual((s["b_usdt"], s["b_gas"], s["expenses"]), ("190.00", "0.7", "10.6"))

    def test_advance_limit_includes_expenses_not_only_cash(self):
        self.advance()
        self.order(value="150")
        self.settle()
        self.event("gift", "fund_b", usdt="100", gas="0", evidence="fixture")
        with self.assertRaises(Rejected):
            self.order(id="o2", value="51")

    def test_card_advance_purchase_return_no_double_expense(self):
        self.advance()
        self.order(value="80", purpose="card")
        self.settle()
        self.assertEqual((self.l.read()["b_usdt"], self.l.read()["card"], self.l.read()["expenses"]),
                         ("120.00", "80", "0"))
        self.event("card-paid", "card_purchase", id="o1", value="60", expense_id="card:invoice", evidence="fixture:invoice")
        self.event("card-change", "card_return", id="o1", value="20", evidence="fixture:return")
        s = self.l.read()
        self.assertEqual((s["b_usdt"], s["card"], s["expenses"], s["uncovered"]), ("140.00", "0", "60", "60"))

    def test_overdue_card_advance_blocks_only_new_card_advances(self):
        self.advance()
        self.order(value="20", purpose="card")
        self.settle()
        with self.assertRaises(Rejected):
            self.order(id="o2", value="10", purpose="card", at="2026-01-09T00:00:00Z")
        self.order(id="o3", value="10", at="2026-01-09T00:00:00Z")

    def test_refund_uncovered_then_covered_credit_consumed_once(self):
        self.advance()
        self.order(value="100")
        self.settle()
        self.event("refund1", "refund", expense_id="order:o1", value="20", evidence="fixture:refund1")
        self.assertEqual(self.l.read()["uncovered"], "80")
        self.snapshot("12000")
        r = self.close()
        self.assertEqual((r["detail"]["covered"], r["detail"]["reward"]), ("80", "309.00"))
        self.event("refund2", "refund", FEB, expense_id="order:o1", value="30", evidence="fixture:refund2")
        self.assertEqual(self.l.read()["credit"], "30")
        s = self.l.read()
        self.assertEqual((s["expenses"], s["b_usdt"], s["uncovered"]), ("50", "150.00", "0"))
        second = self.close(period="2026-02", equity="12000", at=MAR)
        self.assertEqual((second["detail"]["profit"], second["detail"]["reward"], second["state"]["credit"]), ("30", "4.50", "0"))
        self.assertEqual(self.close(period="2026-02", equity="12000", at=MAR), second)

    def test_extra_refund_creates_no_reward_credit_and_cannot_over_refund(self):
        self.advance()
        self.order(value="20", category="extra")
        self.settle()
        self.event("refund", "refund", expense_id="order:o1", value="20", evidence="fixture")
        self.assertEqual((self.l.read()["expenses"], self.l.read()["credit"]), ("0", "0"))
        with self.assertRaises(Rejected):
            self.event("refund-again", "refund", expense_id="order:o1", value="1", evidence="fixture")

    def test_user_flow_adjusts_both_hwms_without_profit(self):
        self.event("deposit", "user_flow", direction="deposit", value="1000", evidence="fixture")
        r = self.close(equity="11000")
        self.assertEqual((r["detail"]["gain"], r["detail"]["reward"]), ("0", "0.00"))

    def test_close_requires_order_and_historical_evidence(self):
        before = self.l.read()
        with self.assertRaises(Rejected):
            self.close(period="2026-02", equity="10000", at=MAR)
        with self.assertRaises(Rejected):
            self.event("bad-close", "close", FEB, period="2026-01", equity="10000", valid=False, evidence="")
        self.assertEqual(self.l.read(), before)
        self.event("future", "fund_b", FEB, usdt="1", gas="0", evidence="fixture")
        with self.assertRaises(Rejected):
            self.event("late", "fund_b", T, usdt="2", gas="0", evidence="fixture")

    def test_unknown_order_retains_reserves_after_reopening(self):
        self.advance()
        self.order(value="150")
        self.event("timeout", "unknown_order", id="o1")
        self.l = Ledger(self.path)
        self.assertEqual(Ledger.reserves(self.l.read(), T)["usdt"], Decimal(150))
        with self.assertRaises(Rejected):
            self.order(id="o2", value="51")

    def test_actual_fee_exceeding_cap_records_deficit_and_blocks_purchase(self):
        self.advance()
        self.event("bill", "calendar", id="due", value="180", due=T)
        self.order(value="10", fee_usdt="1")
        self.settle(fee_usdt="15")
        self.assertTrue(self.l.read()["deficit"])
        self.assertEqual(self.l.read()["b_usdt"], "175.00")
        with self.assertRaises(Rejected):
            self.order(id="o2", value="1")

    def test_postings_reconstruct_registers_and_journal_is_immutable(self):
        self.advance()
        self.order()
        self.settle()
        self.assertEqual(self.l.audit(), dict(integrity="ok", events=4))
        with self.l.connect() as db:
            for name in ("equity", "debt", "b_usdt", "expenses", "uncovered"):
                values = db.execute("SELECT delta FROM postings WHERE register=?", (name,)).fetchall()
                self.assertEqual(sum((Decimal(v[0]) for v in values), Decimal(0)), Decimal(self.l.read()[name]))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM events")

    def test_payout_fee_in_different_asset_keeps_physical_units(self):
        self.reward()
        self.payout()
        r = self.event("paid:p1", "settle_payout", FEB, id="p1", debit="49", received="48", fee="1",
                       status="SUCCESS", category="extra", evidence="fixture:withdrawal:p1",
                       fee_lines=[dict(asset="fixture:BNB", quantity="0.002", price="500", source="fixture:BNB-price")])
        s = r["state"]
        self.assertEqual(s["asset_outflows_a"], {"fixture:BNB": "0.002"})
        self.assertEqual(s["payouts"]["p1"]["usdt_debit"], "48.000")
        self.assertEqual(Ledger.trading(s), Decimal("11675"))
        self.assertEqual(s["expenses"], "1")

    def test_gas_purchase_is_exchange_not_full_expense(self):
        self.advance()
        self.order(value="10", fee_usdt="1", purpose="gas")
        self.event("settle:o1", "settle_order", id="o1", status="SUCCESS", fee_usdt="1", fee_gas="0",
                   gas_price="2", gas_received="5", valuation_source="fixture:gas", evidence="fixture:swap")
        s = self.l.read()
        self.assertEqual((s["b_usdt"], s["b_gas"], s["expenses"]), ("189.00", "5", "1"))
        self.order(id="use", value="1", fee_gas="0.5")
        self.settle(id="use", fee_gas="0.5")
        self.assertEqual(self.l.read()["expenses"], "3.0")

    def test_concurrent_gas_reservations_only_one_wins(self):
        self.advance()
        self.event("gas", "fund_b", usdt="0", gas="1", evidence="fixture")
        def reserve(i):
            try:
                self.order(id=str(i), value="1", fee_gas="0.6")
                return True
            except Rejected:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(reserve, range(2))), [False, True])

    def test_concurrent_same_event_is_recorded_once(self):
        def fund(_):
            return self.event("fund", "fund_b", usdt="10", gas="0", evidence="fixture")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(fund, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.l.read()["b_usdt"], "10")
        self.assertEqual(self.l.audit()["events"], 2)

    def test_initial_budget_includes_card_and_gas_equivalent(self):
        self.advance()
        self.order(value="150", purpose="card")
        self.settle()
        self.event("gas", "fund_b", usdt="100", gas="5", evidence="fixture")
        with self.assertRaises(Rejected):
            self.order(id="large", value="49", fee_gas="1")
        self.order(id="fits", value="48", fee_gas="1")

    def test_corrupt_projection_is_detected_by_audit(self):
        with self.l.connect() as db:
            db.execute("UPDATE state SET body=replace(body, '10000', '10001')")
        with self.assertRaises(Rejected):
            self.l.audit()

    def test_process_crash_after_postings_before_state_rolls_back(self):
        before = self.l.read()
        program = '''
import os, sys
from accounting import Ledger
class CrashLedger(Ledger):
    def connect(self):
        db = super().connect()
        db.create_function("crash", 0, lambda: os._exit(73))
        return db
l = CrashLedger(sys.argv[1])
with l.connect() as db:
    db.execute("CREATE TRIGGER crash_state BEFORE INSERT ON state BEGIN SELECT crash(); END")
l.apply("crashed-fund", "fund_b", "2026-01-01T00:00:00Z", usdt="7", gas="0", evidence="fixture")
'''
        run = subprocess.run([sys.executable, "-c", program, str(self.path)], capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 73, run.stderr.decode())
        with self.l.connect() as db:
            db.execute("DROP TRIGGER crash_state")
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.l.read(), before)
        self.event("crashed-fund", "fund_b", usdt="7", gas="0", evidence="fixture")
        self.assertEqual(self.l.read()["b_usdt"], "7")


if __name__ == "__main__":
    unittest.main(verbosity=2)
