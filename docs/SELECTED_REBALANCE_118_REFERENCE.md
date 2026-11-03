# Selected four-change checkpoint

Base: `230cf73f532a6e6becbcccc63261ee1937779070`.

Selected changes:

- Defer both futures legs of a flat initial build when the directional guard blocks one leg.
- Preserve negative TPV instead of replacing it with epsilon.
- Calculate CASH from wallet funds less occupied margin.
- Label an approved amount below the original backtest request as partial execution.

The previously tested equity trailing-stop floor remains: no new stop below configured initial capital; reset its pending timer below the floor. BACKTEST uses initial_capital, PAPER uses paper_initial_capital, REAL uses initial_capital. Runtime checks active TPV excluding Siphon.

The legacy profit-budget restriction, reduction-sizing formula, funding scenario, allocation order and dashboard are retained. CASH-only and NAV-based reduction sizing from the rejected package are excluded. No lifecycle, configuration or risk-threshold changes are included.

## Actual working-source verification

ZECUSDT, 525600 one-minute bars, 2025-10-20 00:00 through 2026-10-19 23:59 UTC:

| Metric | Base | Selected changes together |
|---|---:|---:|
| Profit | +118.45322563252351% | +118.71276164859772% |
| MaxDD | 83.00922438600949% | 82.98999304979547% |
| Rebalances | 259 | 247 |
| Trailing stops | 55 | 53 |

Accounting passed; funding halt is false. All 54 initial opening bars contain both futures legs. The complete fills and equity curve match the separately tested initial-pair variant. The other selected changes do not change this window's trading trajectory; execution-status metadata changes.

109 targeted tests passed, exit 0:

```powershell
python.exe -m pytest tests/test_equity_stop_capital_floor.py tests/test_selected_rebalance_changes.py tests/test_calculator.py tests/test_profit_budget.py tests/test_paper_cash_budget.py tests/test_executor.py tests/test_backtest_rebalance.py -q --ignore-safety
```

The tests run offline with mocked execution; PAPER/REAL runtime acceptance was not performed. This is not a full-suite certification.

## Frozen inputs and reproduction

- Configuration SHA256: `c939403b790e85a0813627d30d2ed5df3887ba35a007f575f4bf8aee03296ba5`.
- ZECUSDT_live.feather SHA256: `37ec72c903e426e5ad445a409b77cc48b2d7039398b973cf27a71451f9b026dd`.
- Funding scenario SHA256: `f884d369aff13c173c4fe2390f2e8abaf10f5f7d6687b6a1bfe337c6c1c2d013`.

The reference configuration uses initial capital 1000, siphoning_threshold_pct=0 and the existing signed funding scenario. Verify input hashes before comparing:

```powershell
python.exe backtest/backtest_rebalance.py --config config.json --ticker ZECUSDT --since "2025-10-20 00:00" --until "2026-10-19 23:59"
```

The engine selects the largest matching local Feather. The funding input is a fixed signed historical scenario, not rates recalculated against changed positions.

Local, ignored evidence: reports/selected_four_changes/result.json, summary.json, scope_verification.json, verification.json, tests.log and run.log. The rejected package and prior diff are backed up in reports/selected_four_changes/before; neither is part of this checkpoint.

## Remaining limits

MaxDD remains approximately 83%. The exact-zero TPV fallback, legacy spending restriction, reduction-sizing behavior and dashboard sign-normalization issue remain. A positive annual result does not establish suitability for REAL. No main-checkout transfer, push or process restart accompanies this commit.
