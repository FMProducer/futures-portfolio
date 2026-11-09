"""Unit tests for quant audit metrics and binance margin ratio additions."""
import math
import pytest
from unittest.mock import MagicMock
import numpy as np

from core.connector import BinanceConnector
from backtest.backtest_rebalance import run_backtest


@pytest.mark.asyncio
async def test_binance_margin_ratio_and_coverage_ratio_calculation():
    """Verify margin_ratio (coverage) and binance_margin_ratio (maint/balance) formulas."""
    connector = BinanceConnector(api_key="mock", secret_key="mock")
    connector.futures_client = MagicMock()

    # Case 1: Standard healthy account
    # TotalMarginBalance = 5000, TotalMaintMargin = 500
    # coverage ratio = 5000 / 500 = 10.0
    # binance margin ratio = 500 / 5000 = 0.1 (10%)
    connector.futures_client.futures_account = MagicMock(return_value={
        "totalMarginBalance": "5000.0",
        "totalMaintMargin": "500.0",
        "availableBalance": "4500.0",
        "totalWalletBalance": "5000.0",
        "liquidationPrice": "0"
    })

    res = await connector.get_margin_ratio()
    assert math.isclose(res["margin_ratio"], 10.0, rel_tol=1e-6)
    assert math.isclose(res["binance_margin_ratio"], 0.1, rel_tol=1e-6)
    assert res["total_margin_balance"] == 5000.0
    assert res["total_maint_margin"] == 500.0

    # Case 2: Zero maintenance margin (no open positions)
    # coverage ratio = inf
    # binance margin ratio = 0.0
    connector.futures_client.futures_account = MagicMock(return_value={
        "totalMarginBalance": "1000.0",
        "totalMaintMargin": "0.0",
        "availableBalance": "1000.0",
        "totalWalletBalance": "1000.0",
    })
    res_zero_maint = await connector.get_margin_ratio()
    assert math.isinf(res_zero_maint["margin_ratio"])
    assert res_zero_maint["binance_margin_ratio"] == 0.0

    # Case 3: Zero margin balance (blown account)
    # coverage ratio = 0.0
    # binance margin ratio = inf
    connector.futures_client.futures_account = MagicMock(return_value={
        "totalMarginBalance": "0.0",
        "totalMaintMargin": "200.0",
        "availableBalance": "0.0",
        "totalWalletBalance": "0.0",
    })
    res_blown = await connector.get_margin_ratio()
    assert res_blown["margin_ratio"] == 0.0
    assert math.isinf(res_blown["binance_margin_ratio"])


@pytest.mark.asyncio
async def test_backtest_sharpe_and_sortino_annualization():
    """Verify that run_backtest exposes annualized Sharpe and Sortino ratios."""
    # Run backtest on sample short window
    res = await run_backtest(
        config_path="config.json",
        data_dir="data",
        ticker_override="ZECUSDT",
        since_utc="2025-10-20 00:00",
        until_utc="2025-10-20 06:00",
        quiet=True
    )
    assert res is not None
    assert "sharpe_ratio" in res
    assert "sortino_ratio" in res
    assert "sortino_annualized" in res

    # Check scaling relationship: sortino_annualized == sortino_ratio * sqrt(525600)
    ann_factor = math.sqrt(1440.0 * 365.0)
    assert math.isclose(res["sortino_annualized"], res["sortino_ratio"] * ann_factor, rel_tol=1e-5)
    assert math.isfinite(res["sharpe_ratio"])
    assert math.isfinite(res["sortino_annualized"])
