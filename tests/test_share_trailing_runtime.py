import copy
import json
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from core import share_trail
from tests.test_main_new import mock_config, mock_connector, mock_notifier
from futures_portfolio.core import main


class EndTick(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_identity", [False, True])
async def test_runtime_duplicate_fill_and_disk_checkpoint(mock_config, mock_connector, mock_notifier, missing_identity):
    cfg = mock_config
    cfg.update(rebalance_share_trail_distance=.01, equity_trailing_stop_activation_pct=9000,
               max_drawdown_limit=200)
    portfolio = cfg["portfolios"][0]
    portfolio.update(initial_capital=1000., paper_initial_capital=1000., max_capital_usdt=1000.,
                     siphoning_threshold_pct=1000., reinvestment_ratio=0., fuse_enabled=False)
    mock_connector.get_mark_prices.return_value = {"BTCUSDT": 100.}
    mock_connector.get_futures_prices.return_value = {"BTCUSDT": 100.}
    mock_connector.get_order_book.return_value = {"bids": [["100", "10"]], "asks": [["100", "10"]]}
    trail = share_trail.fresh_state()
    trail["BASE_LONG"]["trough"] = "0.02"
    files = {
        "state.json": {"base_ticker": "BTCUSDT", "initial_tpv": 1000., "tpv_ath": 1000.,
                       "virt_debt": 200., "share_trail": trail, "rebalance_cycles": 1},
        "paper_state.json": {"base_ticker": "BTCUSDT", "balance": 1000.,
                             "profit_budget": {"balance": "100", "receipts": {}, "blocked": False},
                             "positions": {"BTCUSDT_LONG": 1., "BTCUSDT_SHORT": 1.},
                             "long_entry_price": 100., "short_entry_price": 100., "last_price": 100.}}
    fill = dict(status="SUCCESS", type="LONG", symbol="BTCUSDT_LONG", side="BUY", qty=.1,
                price=100., commission=.004, trade_pnl=0., reduce_only=False, execution_id="paper:test")
    if missing_identity:
        fill.pop("execution_id")
    executor = MagicMock()
    executor.execute_actions = AsyncMock(return_value=[fill, dict(fill)])
    executor.sync_positions = AsyncMock()

    async def load(path, default=None):
        return copy.deepcopy(files.get(str(path), default))

    async def save(path, value):
        files[str(path)] = json.loads(json.dumps(value))

    async def sleep(seconds):
        raise EndTick()

    with patch.object(main, "load_json", side_effect=load), \
         patch.object(main, "save_json", side_effect=save), \
         patch.object(main, "safe_load_json_sync", return_value=cfg), \
         patch.object(main, "PortfolioExecutor", return_value=executor), \
         patch.object(main, "TelegramNotifier", return_value=mock_notifier), \
         patch("builtins.open", mock_open(read_data=json.dumps(cfg))), \
         patch("os.path.getmtime", return_value=123), \
         patch("asyncio.sleep", side_effect=sleep):
        with pytest.raises(EndTick):
            await main.rebalance_loop(mock_connector, "config.json", "state.json", "paper_state.json", MagicMock())
        if not missing_identity:
            # Simulate a crash between the economic-book and strategy-state saves.
            files["state.json"].pop("share_trail_last_fill", None)
            files["state.json"]["share_trail"] = copy.deepcopy(trail)
            with pytest.raises(EndTick):
                await main.rebalance_loop(mock_connector, "config.json", "state.json", "paper_state.json", MagicMock())
    assert executor.execute_actions.await_count == (1 if missing_identity else 2)
    book = files["paper_state.json"]
    if missing_identity:
        assert files["state.json"]["startup_blocked"]
        assert files["state.json"]["share_trail_unreconciled_results"]
        assert book["positions"]["BTCUSDT_LONG"] == 1.
        return
    assert book["positions"]["BTCUSDT_LONG"] == 1.1
    assert book["balance"] == pytest.approx(999.996)
    assert len(book["share_fill_receipts"]) == 1
    assert "wait_add" not in files["state.json"]["share_trail"]["BASE_LONG"]
    assert book["share_trail_checkpoint"]["trail"]["BASE_LONG"]["trough"] is None
