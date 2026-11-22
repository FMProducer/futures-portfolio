# Dashboard and observer CPU reduction — 2026-11-22

## Outcome and boundaries

The patch was copied to `C:/Python/Prosperous_Bot/futures_portfolio` after the user stopped only `swarm-dashboard` and `swarm-observer`. After the requested restart, the user reported that the fan stopped buzzing. This is operator-confirmed qualitative improvement, not a measured one-hour CPU acceptance result.

Trading logic, risk settings, position sizing, trading polling intervals and accounting state were not changed. No dependencies were installed. The remaining PM2 services were not stopped by this work.

## Confirmed causes

- `get_log_tail()` and heartbeat checks loaded entire log files before selecting a short tail.
- `reconstruct()` repeatedly parsed about 70,562 historical timestamps. The observer invokes reconstruction every five seconds.
- `read_curve()` repeatedly decoded roughly 78,000 observation summaries and returned every point to the browser.
- The observer also requested `/health` every five seconds. That endpoint called the full overview, including session reconstruction and the graph, although its health decision only needed supervisor status and REAL bot heartbeats.
- Page timers allowed overlapping requests and continued polling hidden tabs. A ticker page requested logs twice per cycle. Historical dashboard logs contained Waitress queue-depth warnings.

The diagnostic 10.07-second process sample recorded 1.734 CPU seconds for dashboard worker PID 34052 and 0.859 seconds for observer PID 16016. Trading workers were nearly idle in this sample. The old incident PIDs were not conclusively mapped to these new processes; neither a memory leak nor an exchange API change was established as the primary cause.

## Changes

- Add shared binary tail reads from EOF; count lines in chunks once, then count appended bytes. Preserve pagination, CR/LF/CRLF, partial lines and rotation handling. Bound log responses to 2,000 requested lines.
- Cache parsed heartbeat/rebalance samples and parse only appended log lines. Re-read state/wallet/archive data to preserve current session classification.
- Cache decoded observation rows and cumulative drawdown state. Read new rows after the saved SQLite rowid. Detect replacement, shrinking history, out-of-order additions and standalone historical edits before rebuilding.
- Render a compact graph preserving bucket endpoints, extrema, maximum-drawdown samples and gap boundaries. All observations remain on disk; aggregate drawdown uses the complete history. The frozen example sends 1,510 plot points instead of 78,094.
- Allow one in-flight page refresh, pause hidden tabs, resume when visible, add a 15-second GET timeout and use the ticker response's 150 log lines instead of a second log request.
- Make `/health` read only supervisor status and REAL ticker summaries. Preserve its existing maximum-one-stale-bot rule and response fields.

## Frozen-input performance evidence

Python 3.11.9, same locally copied inputs for both versions. Each function was run four times; the table uses the median of the last three wall times. Cold loads are excluded from these warm figures.

| Operation | Before | After |
|---|---:|---:|
| Session reconstruction | 777.4 ms | 102.1 ms |
| Observation curve | 1302.2 ms | 6.15 ms |
| Last 150 AKE log lines | 3.75 ms | 0.302 ms |

Warm reconstruction CPU median decreased from 0.78125 to 0.09375 seconds. The curve's warm CPU median was 0.015625 seconds; Windows CPU timer granularity limits interpretation of individual short calls. The candidate cold graph load took about 1.30 seconds, so the first request still warms the cache.

Session reconstruction and log-tail results matched exactly. Every returned reduced graph point matched its original full-history point; Net, maximum drawdown and other graph metadata matched. See `reports/dashboard_cpu_20261122/result.json` for measurements, source hashes, input hashes and parity verdicts.

## Verification provenance

Baseline runtime HEAD: `d48f8198461ddfafd276e336aa63f5d6135823fb` on `master`. Workspace baseline: `9a25630f2b6f2854c1de822e905c627d72b6bb53` on `FMProducer/futures_portfolio`. Both initially had clean working trees; only this task's files were subsequently staged.

Interpreter: `C:/Python/Prosperous_Bot/futures_portfolio/.venv/Scripts/python.exe` (3.11.9). Commands ran in the Orca workspace, with its package paths pinned. Tests used temporary inputs/mocks; `--ignore-safety` was used for these explicitly isolated tests because the PM2 RPC preflight returned `EPERM`.

```text
python -B -m pytest tests/test_dashboard_performance.py tests/test_data_collector_gap.py tests/test_swarm_curve.py tests/test_swarm_history.py tests/test_paper_observer.py tests/test_dashboard_gap.py tests/test_swarm_dashboard.py -q --ignore-safety
39 passed in 3.87s; exit 0 (before the final /health-only correction)

python -B -m pytest tests/test_dashboard_gap.py tests/test_runtime_surface_gap.py -k "health or server_error_handlers" -q --ignore-safety
10 passed, 32 deselected in 1.56s; exit 0 (after the final /health correction)

node tests/dashboard_polling.cjs
3 PASS: overview, ticker and logs; exit 0
```

The final health correction only changed `dashboard/dashboard.py` and its affected tests. The performance modules retained the hashes in `result.json`. No full-suite rerun was performed or claimed. Direct-script imports from the actual runtime directory succeeded; the runtime JavaScript checks passed. Deployment verification matched all 12 source/test files against their manifest hashes; `git diff --check` passed.

Evidence retained under `reports/dashboard_cpu_20261122/`: `result.json`, `pytest.log`, `health_pytest.log`, `benchmark.log`, the executed pre-deployment `verify.py`, frozen `inputs/`, and `deploy_20261122_114802/` with originals, baseline HEAD/status/diff and a 12-file manifest. Selected small evidence files are versioned; raw inputs and backup originals remain local. The benchmark script captured baseline imports before deployment; rerunning it against the deployed runtime does not reproduce that historical baseline without restoring the saved baseline sources in an isolated directory.

## Deployment and remaining limits

PM2 RPC returned `EPERM` from both the agent process and an Orca shell. Native GUI control was unavailable. The user stopped the two services, the agent copied verified files and the user was instructed to restart those same services and refresh the browser with Ctrl+F5. The subsequent user report confirmed that the fan noise stopped. No exact restart time or post-restart PID/CPU sample was independently captured.

- No post-restart one-hour CPU, request-rate, latency percentile or memory-growth measurement is available.
- Observation caching follows the append-only writer contract. Historical edits combined with inserts in the same interval require a full cache reload; standalone historical edits trigger a rebuild.
- Graph reduction changes visual point density and hover samples; full-history drawdown remains intact. Gap/invalid-point preservation can make the display exceed the nominal 2,000-point target.
- Caches retain parsed samples rather than raw log text and have bounded file/root entry counts. Memory consumption over a long running session remains unmeasured.
- No parameter optimization, exchange action, dependency repair or unrelated incident remediation was performed.
