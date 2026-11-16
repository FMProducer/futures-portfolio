"""Gap coverage for rebalance/binance_readonly.py (no network, fake opener only)."""
import json
import sqlite3
import types

import pytest

from rebalance import binance_readonly as br
from rebalance.binance_readonly import (ReadError, Reader, capture,
                                        inspect_order, normalize, unresolved)

ACCOUNT = {"assets": [{"asset": "USDT", "walletBalance": "1000", "unrealizedProfit": "0",
                      "marginBalance": "1000", "availableBalance": "500",
                      "initialMargin": "0", "maintMargin": "0"}]}


def test_redirect_handler_is_blocked():
    with pytest.raises(ReadError, match="redirect_blocked"):
        br.NoRedirect().redirect_request(None, 0, 301, "move", {}, "https://new.example")


@pytest.mark.parametrize("key,secret", [("", "s"), ("k", "")])
def test_missing_credentials_are_rejected(key, secret):
    with pytest.raises(ReadError, match="missing_credentials"):
        Reader(key, secret)


def _reader_with_opener(open_fn):
    reader = Reader("key", "secret")
    reader._opener = types.SimpleNamespace(open=open_fn)
    return reader


def test_get_propagates_an_inner_read_error():
    def open_request(request, timeout=15):
        raise ReadError("injected_read_error")

    reader = _reader_with_opener(open_request)
    with pytest.raises(ReadError, match="injected_read_error"):
        reader.get("/fapi/v1/time")


def test_get_wraps_a_generic_transport_error():
    def open_request(request, timeout=15):
        raise OSError("injected offline transport")

    reader = _reader_with_opener(open_request)
    with pytest.raises(ReadError, match="network_or_response_error"):
        reader.get("/fapi/v1/time")


def test_get_rejects_an_endpoint_outside_the_allowlist():
    reader = _reader_with_opener(lambda *a, **k: None)
    with pytest.raises(ValueError, match="GET allowlist"):
        reader.get("/fapi/v1/order", symbol="BTCUSDT", qty="1")
    with pytest.raises(ValueError, match="GET allowlist"):
        reader.get("/fapi/v1/nope")


def test_synchronize_records_the_server_offset():
    reader = Reader("key", "secret")
    reader.get = lambda *args, **kwargs: {"serverTime": 1000}
    result = reader.synchronize()
    assert result["server_time"] == 1000 and result["roundtrip_ms"] >= 0
    assert reader.offset <= 1000


def test_normalize_rejects_a_non_boolean_mode_response():
    with pytest.raises(ReadError, match="invalid_mode_response"):
        normalize(ACCOUNT, [], {"dualSidePosition": "yes"}, {"multiAssetsMargin": False})
    with pytest.raises(ReadError, match="invalid_mode_response"):
        normalize(ACCOUNT, [], {"dualSidePosition": True}, {"multiAssetsMargin": 1})


def test_normalize_collects_only_active_positions():
    positions = [
        {"symbol": "BTCUSDT", "positionAmt": "2", "positionSide": "LONG", "entryPrice": "100",
         "markPrice": "101", "unRealizedProfit": "2", "marginType": "ISOLATED"},
        {"symbol": "ETHUSDT", "positionAmt": "0", "positionSide": "SHORT", "entryPrice": "1",
         "markPrice": "1", "unRealizedProfit": "0"},
    ]
    result = normalize(ACCOUNT, positions, {"dualSidePosition": True}, {"multiAssetsMargin": False})
    assert result["active_positions"] == [{
        "symbol": "BTCUSDT", "side": "LONG", "quantity": "2", "entry_price": "100",
        "mark_price": "101", "unrealized": "2", "margin_type": "ISOLATED"}]
    assert result["trading_authorized"] is False


def test_capture_rejects_an_invalid_symbol():
    with pytest.raises(ValueError, match="invalid symbol"):
        capture(object(), "btc")


def test_capture_reports_a_symbol_not_found():
    observer = {
        "/fapi/v1/positionSide/dual": {"dualSidePosition": True},
        "/fapi/v1/multiAssetsMargin": {"multiAssetsMargin": False},
        "/fapi/v3/account": ACCOUNT,
        "/fapi/v3/positionRisk": [],
        "/fapi/v1/openOrders": [],
        "/fapi/v1/exchangeInfo": {"symbols": []},
    }

    class FakeReader:
        def synchronize(self):
            return {"server_time": 1000, "roundtrip_ms": 1}

        def get(self, path, **params):
            return observer[path]

    with pytest.raises(ReadError, match="symbol_not_found"):
        capture(FakeReader(), "BTCUSDT")


