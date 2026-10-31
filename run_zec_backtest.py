import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest.backtest_rebalance import run_backtest

async def main():
    result = await run_backtest(
        config_path="config.json",
        data_dir="data",
        ticker_override="ZECUSDT",
        since_utc="2025-10-20 00:00",
        until_utc="2026-10-19 23:59",
        quiet=False
    )
    if result:
        print(f"Status: {result['status']}")
        print(f"Liquidations: {result['liquidations']}")
        print(f"Trailing Stops: {result['trailing_stops']}")
        print(f"Max DD: {result['max_dd_pct']:.2f}%")
        print(f"Profit: {result['profit_pct']:.2f}%")
        print(f"Cycles: {result['cycles']}")

        # Print events related to liquidation
        for event in result.get('events', []):
            if 'liquidation' in event.get('kind', '').lower():
                print(f"Event: bar={event['bar']} kind={event['kind']} detail={event.get('detail', '')}")
            if 'cooldown' in event.get('kind', '').lower():
                print(f"Event: bar={event['bar']} kind={event['kind']} detail={event.get('detail', '')}")

if __name__ == "__main__":
    asyncio.run(main())