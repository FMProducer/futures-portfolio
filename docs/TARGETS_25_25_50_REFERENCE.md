# Targets 25/25/50: verified accounting checkpoint

Parent trading code: `7a490ba6cef87b4f5cf1ad096b2720e812edcca2`.

This checkpoint sets the tested config targets to LONG=0.25, SHORT=0.25, VIRTUAL=0.50. All other configuration fields and trading code are unchanged. Both futures legs retain leverage 3; initial capital is 1000 USDT, external margin backing is 5000 USDT, siphoning is disabled.

## Annual results

ZECUSDT, 525600 one-minute bars, 2025-10-20 00:00 through 2026-10-19 23:59 UTC. All three runs used the same config and frozen data/funding.

| Code | Profit | MaxDD | Rebalances | Trailing stops | Attribution residual, USDT |
|---|---:|---:|---:|---:|---:|
| Original 230cf73 | +104.97957050358382% | 31.500081146473708% | 182 | 42 | -4.068640000001778 |
| Selected changes b6794f2 | +102.35487224832846% | 33.67031106608756% | 172 | 42 | -2.2894600000022365 |
| Corrected accounting 7a490ba | +102.6826277897503% | 33.71038797339733% | 172 | 42 | -1.8189894035458565e-12 |

The original run matched all 296 fill rows of the supplied HTML. The corrected case has final equity 2026.82627789953 USDT, 297 fills, accounting_ok=true and funding_halt_active=false. Funding preservation adds 0.32775554142184 percentage points of profit relative to b6794f2 on these targets; the earlier drop from the original result predates that fix.

## Reproduction

SHA256:

- config.json: `8de2ac89e45741ab0a46149bd00547b80255d50dda948ff1a4b6b00655fcdea8`.
- ZECUSDT_live.feather: `37ec72c903e426e5ad445a409b77cc48b2d7039398b973cf27a71451f9b026dd`.
- data/funding_ZECUSDT.json: `f884d369aff13c173c4fe2390f2e8abaf10f5f7d6687b6a1bfe337c6c1c2d013`.

The committed config matches the tested snapshot exactly before Git newline normalization. On another checkout, compare parsed JSON and normalize line endings when verifying the config hash.

With matching local Feather in data/:

```powershell
python.exe backtest/backtest_rebalance.py --config config.json --ticker ZECUSDT --since "2025-10-20 00:00" --until "2026-10-19 23:59"
```

The engine selects the largest matching local Feather; verify its hash. Local ignored evidence: reports/targets_25_25_50/manifest.json, comparison.json, verification.json, REPORT_RU.md and corrected/result.json. The corrected dashboard is corrected/zec_12m_dashboard.html in that report directory.

## Limits

These figures concern the 1000-USDT portfolio with 5000-USDT external margin backing. The fixed signed funding scenario remains unchanged and is not rates recalculated against changed positions. PAPER/REAL runtime and other periods were not tested in this step. A cosmetic double minus in the funding KPI remains outside this checkpoint; signed funding in the attribution table and the corrected monetary calculation are separately verified.

No main-checkout transfer, push or process restart is part of this commit.