def _fake_order(order_id="88", client_id="c1", symbol="BTCUSDT", position_side="LONG",
                side="SELL", status="NEW", executed="0", orig="1"):
    return {"orderId": order_id, "clientOrderId": client_id, "symbol": symbol,
            "positionSide": position_side, "side": side, "status": status,
            "executedQty": executed, "origQty": orig}


class OrderReader:
    def __init__(self, order):
        self.order = order

    def get(self, path, **params):
        assert path == "/fapi/v1/order"
        return self.order


def test_inspect_order_keeps_a_reduce_direction_and_reports_evidence_only():
    reader = OrderReader(_fake_order())
    intent = {"client_id": "c1", "action": {"kind": "REDUCE", "side": "LONG"}}
    result = inspect_order(reader, "BTCUSDT", intent)
    assert result["resolution"] == "EVIDENCE_ONLY_NO_STATE_CHANGE"
    assert result["status"] == "NEW" and result["executed_qty"] == "0"


def test_inspect_order_rejects_a_direction_mismatch():
    reader = OrderReader(_fake_order(side="BUY"))
    intent = {"client_id": "c1", "action": {"kind": "REDUCE", "side": "LONG"}}
    with pytest.raises(ReadError, match="order_direction_mismatch"):
        inspect_order(reader, "BTCUSDT", intent)


def test_inspect_order_reports_an_identity_mismatch():
    reader = OrderReader(_fake_order(client_id="other"))
    intent = {"client_id": "c1", "action": {"kind": "ADD", "side": "LONG"}}
    with pytest.raises(ReadError, match="order_identity_mismatch"):
        inspect_order(reader, "BTCUSDT", intent)


class FailingOrderReader:
    def get(self, path, **params):
        raise ReadError("http_error", status=403, code=-1121)


def test_inspect_order_keeps_unknown_on_a_read_error():
    intent = {"client_id": "c1", "action": {"kind": "ADD", "side": "LONG"}}
    result = inspect_order(FailingOrderReader(), "BTCUSDT", intent)
    assert result["resolution"] == "KEEP_UNKNOWN" and result["error"]["http_status"] == 403


def test_unresolved_lists_only_non_terminal_orders(tmp_path):
    db = tmp_path / "state.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE orders(client_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        conn.execute("INSERT INTO orders VALUES('a', ?)", (json.dumps({"client_id": "a", "status": "OPEN"}),))
        conn.execute("INSERT INTO orders VALUES('b', ?)", (json.dumps({"client_id": "b", "status": "FILLED"}),))
    assert [o["client_id"] for o in unresolved(db)] == ["a"]


def test_main_reports_state_db_order_observations(tmp_path, monkeypatch, capsys):
    db = tmp_path / "state.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE orders(client_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        conn.execute("INSERT INTO orders VALUES('a', ?)", (json.dumps({"client_id": "a", "status": "OPEN"}),))
    seen = []

    class FakeReader:
        def synchronize(self):
            return {"server_time": 1000, "roundtrip_ms": 1}

        def get(self, path, **params):
            return {}

    monkeypatch.setattr(br.Reader, "from_env", staticmethod(lambda path: FakeReader()))
    monkeypatch.setattr(br, "capture", lambda reader, symbol: {
        "rest_bracket_equal": True, "active_positions": [], "open_orders": 0, "blocking_reasons": []})
    monkeypatch.setattr(br, "inspect_order",
                        lambda reader, symbol, intent: seen.append(intent["client_id"])
                        or {"client_id": intent["client_id"], "resolution": "EVIDENCE_ONLY_NO_STATE_CHANGE"})
    output = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["binance_readonly", "--env-file", str(tmp_path / ".env"),
                                     "--symbol", "BTCUSDT", "--state-db", str(db),
                                     "--output", str(output)])
    br.main()
    assert seen == ["a"]
    payload = json.loads((output / "observation.json").read_text(encoding="utf-8"))
    assert payload["order_observations"][0]["client_id"] == "a"
    assert json.loads(capsys.readouterr().out)["status"] == "READ_ONLY_CAPTURED"
