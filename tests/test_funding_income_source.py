"""Read-only funding source trust boundaries and completeness."""
from unittest.mock import Mock, call
import pytest
from futures_portfolio.core.connector import BinanceConnector


def row(identity=1, timestamp=10, amount="-0.000049"):
    return {"symbol": "ZECUSDT", "incomeType": "FUNDING_FEE", "asset": "USDT",
            "time": timestamp, "tranId": identity, "income": amount}


def connector(responses):
    c = BinanceConnector("key", "secret", testnet=False)
    c.futures_client = Mock()
    c.futures_client.futures_income_history.side_effect = responses
    return c


@pytest.mark.asyncio
async def test_empty_read_only():
    c = connector([[]])
    assert await c.get_funding_income("ZECUSDT", 0, 20) == []
    assert c.futures_client.mock_calls == [call.futures_income_history(
        symbol="ZECUSDT", incomeType="FUNDING_FEE", startTime=0, endTime=20, limit=1000)]


@pytest.mark.asyncio
async def test_duplicate_sort_sign_fraction():
    a, b = row(2, 11, "3.00000000"), row()
    assert await connector([[a, b, b.copy()]]).get_funding_income("ZECUSDT", 0, 20) == [b, a]


@pytest.mark.asyncio
async def test_full_page_split():
    left = [row(i, 5) for i in range(1, 501)]
    right = [row(i, 15) for i in range(501, 1001)]
    extra = row(1001, 16)
    c = connector([left + right, left, right + [extra]])
    actual = await c.get_funding_income("ZECUSDT", 0, 20)
    assert len(actual) == 1001 and actual[-1] == extra
    assert c.futures_client.futures_income_history.call_args_list[1].kwargs['endTime'] == 10
    assert c.futures_client.futures_income_history.call_args_list[2].kwargs['startTime'] == 11


@pytest.mark.asyncio
async def test_single_timestamp_saturation():
    c = connector([[row(i) for i in range(1, 1001)]])
    with pytest.raises(ValueError, match="truncated"):
        await c.get_funding_income("ZECUSDT", 10, 10)


@pytest.mark.asyncio
async def test_conflicting_identity():
    with pytest.raises(ValueError, match="Conflicting"):
        await connector([[row(), row(amount="3")]]).get_funding_income("ZECUSDT", 0, 20)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"income": "NaN"}, {"income": "Infinity"},
    {"asset": "BNB"}, {"symbol": "BTCUSDT"}, {"incomeType": "COMMISSION"},
    {"time": 21}, {"time": 10.5}, {"tranId": None}, {"tranId": True}])
async def test_invalid_fact(change):
    with pytest.raises((ValueError, KeyError, TypeError)):
        await connector([[dict(row(), **change)]]).get_funding_income("ZECUSDT", 0, 20)


@pytest.mark.asyncio
async def test_api_failure():
    with pytest.raises(RuntimeError):
        await connector([RuntimeError("unavailable")]).get_funding_income("ZECUSDT", 0, 20)
