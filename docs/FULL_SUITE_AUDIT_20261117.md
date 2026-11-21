# Full test suite audit — futures_portfolio @ 74e81815

Date: 2026-11-17 (UTC+2). Audit only: no code, tests, configs, PM2, or swarm state was modified.
Final verdict: suite completed, **NON-ZERO pytest exit (1)** — 1 genuine test failure at baseline.

## 1. Source snapshot under test
- Worktree: `C:\Users\svsma\orca\workspaces\futures_portfolio\full-repo-tests-20261117`
- Branch: `FMProducer/full-repo-tests-20261117` (Orca worktree from origin/master)
- HEAD (pre and post run): `74e81815e22d6a64957185d0f0670bf7b936b91a` — matches expected baseline; `origin/master` re-fetched and unchanged.
- `git status --porcelain`: **empty before and after the run** (reports/ is gitignored, `.gitignore:14`).
- Blob manifest of all tracked files: `manifest_git_ls_files.txt` (git ls-files -s).
- SHA-256 of key files: `manifest_sha256.txt` (pyproject.toml, tests/conftest.py, alias sitecustomize, launcher).

## 2. Isolation (futures_portfolio alias)
- pyproject `pythonpath = ["..", "."]` makes `..` resolve `import futures_portfolio` to the stale adjacent checkout `C:\Users\svsma\orca\workspaces\futures_portfolio\futures_portfolio` (it has `__init__.py`), and it sits ahead of any PYTHONPATH entry.
- Fix (no source edits): temporary `sitecustomize.py` in isolated temp dir
  `C:\Users\svsma\AppData\Local\Temp\opencode\fp_full_suite_20261117\sitecustomize.py`,
  inserted via `PYTHONPATH`; it puts a meta_path finder at index 0 pinning `futures_portfolio`
  (top-level name; submodules via `__path__`) to this worktree root. sha256: `eaf72818...`.
- Pre-test probe confirmed all of `core`, `scanner`, `backtest`, `futures_portfolio(.core.main, .scanner.backtest_gate, .backtest.backtest_rebalance)` resolve to the worktree; alias `futures_portfolio.core.main` and `core.main` point to the same file.

## 3. Interpreter / environment
- `sys.executable`: `C:\Users\svsma\AppData\Local\Programs\Python\Python313\python.exe`, Python 3.13.15 (trading venv 3.11.9 lacks matplotlib; 3.13 used as instructed).
- pytest 9.1.1; third-party plugins: pytest-asyncio 1.4.0, pytest-cov 7.1.0, anyio 4.14.2 (its pytest plugin). pandas 3.0.5, numpy 2.5.3, matplotlib present. No packages installed.
- Trading venv untouched; no production stubs.

## 4. Exact command (launcher text in `run_full_suite.ps1`)
```
$env:PYTHONPATH = 'C:\Users\svsma\AppData\Local\Temp\opencode\fp_full_suite_20261117'
Set-Location C:\Users\svsma\orca\workspaces\futures_portfolio\full-repo-tests-20261117
& C:\Users\svsma\AppData\Local\Programs\Python\Python313\python.exe -m pytest --junitxml='reports\full_suite_20261117\junit.xml'
```
Run via `powershell -NoProfile -ExecutionPolicy Bypass -File reports\full_suite_20261117\run_full_suite.ps1`.
Plain full pytest per pyproject testpaths `["tests", "rebalance/scenarios", "accounting/scenarios"]`;
no `-k`, `--ignore`, file selection, xfail additions, or assertion weakening. `--junitxml` added only to capture the required artifact.

## 5. Run facts
- Start 2026-11-17T01:37:59.287 +02:00, end 01:42:32.546 +02:00, duration 271.46 s, **native exit code 1**.
- Full stdout: `stdout.log` (432 KB); stderr: `stderr.log` (empty); JUnit: `junit.xml`.
- Collection list: `collected_tests.txt` (2093 tests, collection exit 0, ~8.3 s).

## 6. Results
Main test cases (pytest collection, 2093 collected):

| metric | count |
|---|---|
| passed | 2091 |
| failed | **1** |
| skipped | 1 |
| xfailed | 0 |
| xpassed | 0 |
| errors | 0 |
| warnings | 13 |

JUnit XML: the testsuite element carries `tests=2109, failures=1, skipped=1, errors=0,
time=271.377` — i.e. 2109 records counting 2093 main test cases plus the 16 successful
subtests; the 16 subtests do not appear as separate `<testcase>` elements (there are 2093
of them) but they are included in the junit totals via the testsuite `tests` attribute.

### The failure (genuine test drift at baseline, not environment)
`tests/test_scripts_scope_gap.py::test_collect_depth_zec_offline`
- Traceback: `scripts\collect_depth_zec.py:37: await ws.send({'method': 'SUBSCRIBE', ...})` →
  `AttributeError: 'FakeWS' object has no attribute 'send'`.
- Diagnosis: the script now sends an explicit SUBSCRIBE frame on connect, but the test's `FakeWS`
  (tests/test_scripts_scope_gap.py:395) implements only `recv/__aenter__/__aexit__` — the test was
  not updated to the new script behavior. Pure offline fake (websockets.connect monkeypatched, no
  real network). Not re-run: single failure, deterministic cause, no retry warranted.

### The canonical skip
`tests/test_quant_audit_metrics.py:65` (junit testcase `test_backtest_sharpe_and_sortino_annualization`):
`requires local ZECUSDT feather history in data/` — expected environment skip, not a pass.

### Warnings (13 occurrences, 4 groups)
- `optimization/optimize_config.py:112` — UserWarning: leverage value out of IntDistribution range (1).
- `optimization/optimize_universal.py:317` — PedAnova low-target semantics warning (4).
- `optimization/optimize_universal.py:317` — "Target and region quantiles select the same set of trials" (4).
- `export_local_optuna_graphs.py:47` — PedAnova low-target semantics warning (3).
All optuna plotting/importance noise; no test depends on them.

## 7. Safety posture
- `tests/conftest.py` safety guard executed at configure: `pm2 jlist` was inaccessible from the test
  process (known EPERM for this agent), so the guard concluded "no live system" and did not block.
  The PAPER swarm was actually running (user-restarted); no PM2/swarm action was taken by this run.
- External surface blocked by autouse fixtures: real `binance.client.Client` calls patched globally;
  funding-reserve writes to the workspace guarded; `core/main.py` worker spawns guarded.
- pm2-control tests (dashboard, health_check, data_collector, main) all mock their subprocess calls.
- A few crash-recovery tests intentionally spawn isolated `python -I` child processes
  (test_funding_backup, test_funding_halt_crash_recovery, test_live_funding_durable, test_market_crash_intent) — loopback/temp-file only, no swarm, no Binance.

## 8. Tests outside canonical testpaths (reported, NOT executed)
- `backtest/48h_test.py`, `run_zec_test.py` — standalone backtest entrypoint scripts.
- `scripts/test_binance_direct.py` — manual connectivity probe (public endpoints only; would hit network, deliberately not run).
- `reports/swarm_replay_30d_20261113/test_shared_real_account.py` — historical report script, not a pytest test.

## 9. Not verified / limitations
- No re-run of the failed test (diagnosis is deterministic); original failing result kept.
- Suite not run under the trading 3.11 venv (no matplotlib) — Python 3.13 used per task instruction.
- The 4 out-of-testpaths files above were not executed.
- Safety guard's "no live system" verdict is unreliable here (pm2 inaccessible); independence of the
  swarm during the run rests on the mock layer in conftest, which is why external calls stayed blocked.
