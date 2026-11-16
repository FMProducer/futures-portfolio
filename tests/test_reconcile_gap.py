"""Gap coverage for rebalance/reconcile.py read-only history and offline apply errors."""
import json

import pytest

from rebalance.durable import Store
from rebalance.kernel import Action, Config, Portfolio
from rebalance.reconcile import ReadError, analyze, apply_offline, history


class FixedReader:
    def __init__(self, rows):
        self.rows = rows

    def get(self, path, **params):
        return self.rows


def test_history_rejects_an_invalid_window(tmp_path):
    reader = FixedReader([])
    with pytest.raises(ValueError, match="nonnegative interval shorter than seven days"):
        history(reader, "/fapi/v1/income", -1, 100)
    with pytest.raises(ValueError, match="nonnegative interval shorter than seven days"):
        history(reader, "/fapi/v1/income", 1.0, 100)


def test_history_rejects_a_non_list_response(tmp_path):
    with pytest.raises(ReadError, match="invalid_history_response"):
        history(FixedReader({"rows": 1}), "/fapi/v1/income", 0, 100)


def test_history_rejects_a_row_outside_the_requested_window(tmp_path):
    rows = [{"time": 200, "incomeType": "TRANSFER", "tranId": 1}]
    with pytest.raises(ReadError, match="history_outside_requested_window"):
        history(FixedReader(rows), "/fapi/v1/income", 0, 100)


def test_history_rejects_a_conflicting_identity(tmp_path):
    rows = [{"time": 5, "incomeType": "TRANSFER", "tranId": 7, "income": "1"},
            {"time": 5, "incomeType": "TRANSFER", "tranId": 7, "income": "2"}]
    with pytest.raises(ReadError, match="conflicting_history_identity"):
        history(FixedReader(rows), "/fapi/v1/income", 0, 100)


def test_analyze_rejects_invalid_trade_economics(tmp_path):
    trade = {"symbol": "BTCUSDT", "id": "1", "orderId": "9", "side": "BUY", "positionSide": "LONG",
             "qty": "0", "price": "100", "commission": "0", "realizedPnl": "0", "time": 1,
             "commissionAsset": "USDT"}
    with pytest.raises(ValueError, match="invalid trade economics"):
        analyze([trade], [])
    trade["qty"] = "1"
    trade["side"] = "HOLD"
    with pytest.raises(ValueError, match="invalid trade economics"):
        analyze([trade], [])


def test_analyze_reports_unmatched_trading_income(tmp_path):
    incomes = [{"incomeType": "COMMISSION", "tranId": "42", "asset": "USDT", "income": "-1",
                "symbol": "BTCUSDT", "tradeId": "9", "time": 1}]
    result = analyze([], incomes)
    assert "UNMATCHED_TRADING_INCOME" in result["blocking_reasons"]


def test_analyze_emits_cash_facts_from_income_only(tmp_path):
    incomes = [{"incomeType": "FUNDING_FEE", "tranId": "42", "asset": "USDT", "income": "-1", "time": 1},
               {"incomeType": "TRANSFER", "tranId": "43", "asset": "USDT", "income": "5", "time": 2}]
    result = analyze([], incomes)
    assert result["blocking_reasons"] == []
    assert [row["kind"] for row in result["cash"]] == ["FUNDING", "EXTERNAL"]
    assert result["trading_authorized"] is False


def _open_store(tmp_path):
    store = Store(tmp_path / "reconcile.sqlite")
    store.initialize(Portfolio(Config(".001", "5.3", "7", ".0004", ".7", ".7", ".09"), "10000"))
    return store


def _prepare_open(store, client, kind="ADD", units=1000, side="LONG"):
    revision = store.load()[1]
    store.prepare("prep-" + client, client, Action(kind, side, units), "100", "1", "100",
                  revision, entry=(kind == "ADD"))
    store.transition("disp-" + client, client, "DISPATCHING")
    store.transition("open-" + client, client, "OPEN")


def _cancel(store, client):
    store.finalize("fin-" + client, client, "CANCELED", 0)


def _response(order_id, status, executed, client="one", side="BUY", position="LONG"):
    return {"clientOrderId": client, "symbol": "BTCUSDT", "positionSide": position, "side": side,
            "orderId": order_id, "origQty": "1", "executedQty": executed, "status": status}


def _trade(trade_id, qty, order_id, side="BUY", position="LONG", time=123, price="100", fee=".01", realized="0"):
    return {"id": trade_id, "symbol": "BTCUSDT", "order_id": order_id, "side": side,
            "position_side": position, "qty": qty, "price": price, "fee": fee, "realized": realized,
            "time": time}


def _bundle(trades=(), cash=()):
    return {"blocking_reasons": [], "trades": list(trades), "cash": list(cash),
            "cash_source_rule": "trades for PnL/fees; income for funding/transfers",
            "trading_authorized": False}


def test_apply_offline_rejects_a_stale_revision(tmp_path):
    store = _open_store(tmp_path)
    with pytest.raises(ValueError, match="stale reconciliation revision"):
        apply_offline(store, "ev-stale", _bundle(), {}, symbol="BTCUSDT", expected_revision=999)


