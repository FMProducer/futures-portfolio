"""Funding belongs to external reserves, never strategy equity or VIRTUAL."""
import copy
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
import json

import pytest

from backtest.backtest_rebalance import BacktestState
from core.funding import (FundingEvent, FundingHalt, FundingJournal,
                          apply_funding_backtest, apply_funding_paper,
                          recover_funding_boundary, reserve_funding,
                          save_funding_boundary, settle_funding_reserve)
from core.storage import safe_save_json_sync


def account(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"portfolios": [{"cash_reserve_usdt": 5000,
                                                "paper_account_free_margin": 5000}]}))
    return path


def event(identity="1", amount="-3"):
    return FundingEvent(event_id=identity, signed_amount=D(amount), event_time=100)


@pytest.mark.parametrize("amount,expected", [("-3", 4997), ("3", 5003)])
def test_backtest_funding_preserves_portfolio_and_records_expense(amount, expected):
    state = BacktestState(D(1000), "TESTUSDT", D(660), virt_debt=D(340),
                          account_free_margin=D(5000), profit_book={"profit_budget": {"balance": "50", "receipts": {}, "blocked": False}})
    apply_funding_backtest(D(amount), state)
    assert state.account_free_margin == expected
    assert state.virt_debt == 340 + D(amount) and state.val_cash == 660
    assert state.get_tpv(D(100)) == 1000 + D(amount)
    assert state.get_tpv(D(100)) + state.funding_accounting_offset == 1000
    assert state.check_accounting(D(100), 0) == 0
    assert D(state.profit_book["profit_budget"]["balance"]) == 50 + min(D(amount), D(0))


@pytest.mark.parametrize("live,key", [(False, "paper_account_free_margin"), (True, "cash_reserve_usdt")])
@pytest.mark.parametrize("amount,expected", [("-3", 4997), ("3", 5003)])
def test_wallet_funding_only_changes_mode_reserve(live, key, amount, expected):
    wallet = {"balance": 1000, "positions": {"TESTUSDT_LONG": 1}}
    before = copy.deepcopy(wallet)
    state = {"virt_debt": 340, key: 5000}
    if live:
        state["live_funding"] = {"symbol": "TESTUSDT"}
    apply_funding_paper(D(amount), wallet, state)
    assert wallet["positions"] == before["positions"]
    assert D(str(wallet["balance"])) + D(str(state["funding_accounting_offset"])) == 1000
    assert state["virt_debt"] == 340 + D(amount)
    if D(amount) < 0:
        assert D(wallet["profit_budget"]["balance"]) == D(amount)
    assert state[key] == expected and state["funding_paid"] == -D(amount)


def test_insufficient_reserve_never_borrows_from_portfolio():
    state = BacktestState(D(1000), "TESTUSDT", D(660), virt_debt=D(340), account_free_margin=D(3))
    result = apply_funding_backtest(D(-10), state)
    assert state.account_free_margin == 0 and result["shortfall"] == 7
    assert state.get_funding_shortfall() == 7
    assert state.get_tpv(D(100)) + state.funding_accounting_offset == 1000 and state.virt_debt == 330
    assert state.check_accounting(D(100), 0) == 0


@pytest.mark.parametrize("amount,reserve", [("NaN", "1"), ("1", "Infinity"), ("1", "-1")])
def test_invalid_reserve_funding_rejected(amount, reserve):
    with pytest.raises(ValueError):
        reserve_funding(amount, reserve)


@pytest.mark.parametrize("mode,key", [("real", "cash_reserve_usdt"), ("paper", "paper_account_free_margin")])
def test_shared_reserve_duplicate_and_stale_config_write(tmp_path, mode, key):
    path = account(tmp_path)
    stale = json.loads(path.read_text())
    first = settle_funding_reserve(str(path), mode, "TESTUSDT", event())
    again = settle_funding_reserve(str(path), mode, "TESTUSDT", event())
    assert {k:v for k,v in first.items() if k != "current_reserve"} == {k:v for k,v in again.items() if k != "current_reserve"}
    assert D(first["current_reserve"]) == D(again["current_reserve"])
    stale["probation_paper"] = {"OTHER": 1000}
    safe_save_json_sync(str(path), stale)
    latest = json.loads(path.read_text())
    assert latest["portfolios"][0][key] == 4997
    assert len(latest["funding_reserve_receipts"]) == 1
    assert latest["probation_paper"] == {"OTHER": 1000}
    with pytest.raises(ValueError, match="identity"):
        settle_funding_reserve(str(path), mode, "TESTUSDT", event(amount="-4"))


@pytest.mark.parametrize("duplicate", [False, True])
def test_concurrent_bots_share_one_reserve(tmp_path, duplicate):
    path = account(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(settle_funding_reserve, str(path), "real", "TESTUSDT", event("same" if duplicate else str(i)))
                   for i in range(12)]
        for future in futures:
            future.result()
    config = json.loads(path.read_text())
    assert config["portfolios"][0]["cash_reserve_usdt"] == (4997 if duplicate else 4964)
    assert len(config["funding_reserve_receipts"]) == (1 if duplicate else 12)


