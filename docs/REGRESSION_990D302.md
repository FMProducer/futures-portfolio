# Regression audit of 990d302 (2026-11-05)

Candidate: codex/regression-990d302, isolated from the running main checkout. The initial audit performed no commit, push, deployment or swarm restart. The user subsequently authorized committing this verified checkpoint; no push or deployment is included. The running swarm has not loaded these changes.

## Inputs

The user's current config was frozen in reports/regression_990d302/working_config_snapshot.json. Config SHA256: 60ceb775a9656fae8734fb7f8aaed59343a5c6668e0f850a56561763dc4c0c56. The tested config is byte-identical to that copy. Targets25/25/50; trading/PAPER capital1000; external reserves5000; legacy_constant funding; share trail0.05800887342949769; MaxDD100 (disabled); max_bots8/paper_mode_bots8, i.e. PAPER-only capacity. Unit tests explicitly supply inputs for their named edge cases; these are not parameter optimization runs.

## Candidate changes

- Replaced stale source-line ranges in the guard oracle with structural AST selection; the backtest guards remain the oracle and assertions remain enabled.
- Updated the cooldown AST fixture for exit/drawdown; isolated REAL tests explicitly request their single REAL slot instead of inheriting PAPER-only capacity.
- Reproduced a supervisor defect: confirmed strategy exit could delete its flag before saving the cooldown, then lose the deadline if an empty scanner returned early. Save the confirmed deadline before flag deletion. Red:2failed/6passed. Green:8passed for PAPER/REAL and unverified-close rejection.
- Per explicit user choice, MaxDD below100 now uses BACKTEST's full portfolio equity including SAFE and a fixed configured initial-capital loss budget. PAPER/REAL check it before rebalance submission, persist the peak, close through the existing confirmed-close path and preserve the strategy-minute cooldown. Limits>=100 remain disabled. Supervisor accepts both legacy max_dd_stop and current max_drawdown reasons.22 targeted tests passed, including equality, nonfinite input, persisted peaks, SAFE and stop-before-order.
- Found incomplete subprocess mocks in legacy supervisor tests. Earlier phases spawned the isolated CLI; credential identity validation rejected it before the trading loop. The final fixture refuses unmocked launches of this checkout's trading worker before process creation.3 isolation tests passed; temporary fake-worker probes remain allowed. The final isolation/lifecycle selection passed66tests.

## Annual controls

Local525600-minute histories,2025-10-20 00:00 through2026-10-19 23:59UTC. Both runs exited0 with the exact config. No historical funding rates substituted for legacy_constant.

| Ticker | Net | MaxDD | Cycles | Stops | Liquidations |
|---|---:|---:|---:|---:|---:|
| AGTUSDT |+379.927489%|67.421727%|386|82|0|
| ZECUSDT |+89.177330%|80.968951%|236|46|0|

All525600 equity points match their previous saved controls within1e-8USDT. Exact differences and data hashes are in report.json. These are BACKTEST results, not annual REAL acceptance. The supplied100% setting disables the stop; enabled MaxDD behavior is covered by named unit cases.

## Full-suite verdict: FAIL

Command: `.venv\Scripts\python.exe -B -m pytest tests -q --tb=short --junitxml=reports/regression_990d302/isolated-final-junit.xml`, using the main environment Python from this isolated checkout. Console:1060passed,79failed,13errors,10warnings in100.98s; exit1. Full evidence:isolated-final-tests.log, isolated-final-junit.xml and report.json. Raw JUnit suite attributes are retained separately from the console counters. No xfail or skip was added to hide failures.

Confirmed remaining examples:

-10 setup errors: missing data/ZECUSDT_12m.feather. The supplied current history has a different filename; the missing recorded replay artifact was not silently fabricated or substituted.
-3 setup errors: versioned tests reference an absent experiments/trailing_share/report_tables.py. An untracked main-checkout copy is not part of990d302.
-One operational assertion requires one REAL slot despite the user's supplied8/8 PAPER-only setting. The config was preserved.
-Old fee expectations omit the current PAPER slippage; other legacy supervisor tests expect terminal evidence to be erased or fail to mock child launches.
-Several REAL fixtures lack the current funding/allocation proof. Financial, recovery and lifecycle failures not yet classified individually remain open; the remaining79 failures must not all be labelled test defects.

This is not production certification. All candidate changes and generated test WAL artifacts remain in this worktree; nothing was copied into live state.


## Follow-up after checkpoint 94f7a02: full regression PASS

The user authorized committing the audited checkpoint and continuing fixes in this branch with the same annual AGTUSDT replay after each group. Commit94f7a02 contains the prior equity/cooldown/isolation checkpoint. Subsequent changes below remain uncommitted and undeployed.

