import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backtest.backtest_rebalance import run_backtest

async def main():
    files = (ROOT / "data").glob("*.feather")
    tickers = sorted({
        match.group(1).upper()
        for path in files
        if (match := re.match(r"^([A-Za-z0-9]+USDT)(?:_|$)", path.stem, re.I))
    })

    results = []
    failed = []

    for ticker in tickers:
        print(f"\n========== {ticker} ==========", flush=True)
        try:
            result = await run_backtest(
                config_path=str(ROOT / "config.json"),
                data_dir=str(ROOT / "data"),
                ticker_override=ticker,
                live_mode=False,
                days=2,
                quiet=False,
            )
            if result is None:
                failed.append(ticker)
            else:
                results.append(result)
        except Exception as error:
            failed.append(ticker)
            print(f"ERROR {ticker}: {error}", flush=True)

    if not results:
        print(f"\nНет успешных результатов. Ошибки: {', '.join(failed) or 'нет файлов'}")
        return

    n = len(results)
    avg_profit = sum(r["profit_pct"] for r in results) / n
    avg_dd = sum(r["max_dd_pct"] for r in results) / n
    positive = sum(r["profit_pct"] > 0 for r in results)
    total = lambda key: sum(r.get(key, 0) for r in results)

    print("\n========== ОБЩИЙ РЕЗУЛЬТАТ ==========")
    print(f"Тикеров: {n} из {len(tickers)} | В плюсе: {positive} | В минусе: {sum(r['profit_pct'] < 0 for r in results)}")
    print(f"Средний Profit: {avg_profit:+.2f}% | Средний MaxDD: {avg_dd:.2f}% | Циклы: {total('cycles')}")
    print(f"SAFE Reserve: {total('siphoning_reserve'):.2f} USDT | Комиссии: {total('commission_paid'):.2f} USDT")
    print(f"Skipped Expansions: {total('skipped_expansions')} | Stops: {total('trailing_stops')} | Liqs: {total('liquidations')}")
    print(f"Trend Guard: {total('trend_guard_blocks')} | Velocity Guard: {total('velocity_guard_blocks')} | Trend-add: {total('trend_add_blocks')}")
    print(f"Исполнений: {total('exchange_fill_count')} | Share trailing fills: {total('share_trail_consumes')}")
    print(f"Ошибок: {len(failed)}" + (f" — {', '.join(failed)}" if failed else ""))

asyncio.run(main())
