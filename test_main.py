import asyncio
import sys
import traceback

# Set up logging
import logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s")

try:
    from rank_tickers import main
    result = asyncio.run(main(quiet=False, min_volume=20_000_000))
    print(f"\nTotal results: {len(result) if result else 0}")
except Exception as e:
    traceback.print_exc()