### Product correction

`reset_bot_state_files` previously rebuilt a missing or incomplete wallet from configured capital when an old strategy state existed. Eight PAPER/REAL scenarios reproduced this creation of unsupported capital. It now rejects the reset before archiving or replacing either financial file unless the old wallet contains balance and positions. These tests verify both original files remain unchanged on refusal. Actual caller/reset tests validate committed two-file WAL state and byte-preserved archives rather than counts of obsolete os.remove calls.

### Test and packaging corrections

- Commission/PnL expectations now use the independently specified adverse0.05% PAPER fill price and0.04% fee. Wallet preservation and reduction bounds remain checked. Runtime storage expectations include the once-per-bar funding debit and full precision.
- Guard, initial-opening and trailing tests receive an explicit external reserve where their subject is not funding exhaustion. The dedicated liquidation test keeps its zero-reserve fixture. The supplied8/8 config is correctly PAPER-only; its REAL allocation attempt must fail before an account read.
- Isolated metrics no longer read another checkout's SQLite budget. Supervisor tests use real temporary state/archives, confirmed-close evidence, mocked PM2 results and explicit session deadlines. They test failure propagation and preservation of terminal proof rather than erasing it.
- The bound Client names in both connector import namespaces are mocked, in addition to binance.client.Client. Two new tests verify the effective client is mocked; CLI worker launch guards remain active.
- The replay-corruption unit fixture now generates a flat200-USDT,10-day synthetic history and a100-USDT reserve so its prescribed4500-USDT synthetic payment exercises shortfall. All ten verdict-corruption cases remain enforced. This is synthetic unit evidence, not historical ZEC/AGT performance.
- Restored the existing report_tables.py from the main checkout, recorded its SHA256 in report_module_provenance.json, and whitelisted only that dependency in .gitignore. Report unit tests supply a two-bar DataFrame instead of requiring local historical files at import. No recorded historical artifact or baseline was silently substituted.
- Market partial-fill and real-process crash tests now use valid funding snapshots, normalized mocked exchange positions, complete trade facts (quantity and USDT fee asset), and real temporary files. They still kill a process after send/result/book, verify exact receipts/fees/quantities, and refuse duplicate submissions on restart.
- Trend guard tests drive closed-minute data and capture the actual filter output before the one-leg selector, while checking that execution stays within one leg. CASH tests no longer expect an addition funded by a same-tick projected sale. Range tests derive bounds from supplied values; no real-data parameter optimization was launched.

### Per-group acceptance

| Group | Targeted PASS | AGT Net | MaxDD | Cycles | Equity comparison |
|---|---:|---:|---:|---:|---|
|1:costs/reserves/capacity|84|+379.927489%|67.421727%|386|all525600 equal|
|2:metrics/reset archives/start mocks|11|+379.927489%|67.421727%|386|all525600 equal|
|3:self-contained replay/report dependencies|13|+379.927489%|67.421727%|386|all525600 equal|
|4:supervisor/reset financial refusal|146|+379.927489%|67.421727%|386|all525600 equal|
|5:REAL funding/allocation/margin replay|131|+379.927489%|67.421727%|386|all525600 equal|
|6:partial fill/process crash recovery|28|+379.927489%|67.421727%|386|all525600 equal|
|7:resume/CASH/trend/exact storage|51|+379.927489%|67.421727%|386|all525600 equal|

Every annual replay used the same frozen config SHA25660ceb775a9656fae8734fb7f8aaed59343a5c6668e0f850a56561763dc4c0c56 and local AGTUSDT_live.feather SHA256c6cf752e9d204a19c1e85ba6b75c5b60d817bf81b7e0cfc8eebc52e75fbc5308,2025-10-20 00:00 through2026-10-19 23:59UTC. Funding remains legacy_constant; actual historical rates are not claimed. All seven annual runs exited0.

Final complete command: `.venv\Scripts\python.exe -B -m pytest tests -q --tb=short --junitxml=reports/regression_990d302/after-fixes-junit.xml`. **1159passed,0failed,0errors,0skipped,9Optuna warnings in115.89s; exit0**. Log:after-fixes-tests.log. Manifest:followup_report.json. After the run, only extra blank EOF lines in two test modules were removed; their ASTs were explicitly checked identical and before/after hashes recorded. No behavioral changes followed acceptance.

The previous FAIL section is preserved as historical evidence and is superseded by this complete PASS. Generated funding_intent files are test artifacts and are excluded from delivery. No changes were copied into the running main checkout, no PM2 workers restarted, and no REAL exchange acceptance was performed. Offline PASS is not proof of exchange/runtime deployment.
