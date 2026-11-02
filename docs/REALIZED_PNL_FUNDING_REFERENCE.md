# Realized PnL and funding accounting checkpoint

Base: `b6794f2c678ca328166ec66c244449081b89390d`.

Two proven errors are corrected:

- The dashboard funding contribution is `-funding_paid`, preserving credits as positive contributions instead of displaying every amount as an expense.
- During backtest cooldown, dormant capital follows actual TPV plus retained reserve before history sampling and restart. Funding receipts and debits, including events on the restart bar, no longer disappear during rebase.

Actual negative realized PnL is retained. Reconstructing weighted execution costs and every close found no PnL calculation errors above 1e-6 USDT. Portfolio trailing stops still close losing legs. No trading thresholds, funding model, session policy or PAPER/REAL processes were changed.

## Separate annual verification

Each case starts from the base commit, with identical frozen inputs: ZECUSDT, 525600 one-minute bars, 2025-10-20 00:00 through 2026-10-19 23:59 UTC.

| Case | Profit | MaxDD | Rebalances | Attribution residual, USDT |
|---|---:|---:|---:|---:|
| Dashboard funding sign only | +118.71276164859772% | 82.98999304979547% | 247 | -9.082080000006272 |
| Cooldown funding preservation only | +116.5045907717313% | 82.98579075112161% | 249 | -6.366462912410498e-12 |

The display-only case matches the complete base result data, including fills and equity. The accounting fix changes subsequent allocation sizes and reduces this window's profit by 2.20817087686642 percentage points. It corrects a demonstrated loss of recorded funds; it is not a profitability improvement. Both cases pass engine accounting and have funding halt disabled. All 189 closes of the corrected financial case were independently recalculated; its 55 negative closes are actual execution losses.

The prior residual equals the independently summed funding received within cooldown intervals: 9.08208 USDT. It is not hidden by an artificial residual-balancing adjustment.

## Tests and inputs

Before the fixes, regression cases reproduced four lost-credit/debit failures and one funding-display sign failure. After the fixes, 62 targeted tests passed, exit 0:

```powershell
python.exe -m pytest tests/test_realized_pnl_audit.py tests/test_backtest_rebalance.py tests/test_dashboard_attribution.py tests/test_equity_stop_capital_floor.py tests/test_selected_rebalance_changes.py -q --ignore-safety
```

Input SHA256 values:

- Config: `c939403b790e85a0813627d30d2ed5df3887ba35a007f575f4bf8aee03296ba5`.
- ZECUSDT_live.feather: `37ec72c903e426e5ad445a409b77cc48b2d7039398b973cf27a71451f9b026dd`.
- Fixed signed funding scenario: `f884d369aff13c173c4fe2390f2e8abaf10f5f7d6687b6a1bfe337c6c1c2d013`.

With the matching local data and unchanged reference config:

```powershell
python.exe backtest/backtest_rebalance.py --config config.json --ticker ZECUSDT --since "2025-10-20 00:00" --until "2026-10-19 23:59"
```

Verify hashes first: the engine chooses the largest matching Feather. The funding scenario is fixed signed historical amounts, not rates recalculated against changed positions.

Ignored local evidence is under `reports/realized_pnl_audit/`: REPORT_RU.md, manifest.json, comparison.json, original-close proofs, cooldown funding sums, test logs, and separate display_only/cooldown_only results and dashboards.

PAPER/REAL runtime and the full test suite were not verified. Existing dashboard share-sign normalization and other previously excluded changes remain outside this checkpoint. No push, main-checkout transfer or restart accompanies this commit.
