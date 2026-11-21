# Work protocol — SOPH, Binance networking, integration and repository tests

Recorded: 2026-11-17. This protocol summarizes completed work and preserves the distinction between historical results, accepted settings, observed runtime behavior and outstanding checks.

## 1. SOPH failure and selected policy

The SOPHUSDT session was administratively rotated after its sixth cycle, rather than closed because the 5,000-USDT margin reserve was exhausted. Receipt-based reconstruction matched 279 heartbeats. At minimum strategy TPV 908.814078 USDT, modeled collateral including reserve was 5,908.814078 versus maintenance 5.136372.

Losing open PAPER sessions are protected from normal and idle administrative rotation; unknown exposure is retained until clarified. The existing replacement limit is honored. Explicit stops, quarantine and risk guards remain active. The final user-selected policy preserves profit trailing activation at +1.55% (distance 0.3%); activation at 0% was research, not the selected configuration. A replacement limit does not override session protection and is not a target number of closures.

Selected annual SOPH backtest: native exit 0, 525,600 closed Binance minute bars, return +52.3822%, strategy MaxDD 115.9934%, accounting residual zero. Strategy DD above 100% is distinct from account DD including collateral. Research controls: activation 0% returned -13.0710%; disabling the TPV stop with reserve returned +122.7678% with DD 193.6883%; without reserve returned -96.3897% and one modeled predictive margin close. These controls did not change the accepted risk policy. A standalone ticker replay does not test supervisor rotation.

Primary records: docs/SOPH_FAILURE_20261115.md and docs/SOPH_NO_ROTATION_20261115.md; local research artifacts under reports/soph_audit/ and reports/soph_no_stop/.

## 2. Direct Binance transport

Cause: disabling requests environment proxies did not override Windows VPN/TUN routing. Existing sockets used 172.18.0.1 on singbox_tun; the scanner also inherited proxy environment settings.

core/direct_network.py discovers one connected physical IPv4 gateway and binds source address plus Winsock IP_UNICAST_IF. Ambiguous/missing adapters and binding failures fail closed. No fallback to the TUN/default route. Covered surfaces: requests/python-binance, aiohttp REST/WebSocket, urllib HTTPS, websockets, trading/scanner/admission clients, backtest downloads and standalone Binance tools. Explicit proxies and HTTP downgrades are rejected; TLS verification is retained. No new dependencies or trading/accounting policy changes. Windows DNS resolution is retained; DNS transport itself is not replaced.

Public probes returned HTTP 200 for Futures, Spot and data.binance.vision, and WebSocket SUBSCRIBE acknowledgement. Inspected sockets used physical interface 11 / source 192.168.1.3 while VPN remained enabled; these values were discovered, not hardcoded. The depth collector now explicitly subscribes on the public stream endpoint and ignores acknowledgements. Earlier raw/legacy WS attempts that timed out were not accepted as successful checks.

Validation at that stage: 21 focused tests and 86 existing scanner/read-only/stream tests passed. Private stream delivery and actual adapter-failure behavior were not certified. Details: docs/BINANCE_PHYSICAL_DIRECT_20261117.md in the integrated repository.

## 3. PM2 restart helper and runtime observation

scripts/binance_runtime_network.py snapshots PAPER JSON and SQLite budget state, refuses REAL mode/processes and restarts only online paper-* plus supervisor-service. It does not reset positions or start previously stopped Telegram workers.

The user executed restart-paper. PM2 output was UTF-8, but Python reader threads attempted CP1251 decoding and raised UnicodeDecodeError despite completed restarts. Both subprocess calls now capture bytes; JSON parsing remains locale-independent. A regression test used UTF-8 Cyrillic and emoji without accessing PM2.

Read-only PID/parent/TCP correlation subsequently observed physical source 192.168.1.3 for all eight workers: IDOL, JCT, INIT, AKE, SOPH, BULLA, LIT and ARIA. JCT minute heartbeats and a completed supervisor cycle were observed. These are historical observations, not continuous monitoring or proof of private account traffic. The agent's PM2 RPC access returned EPERM; no repeat restart was performed for verification. The user later reported another swarm restart.

## 4. Saved work and merge

