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
        print(f"\n=== RESULT SUMMARY ===")
        print(f"Status: {result['status']}")
        print(f"Liquidations: {result['liquidations']}")
        print(f"Trailing Stops: {result['trailing_stops']}")
        print(f"Max DD: {result['max_dd_pct']:.2f}%")
        print(f"Profit: {result['profit_pct']:.2f}%")
        print(f"Cycles: {result['cycles']}")
        print(f"Predictive guard blocks: {result.get('trend_guard_blocks', 0)}")
        print(f"Velocity guard blocks: {result.get('velocity_guard_blocks', 0)}")
        print(f"Funding halt: {result.get('funding_halt_active', False)}")

        print(f"\n=== LIQUIDATION/STOP EVENTS ===")
        for event in result.get('events', []):
            k = event.get('kind', '')
            if any(x in k.lower() for x in ['liq', 'stop', 'cooldown', 'funding', 'default', 'guard']):
                print(f"  bar={event['bar']:5d}  {k:30s}  tpv={event.get('tpv', 0):.2f}  {event.get('detail', '')[:100]}")

        # Check fills for liquidation triggers
        print(f"\n=== LIQUIDATION FILLS ===")
        for fill in result.get('fills', []):
            if fill.get('trigger') == 'liquidation':
                print(f"  bar={fill['bar']:5d}  {fill['side']:4s}  {fill['leg']:10s}  qty={fill['qty']:.4f}  price={fill['price']:.4f}  tpv={fill['tpv']:.2f}")

asyncio.run(main())