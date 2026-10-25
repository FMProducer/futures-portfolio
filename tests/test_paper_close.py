import json
import logging
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from futures_portfolio.core.paper_close import close_wallet


def test_close_receipts_reconcile_and_retry_does_not_charge_twice():
    wallet = {"balance": 1000, "positions": {"X_LONG": 2, "X_SHORT": 1},
              "long_entry_price": 100, "short_entry_price": 110}
    before = deepcopy(wallet)
    closed = close_wallet(wallet, "X", 105, "stop1", "0.0004")
    assert closed["balance"] == 1014.874
    assert wallet == before
    assert len(closed["share_fill_receipts"]) == 2
    assert close_wallet(closed, "X", 105, "stop1", "0.0004") == closed


def test_close_invalid_second_leg_is_atomic():
    wallet = {"balance": 1000, "positions": {"X_LONG": 2, "X_SHORT": 1},
              "long_entry_price": 100, "short_entry_price": 0}
    before = deepcopy(wallet)
    with pytest.raises(ValueError):
        close_wallet(wallet, "X", 105, "stop1", "0.0004")
    assert wallet == before


@pytest.mark.asyncio
async def test_emergency_paper_stop_preserves_opening_and_ath(tmp_path):
    from futures_portfolio.core.main import emergency_stop
    (tmp_path / "core").mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"portfolios": [{"initial_capital": 1000}]}))
    sp, wp = tmp_path / "core/state.json", tmp_path / "core/wallet.json"
    sp.write_text(json.dumps({"base_ticker": "X", "started_at": 123, "initial_tpv": 1000,
                              "tpv_ath": 1200, "rebalance_cycles": 4}))
    wp.write_text(json.dumps({"balance": 1000, "positions": {"X_LONG": 2, "X_SHORT": 1},
                              "long_entry_price": 100, "short_entry_price": 110}))
    connector = AsyncMock()
    connector.get_mark_prices.return_value = {"X": 105}
    await emergency_stop(connector, str(config), str(sp), str(wp), logging.getLogger(),
                         ticker_override="X", paper_mode=True, close_only=True)
    state, wallet = json.loads(sp.read_text()), json.loads(wp.read_text())
    assert state["initial_tpv"] == 1000 and state["tpv_ath"] == 1200
    assert state["last_tpv"] == wallet["balance"] == 1014.874
    assert state["paper_close_complete"] is True
    assert len(wallet["share_fill_receipts"]) == 2
    await emergency_stop(connector, str(config), str(sp), str(wp), logging.getLogger(),
                         ticker_override="X", paper_mode=True, close_only=True)
    assert json.loads(wp.read_text())["balance"] == 1014.874
