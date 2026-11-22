# Local financial parity check

Run from the repository root with the project's Python environment:

```powershell
python scripts/check_financial_parity.py BTWUSDT --data "C:\Python\Prosperous_Bot\futures_portfolio\data\BTWUSDT_year.feather"
```

Choose an inclusive UTC candle-open interval and an explicit single-portfolio policy:

```powershell
python scripts/check_financial_parity.py BTWUSDT --data "C:\Python\Prosperous_Bot\futures_portfolio\data\BTWUSDT_year.feather" --start "2026-11-01 00:00" --end "2026-11-10 23:59" --config config.json
```

Replace the symbol and its local `TICKER_*.feather` file for another USDT ticker. Omit both dates to use the entire file, including histories longer than one year. Dates without a timezone mean UTC. A date without a time means midnight; specify `23:59` to include an entire final day. A selected window starts a fresh isolated session, not a continuation of an existing PAPER position.

The source history and config are never overwritten. The selected period must have ordered, unique, contiguous one-minute OHLCV candles. Missing history, mismatching starting capital or unsupported external funding scenarios fail explicitly. Config policy is preserved: there is no implicit tuning or disabling of funding. For `signed_actual`, provide `--funding-rates path/to/rates.json`: an ordered JSON array with `symbol`, integer millisecond `fundingTime`, `fundingRate` and positive actual `markPrice`. The user must supply complete funding history for the requested interval; the CLI cannot certify provider completeness. No network download or exchange orders occur.

Each execution creates a fresh `reports/parity_SYMBOL_TIMESTAMP/` directory (or a new path supplied through `--output`). It contains the frozen data/config, input SHA-256 hashes, full three-mode curves/fills/results, `comparison.json`, `summary.json`, and the printed table in `SUMMARY.md`. CLI exit status is recorded in `summary.json` for completed runs; uncaught execution errors are written to `cli_error.json` when the directory has been prepared. Redirect stdout/stderr if a combined log is required:

```powershell
python scripts/check_financial_parity.py BTWUSDT --data "C:\Python\Prosperous_Bot\futures_portfolio\data\BTWUSDT_year.feather" *> parity.log
$LASTEXITCODE
```

Exit codes: **0** all checks pass; **1** financial parity, execution, input, or persistence-prefix verification fails; **2** financial parity passes but a strict CASH/VIRTUAL invariant fails. Exit 2 is not overall PASS. Net includes commissions; fees are shown separately. Ending bot TPV excludes the separately configured reserve. Negative bot TPV and bot MaxDD above 100% must not be confused with account drawdown.

The CLI reuses the real BACKTEST and runtime PAPER/SIMULATED REAL state machines sequentially, with isolated I/O and an opt-in virtual polling clock. It exercises premature +1000 ms and valid +6000 ms polls with the production 5000 ms finalization margin enabled. Financial comparisons retain the existing 1e-8 USDT tolerance, while the CASH/VIRTUAL sign check remains strict. The native-persistence check covers only a prefix of up to 180 candles; the full run uses isolated adapters. Local finalized candles cannot prove exchange candle finality, live order-book execution, or production deployment readiness.

The full BTW verification on 2026-11-22 established financial parity but retained overall FAIL due to an unresolved precision/provenance issue in free-CASH reconstruction. See `docs/BTW_PARITY_20261122.md` for the protocol and evidence paths. No negative balance is rounded or clamped to make the check pass.