Relevant commits:
- 944aede: physical-adapter networking.
- 2989ec6: PM2 subprocess byte capture.
- 43f6b9b: admission-gate mocks updated to public_urlopen.
- 227444c: saved SQLite budget connection/transaction fixes, dashboard time import, optimizer source path and regression coverage. Empty-window attribution was already upstream.
- 62715c0: ticker-list config snapshot.
- dc2531e: preserve OpenCode's equivalent four-line transport-mock correction.
- ed3596c: integration validation fixture and provenance.
- 997ff6e: integrate concurrent remote master updates.
- 74e81815e22d6a64957185d0f0670bf7b936b91a: final master merge, PR #54.

Commit/push/merge was explicitly authorized earlier. Source/config snapshots and histories were preserved without reset/restore/clean. Integration used chore/save-remaining-20261117. At the previous synchronization check the relevant branches matched remote and worktrees were clean; this statement is a historical checkpoint, not a fresh remote comparison.

Final integration suites: Python 3.13.15 research/fix suite 135 passed, one existing Optuna warning, exit 0; Python 3.11.9 runtime/network suite 218 passed, exit 0. Suites overlap and are not summed as unique coverage. The earlier 3.11 research attempt failed due to missing matplotlib and a stale session fake; it is not a PASS. No packages were installed into the trading venv. Known optimizer lookback no-op remains characterized, not repaired or optimized. Details: docs/SAVE_REMAINING_20261117.md in the integrated repository.

## 5. OpenCode full repository test audit

Managed Orca run: run_a3e7f294f1d2. Original task task_53c1fd2576ce / dispatch ctx_5fc453e0bff1. Worker worktree:
C:/Users/svsma/orca/workspaces/futures_portfolio/full-repo-tests-20261117

Tested HEAD: 74e81815e22d6a64957185d0f0670bf7b936b91a. Tracked source was clean before/after. Python 3.13.15 with existing dependencies; no installs. The futures_portfolio import namespace was explicitly pinned to this isolated checkout to avoid loading the stale neighboring workspace.

Full canonical pytest testpaths: tests, rebalance/scenarios, accounting/scenarios. No -k, ignore, subset selection or assertion weakening. Native exit 1; 271.46 seconds; 2,093 main cases: 2,091 passed, 1 failed, 1 skipped, plus 16 successful subtests and 13 warnings. JUnit tests=2109 includes the 16 subtests; XML has 2,093 testcase elements, failures=1, errors=0, skipped=1. Capturing an accurate audit was completed; the test suite did not pass.

Failure: tests/test_scripts_scope_gap.py::test_collect_depth_zec_offline. scripts/collect_depth_zec.py now calls await ws.send(SUBSCRIBE...), while FakeWS implements recv/context-manager methods but no send. The collector's added subscription exposed a stale offline test double. No test/source fix, retry or assertion weakening was performed.

Skip: tests/test_quant_audit_metrics.py::test_backtest_sharpe_and_sortino_annualization requires missing local ZECUSDT Feather history.

Outside canonical testpaths, not run: backtest/48h_test.py, run_zec_test.py, scripts/test_binance_direct.py and the archived reports/swarm_replay_30d_20261113/test_shared_real_account.py. This audit is not a new annual replay or live acceptance test.

Safety limitation: tests/conftest.py interpreted inaccessible PM2 as no live system, although the user-restarted swarm was running. Isolation relied on mocked external surfaces; PM2 accessibility is not proof that the swarm is absent. No swarm restart, account mutation or live trading action was performed by this audit.

Original artifacts:
C:/Users/svsma/orca/workspaces/futures_portfolio/full-repo-tests-20261117/reports/full_suite_20261117/
Contains REPORT.md, stdout.log, stderr.log, junit.xml, collected_tests.txt, run_full_suite.ps1 and manifests. A report-only follow-up corrected JUnit arithmetic; original logs/XML were not modified. A documentation copy of REPORT.md is preserved beside this protocol as FULL_SUITE_AUDIT_20261117.md.

## 6. Documentation checkpoint and remaining work

The current documentation workspace is FMProducer/futures_portfolio at 227444cdb670b700403cfe9d09066068723b5428. It is not the tested master checkout. Before documentation edits it already contained changes in optimization/optimize_trend_guard.py and tests/test_bugfix_four_verification.py; they were preserved and excluded from this documentation task.

This task updates only CHANGELOG.md and documentation, plus an Obsidian protocol/index. No new commit, push, merge, test run, source fix or restart is included.

Open items: repair the FakeWS subscription test double and validate the affected path under a separately authorized fix; provide exact ZEC data if the skipped metric test must run; private-stream/hardware-failure acceptance remains unverified. The full-suite failure cannot be described as a green regression result.