def test_reserve_receipt_recovers_failure_before_wallet_commit(tmp_path):
    path = account(tmp_path)
    old_state = {"virt_debt": 340, "funding_paid": 0}
    old_wallet = {"balance": 1000}
    receipt = settle_funding_reserve(str(path), "paper", "TESTUSDT", event())
    # A crash here leaves the old pair and a committed account receipt.
    state, wallet = copy.deepcopy(old_state), copy.deepcopy(old_wallet)
    receipt = settle_funding_reserve(str(path), "paper", "TESTUSDT", event())
    apply_funding_paper(D(-3), wallet, state, receipt)
    journal = FundingJournal()
    journal.receive(event())
    journal.mark_applied("1")
    sp, wp = str(tmp_path / "strategy.json"), str(tmp_path / "wallet.json")
    save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())
    assert recover_funding_boundary(sp, wp)[0] == "ABSENT"
    assert json.loads(path.read_text())["portfolios"][0]["paper_account_free_margin"] == 4997
    assert json.loads((tmp_path / "wallet.json").read_text())["balance"] == 997
    assert json.loads((tmp_path / "strategy.json").read_text())["funding_paid"] == 3


@pytest.mark.parametrize("amount,expected", [("-3", 4997), ("3", 5003)])
@pytest.mark.asyncio
async def test_paper_loop_commits_only_external_reserve(amount, expected):
    from unittest.mock import patch
    from test_main_coverage import _make_config, _make_state, _make_paper_state, _run_rebalance
    config = _make_config(equity_trailing_stop_pct=0)
    config["portfolios"][0].update(paper_account_free_margin=5000, siphoning_threshold_pct=0)
    state = _make_state(started_at=1, virt_debt=34, funding_model="signed_actual",
                        funding_journal=[event(amount=amount).to_dict()])
    committed = []

    def save(sp, wp, strategy, wallet, journal, halt):
        strategy["funding_journal"] = journal.to_list()
        committed.append(copy.deepcopy((strategy, wallet)))

    with patch("core.funding.save_funding_boundary", side_effect=save):
        await _run_rebalance(config, state, _make_paper_state(), loop_limit=2)
    assert committed
    strategy, wallet = committed[-1]
    assert wallet["balance"] == 100 + D(amount) and strategy["virt_debt"] == 34 + D(amount)
    assert D(str(wallet["balance"])) + D(str(strategy["funding_accounting_offset"])) == 100
    assert strategy["funding_paid"] == -D(amount)
    assert strategy["funding_reserve_key"] == "paper_account_free_margin"
    assert strategy["funding_reserve_balance"] == expected
    assert strategy["funding_journal"][0]["status"] == "applied"


def test_config_precision_loss_blocks_instead_of_discarding_payment(tmp_path):
    path = account(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="precision"):
        settle_funding_reserve(str(path), "real", "TESTUSDT", event(amount="-0.000000000000000001"))
    assert path.read_bytes() == before


def test_reserve_funding_is_not_subtracted_from_dashboard_portfolio_pnl():
    from backtest.dashboard_attribution import reconstruct_attribution
    result = reconstruct_attribution([100, 100], [1000, 1000], [], initial_capital=1000,
        final_equity=1000, funding_model="signed_actual", funding_source="cash_reserve_usdt",
        events=[{"kind": "funding", "bar": 1, "funding_amount": -3}])
    assert result["totals"]["funding"] == 0 and result["totals"]["residual"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["signed_actual", "legacy_constant"])
