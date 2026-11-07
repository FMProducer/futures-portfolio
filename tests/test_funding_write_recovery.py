"""Funding persistence survives Windows sharing violations without double settlement."""
import json
from decimal import Decimal

import pytest

from core import funding, storage
from tests.test_funding_reserves import account, event


def test_funding_writer_retries_and_preserves_decimal(tmp_path, monkeypatch):
    target = str(tmp_path / "state.json")
    original = storage.os.replace
    attempts = []
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)

    def replace(source, destination):
        attempts.append(source)
        if len(attempts) < 3:
            raise PermissionError("injected Windows sharing violation")
        return original(source, destination)

    monkeypatch.setattr(storage.os, "replace", replace)
    funding.atomic_save_json(target, {"amount": Decimal("0.000000000000000000123")})
    assert len(attempts) == 3
    assert Decimal(json.loads((tmp_path / "state.json").read_text())["amount"]) == Decimal("0.000000000000000000123")
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]


@pytest.mark.parametrize("mode,key", [("paper", "paper_account_free_margin"), ("real", "cash_reserve_usdt")])
@pytest.mark.parametrize("failed_file", ["strategy.json", "wallet.json"])
def test_exhausted_write_and_failed_recovery_preserve_single_settlement(tmp_path, monkeypatch, mode, key, failed_file):
    config = account(tmp_path)
    state, wallet = {"virt_debt": 340}, {"balance": 1000}
    payment = event()
    receipt = funding.settle_funding_reserve(str(config), mode, "TESTUSDT", payment)
    funding.apply_funding_paper(payment.signed_amount, wallet, state, receipt)
    journal = funding.FundingJournal()
    journal.receive(payment)
    journal.mark_applied(payment.event_id)
    sp, wp = str(tmp_path / "strategy.json"), str(tmp_path / "wallet.json")
    original = storage.os.replace
    failures = []

    def replace(source, destination):
        if str(destination) == str(tmp_path / failed_file):
            failures.append(source)
            raise PermissionError("injected persistent Windows sharing violation")
        return original(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr(storage.time, "sleep", lambda _: None)
        scoped.setattr(storage.os, "replace", replace)
        with pytest.raises(PermissionError):
            funding.save_funding_boundary(sp, wp, state, wallet, journal, funding.FundingHalt())
        assert (tmp_path / "strategy.json.funding_intent").exists()
        assert funding.recover_funding_boundary(sp, wp)[0] == funding.RECOVERY_FAILED
        assert len(failures) == 10
        assert not list(tmp_path.glob(".tmp_state_*"))

    status, restored, restored_wallet = funding.recover_funding_boundary(sp, wp)
    assert status == funding.RECOVERY_RECOVERED
    assert restored_wallet == wallet and restored["funding_paid"] == 3
    assert not funding.FundingJournal.from_list(restored["funding_journal"]).get_received()
    assert json.loads((tmp_path / "strategy.json").read_text()) == restored
    assert json.loads((tmp_path / "wallet.json").read_text()) == wallet
    assert funding.recover_funding_boundary(sp, wp)[0] == funding.RECOVERY_ABSENT
    funding.settle_funding_reserve(str(config), mode, "TESTUSDT", payment)
    saved = json.loads(config.read_text())
    assert saved["portfolios"][0][key] == 4997
    assert "funding_reserve_receipts" not in saved
    assert saved["funding_reserve_totals"][key] == "-3"
