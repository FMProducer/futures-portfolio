# Configurable admission settings — 2026-11-20

Source specification: https://chatgpt.com/s/cx_6b009b9ce8288191be98bdb4c796f818, read in the browser on 2026-11-20. This is a historical research summary, not proof of a universal optimum. Confirmed selection settings are 48/36 hours, 15 million USDT quote turnover, and at least 15 cumulative PAPER cycles including confirmed closed sessions. The ON/HOT restriction, modeled two REAL owners, 7,000 capital, and experimental drawdown settings are not enabled by this patch.

| Key in config.json | Applied value | Missing-key default | Meaning |
|---|---:|---:|---|
| admission_window_hours | [48, 36] | [48, 24] | Two descending positive integer hour windows, ending at the same last closed UTC hour. Both must pass the existing Net>0 / DD<=50 / cycles>=2 gate. Ranking uses the shorter window. |
| scanner_min_volume_usdt | 15000000 | 20000000 | Minimum current 24h USDT quote turnover, inclusive. Finite positive number; applied to supervisor scanner, standalone scanner and new daily-universe candidates. |
| real_use_cumulative_paper_cycles | true | false | New REAL admission uses unique confirmed historical PAPER session cycles plus current session. Current PAPER rotation age and all REAL incumbent cycle counters remain per-session. |
| min_cycles_for_rank | 15 | Existing caller defaults | Existing minimum admission cycle threshold; retained at 15. |

Cumulative history is deduplicated by started_at. A closed current state already archived is counted once. Conflicting identities/counts, incomplete closes, missing hedge legs or closing receipts, nonflat quantities and unresolved execution/accounting block evaluation. Existing closed Net continues to come from the authoritative PAPER budget ledger; this change does not invent historical capital or deals. Closed archives prior to this budget are cycle evidence only, not newly credited profits.

The fresh gate cache includes the configured windows; 48/24 cache entries cannot satisfy 48/36. Daily historical admission requires all currently configured windows and the matching funding mode. Existing PAPER workers keep the prior exemption from fresh admission, and established lifecycle/cooldown/return protections are preserved. Changing scanner volume during a daily run invalidates publication through the effective-config check.

Reload: supervisor_service launches a fresh swarm_manager process each normal cycle and that process reads config.json. The new keys are read on its next normal cycle, without a restart. Standalone and daily scanners read them on their next invocation. Daily-universe publication is not forced by this change. No PM2 commands, process restarts, optimization, private exchange calls, orders, slot changes, whitelist changes, capital changes or REAL activation are part of deployment. Offline validation does not certify the production REAL executor or support two shared-account owners.

Integration baseline: runtime HEAD 37ebc30def9ca07a53267d497c73b2030bebc9d8. The pre-existing core.direct_network module and the direct/public transport integrations in scanner/backtest_gate.py and scanner/rank_tickers.py are preserved byte-for-byte in the tested candidate. Development started from cd85539 in an isolated worktree; the older baseline lacked those already deployed changes, so the final runtime patch is computed against the actual runtime preimages, not a whole-file overwrite.

Artifacts and test logs: reports/configurable_admission_20261120/. The deployment manifest records source hashes, runtime HEAD, before/after config-key values, backup location and preservation of the direct-network implementation. No commit is created by this work.


## Deployment and verification

Applied on 2026-11-20 at 19:30:45 UTC (21:30:45 Kaliningrad) to C:/Python/Prosperous_Bot/futures_portfolio. Backups: C:/Python/Prosperous_Bot/futures_portfolio/reports/configurable_admission_backup_20261120T193045Z. Final focused pytest run: **173 passed in 41.44s**, exit 0. Post-deploy configuration, Python syntax, installed hashes and all 17 available PAPER cycle histories PASS using the native .venv Python. Earlier failed development runs are not acceptance results; a probe using the minimal bundled Python lacked aiohttp and was rerun successfully with the native environment. Runtime pickup on a subsequent normal supervisor cycle has not been claimed. REAL remains disabled with zero slots. No commit, merge, push, restart or optimization was performed.

Exact command: `.venv/Scripts/python.exe -B -m pytest --ignore-safety tests/test_configurable_admission.py tests/test_backtest_gate.py tests/test_daily_universe.py tests/test_paper_budget.py tests/test_paper_rotation_limit.py tests/test_get_bot_efficiency.py tests/test_supervisor.py tests/test_paper_idle_rotation.py -q` (native interpreter from the runtime, cwd isolated candidate). `--ignore-safety` is limited to isolated mocked tests and does not weaken production guards.