def test_apply_offline_rejects_duplicate_exchange_identity(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    _cancel(store, "one")
    _prepare_open(store, "two")
    evidence = {"one": _response("77", "CANCELED", "0"), "two": _response("77", "NEW", "0", client="two")}
    with pytest.raises(ValueError, match="duplicate exchange order identity"):
        apply_offline(store, "ev-dup", _bundle(), evidence, symbol="BTCUSDT",
                      expected_revision=store.load()[1])


def test_apply_offline_rejects_an_uncovered_outstanding_intent(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    _cancel(store, "one")
    # prepare now serializes outstanding intents, so the second order is injected at table level
    with store.connection() as db:
        db.execute("INSERT INTO orders VALUES (?, ?)",
                   ("three", json.dumps({"client_id": "three",
                                         "action": {"kind": "ADD", "side": "LONG", "units": 1000, "reason": ""},
                                         "status": "OPEN", "filled": 0, "reserved": "0",
                                         "initial_reserved": "0", "entry": True},
                                        sort_keys=True)))
    evidence = {"one": _response("77", "CANCELED", "0")}
    with pytest.raises(ValueError, match="uncovered outstanding intent"):
        apply_offline(store, "ev-uncovered", _bundle(), evidence, symbol="BTCUSDT",
                      expected_revision=store.load()[1])


def test_apply_offline_rejects_an_unbound_trade(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "NEW", "0")}
    with pytest.raises(ValueError, match="unbound account trade"):
        apply_offline(store, "ev-unbound", _bundle([_trade("t1", "1", "55")]), evidence,
                      symbol="BTCUSDT", expected_revision=store.load()[1])


def test_apply_offline_rejects_a_trade_direction_mismatch(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "NEW", "1")}
    with pytest.raises(ValueError, match="trade direction mismatch"):
        apply_offline(store, "ev-direction", _bundle([_trade("t1", "1", "77", side="SELL")]), evidence,
                      symbol="BTCUSDT", expected_revision=store.load()[1])


def test_apply_offline_rejects_an_off_step_quantity(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "PARTIALLY_FILLED", "0.0005")}
    with pytest.raises(ValueError, match="off-step trade quantity"):
        apply_offline(store, "ev-offstep", _bundle([_trade("t1", "0.0005", "77")]), evidence,
                      symbol="BTCUSDT", expected_revision=store.load()[1])


def test_apply_offline_rejects_a_conflicting_posted_trade(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "FILLED", "1")}
    result = apply_offline(store, "ev-fill", _bundle([_trade("t1", "1", "77")]), evidence,
                           symbol="BTCUSDT", expected_revision=store.load()[1])
    assert result["status"] == "OFFLINE_RECONCILED" and result["orders"]["one"] == "FILLED"
    with pytest.raises(ValueError, match="conflicting posted trade"):
        apply_offline(store, "ev-conflict", _bundle([_trade("t1", "1", "77", price="101")]), evidence,
                      symbol="BTCUSDT", expected_revision=store.load()[1])


def test_apply_offline_rejects_a_new_fill_after_terminal_state(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "FILLED", "1")}
    apply_offline(store, "ev-fill", _bundle([_trade("t1", "1", "77")]), evidence,
                  symbol="BTCUSDT", expected_revision=store.load()[1])
    with pytest.raises(ValueError, match="new fill contradicts terminal local state"):
        apply_offline(store, "ev-late", _bundle([_trade("t2", "1", "77")]), evidence,
                      symbol="BTCUSDT", expected_revision=store.load()[1])


def test_apply_offline_rejects_an_overfill(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "PARTIALLY_FILLED", "1.2")}
    with pytest.raises(ValueError, match="overfill"):
        apply_offline(store, "ev-overfill", _bundle([_trade("t1", "1.2", "77")]), evidence,
                      symbol="BTCUSDT", expected_revision=store.load()[1])


def test_apply_offline_rejects_a_posted_quantity_mismatch(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "PARTIALLY_FILLED", "1")}
    # identical receipt (fee in canonical decimal form) dedupes the second post,
    # so local filled stays below the exchange executed quantity
    bundle = _bundle([_trade("t1", "0.5", "77", time=123, fee="0.01"), _trade("t1", "0.5", "77", time=124, fee="0.01")])
    with pytest.raises(ValueError, match="posted quantity mismatch"):
        apply_offline(store, "ev-posted", bundle, evidence, symbol="BTCUSDT",
                      expected_revision=store.load()[1])


def test_apply_offline_preserves_an_open_order_status(tmp_path):
    store = _open_store(tmp_path)
    _prepare_open(store, "one")
    evidence = {"one": _response("77", "NEW", "0")}
    result = apply_offline(store, "ev-open", _bundle(), evidence, symbol="BTCUSDT",
                           expected_revision=store.load()[1])
    assert result["status"] == "OFFLINE_RECONCILED"
    assert result["orders"]["one"] == "OPEN"
