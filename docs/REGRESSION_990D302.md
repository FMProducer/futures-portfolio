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