async def test_backtest_empty_reserve_halts_without_portfolio_fallback(tmp_path, model):
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest
    times = pd.date_range("2026-01-01", periods=4, freq="min", tz="UTC")
    prices = [100.] * 4
    pd.DataFrame({"time": times, "open": prices, "high": prices, "low": prices,
                  "close": prices, "volume": prices}).to_feather(tmp_path / "TESTUSDT_live.feather")
    journal = tmp_path / "funding.json"
    journal.write_text(json.dumps([{"event_id": "empty", "signed_amount": -2,
        "event_time": times[1].timestamp(), "status": "received"}]))
    config = {"base_ticker": "TESTUSDT", "funding_model": model,
              "funding_scenario": str(journal), "max_drawdown_limit": 100,
              "portfolios": [{"initial_capital": 1000, "cash_reserve_usdt": 0,
                  "targets": {"BASE_LONG": {"share": .25, "leverage": 3},
                              "BASE_SHORT": {"share": .25, "leverage": 3}, "VIRTUAL": {"share": .5}}}]}
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    result = await run_backtest(str(cfg), str(tmp_path), days=1, commission=0, slippage=0, quiet=True)
    assert result and result["accounting_ok"] and result["funding_halt_active"]
    assert result["cash_reserve_usdt"] == 0 and result["funding_reserve_shortfall"] > 0
    assert result["actual_portfolio_equity"] == pytest.approx(1000)
    assert not any(f["bar"] > 1 for f in result["fills"])
    closes = [f for f in result["fills"] if f["trigger"] == "funding_shortfall"]
    assert {f["leg"] for f in closes} == {"BASE_LONG", "BASE_SHORT"}


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_mode", [True, False])
async def test_runtime_shortfall_blocks_increases_and_preserves_wallet(paper_mode):
    from unittest.mock import AsyncMock, MagicMock, patch
    from test_main_coverage import _make_config, _make_state, _make_paper_state, _run_rebalance
    config = _make_config(paper_mode=paper_mode, equity_trailing_stop_pct=0)
    config["portfolios"][0].update(paper_account_free_margin=1, cash_reserve_usdt=0,
                                    siphoning_threshold_pct=0)
    if not paper_mode:
        config["portfolios"][0]["cash_reserve_usdt"] = 1
    state = _make_state(started_at=1, virt_debt=34, funding_model="signed_actual",
                        funding_journal=[event(amount="-2").to_dict()])
    committed, applications = [], []
    executor = MagicMock()
    executor.execute_actions = AsyncMock()
    original = apply_funding_paper

    def apply(amount, wallet, current, receipt):
        before = (copy.deepcopy(wallet), current["virt_debt"])
        result = original(amount, wallet, current, receipt)
        applications.append((before, (copy.deepcopy(wallet), current["virt_debt"])))
        return result

    def save(sp, wp, current, wallet, journal, halt):
        current["funding_journal"] = journal.to_list()
        committed.append(copy.deepcopy((current, wallet)))

    with patch("core.funding.save_funding_boundary", side_effect=save), \
         patch("core.funding.apply_funding_paper", side_effect=apply), \
         patch("futures_portfolio.core.main.PortfolioExecutor", return_value=executor):
        await _run_rebalance(config, state, _make_paper_state(), loop_limit=2, real_mode=not paper_mode)
    assert applications
    for (before, v_before), (after, v_after) in applications:
        assert v_after == v_before - 2
        assert {k: v for k, v in before.items() if k not in ("profit_budget", "balance")} == {
            k: v for k, v in after.items() if k not in ("profit_budget", "balance")}
        assert after["balance"] == before["balance"] - 2
        assert D(after["profit_budget"]["balance"]) == D(before.get("profit_budget", {}).get("balance", "0")) - 2
    assert any(current.get("funding_halt", {}).get("active") for current, _ in committed)
    assert committed[-1][0]["funding_reserve_shortfall"] == 1
    assert committed[-1][1]["balance"] == 98
    assert committed[-1][0]["funding_accounting_offset"] == 2
    executor.execute_actions.assert_not_awaited()


def test_reserve_and_wallet_recover_crash_between_pair_writes(tmp_path, monkeypatch):
    import core.funding as funding
    path = account(tmp_path)
    state, wallet = {"virt_debt": 340}, {"balance": 1000}
    receipt = settle_funding_reserve(str(path), "real", "TESTUSDT", event())
    apply_funding_paper(D(-3), wallet, state, receipt)
    journal = FundingJournal()
    journal.receive(event())
    journal.mark_applied("1")
    sp, wp = str(tmp_path / "strategy.json"), str(tmp_path / "wallet.json")
    original = funding.atomic_save_json

    def fail_wallet(target, data):
        if target == wp:
            raise OSError("injected wallet write failure")
        return original(target, data)

    with monkeypatch.context() as scoped:
        scoped.setattr(funding, "atomic_save_json", fail_wallet)
        with pytest.raises(OSError):
            save_funding_boundary(sp, wp, state, wallet, journal, FundingHalt())
    status, recovered, restored_wallet = recover_funding_boundary(sp, wp)
    assert status == "RECOVERED"
    assert restored_wallet["balance"] == 997 and recovered["virt_debt"] == 337
    assert recovered["funding_paid"] == 3
    assert not FundingJournal.from_list(recovered["funding_journal"]).get_received()
    assert json.loads(path.read_text())["portfolios"][0]["cash_reserve_usdt"] == 4997


def test_real_reserve_reload_requires_matching_funding_receipts(tmp_path):
    from core.siphoning import funded_reserve_change
    path = account(tmp_path)
    previous = json.loads(path.read_text())
    settle_funding_reserve(str(path), "real", "TESTUSDT", event())
    current = json.loads(path.read_text())
    assert funded_reserve_change(previous, current)
    forged = copy.deepcopy(current)
    forged["portfolios"][0]["cash_reserve_usdt"] -= 1
    assert not funded_reserve_change(previous, forged)
    forged = copy.deepcopy(current)
    forged["funding_reserve_receipts"] = {}
    assert not funded_reserve_change(current, forged)


def test_stale_writer_preserves_received_funding(tmp_path):
    path = account(tmp_path)
    stale = json.loads(path.read_text())
    settle_funding_reserve(str(path), "paper", "TESTUSDT", event(amount="3"))
    safe_save_json_sync(str(path), stale)
    assert json.loads(path.read_text())["portfolios"][0]["paper_account_free_margin"] == 5003


