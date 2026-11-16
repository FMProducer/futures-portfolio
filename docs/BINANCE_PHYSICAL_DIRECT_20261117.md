# Binance: physical-adapter-only networking

Base: `5d87c3133ecdaab3f64cff97f802bcfa5a094282`.
Branch: `fix/binance-physical-direct`.
Worktree: `C:/Users/svsma/orca/workspaces/futures_portfolio/binance-physical-direct-20261117`.
Production files: `C:/Python/Prosperous_Bot/futures_portfolio`.

## Requirement and cause

Binance traffic must not use HTTP/SOCKS proxies, VPN exits or the local TUN.
The existing trading connector disabled requests environment proxies, but that
did not override Windows routing. Existing Python connections had local address
`172.18.0.1` on `singbox_tun`. The scanner explicitly used `trust_env=True`.
A fresh connector could fetch JCT candles while an existing worker kept receiving
HTTP 451; this did not prove the fresh connection bypassed TUN.

## Change

`core/direct_network.py` discovers one connected **physical** Windows adapter
with an IPv4 default gateway. Connections bind both the adapter's IPv4 source
and Winsock `IP_UNICAST_IF` (31), selecting the interface in network byte order.
At validation time this was Ethernet interface **11**, address **192.168.1.3**.
Neither value is hardcoded. Multiple eligible physical gateways, missing adapters,
binding errors and unsupported platforms fail closed; no default-route fallback.
Existing VPN/global routes are not changed or disabled.

Applied to requests/python-binance, aiohttp REST/WS, urllib HTTPS and websockets.
Proxy environment discovery is disabled. Requests rejects explicit proxies and
HTTP downgrade; urllib rejects HTTP/tunnels and retains TLS verification.
Network discovery occurs only when networking is used, preserving offline imports.
The current environment is Windows, Python 3.11.9, aiohttp 3.14.1 and websockets 17.3.
DNS names are resolved by Windows; the resulting Binance connections are IPv4
and constrained to the physical interface. OS DNS transport itself is not replaced.

## Callers and scope

- `core/connector.py`: shared client for trading/supervisor/public/private REST.
- `scanner/rank_tickers.py`, `scanner/backtest_gate.py`, inherited daily-universe reads.
- Live-data download in `backtest/backtest_rebalance.py`.
- `rebalance/binance_readonly.py`, `rebalance/public_paper.py`, `rebalance/user_stream.py`.
- Standalone download, market snapshot, audit, depth collection and route-probe scripts.

Telegram and localhost dashboard/observer connections are intentionally outside
the Binance transport. Immutable archived research/evidence copies are not rewritten.
No new dependencies, trading parameters, position accounting or credentials changed.

The depth collector used a legacy `/ws/` endpoint. It now uses the public stream
endpoint with an explicit subscription and ignores acknowledgements instead of
recording them as empty bid/ask snapshots. Binance's architecture notice:
https://www.binance.com/en-ZA/support/announcement/detail/ebf9b0aa9eca4ff3804eef6fb09ba32a

## Validation

- 21 targeted tests passed: direct-route fail-closed cases, proxy environment,
  explicit proxy rejection, TLS, connector initialization, gate and downloader.
- 86 existing scanner/daily-universe/read-only/user-stream tests passed.
- Syntax checked for all rollout Python files; `git diff --check` passed.
- Real public-only probes: Futures, Spot and data.binance.vision returned HTTP 200;
  urllib and aiohttp returned 200; WebSocket acknowledged an explicit subscription.
  Every inspected socket had local address 192.168.1.3 and `IP_UNICAST_IF=11`.
  VPN/TUN remained enabled. No keys, orders, transfers or private account calls used.

Earlier attempts on legacy/raw WebSocket URLs completed a handshake but timed out
waiting for frames/pong; they are not counted as a successful application check.
Final verification uses `/market/stream` with SUBSCRIBE acknowledgement.

Run the exact public probe in the production checkout:
```powershell
.\.venv\Scripts\python.exe -B scripts\verify_binance_physical.py
```
Evidence: `reports/binance_physical_20261117/physical_probe.stdout.json` and
`physical_probe_exit.json` in the production checkout. Source backups and before/after
hashes are under the research worktree's `reports/binance_physical_20261117/`.

## Rollout boundary

Only networking files and matching tests were copied after checking production
source provenance against the baseline. The unrelated existing change in
`backtest/dashboard_attribution.py` was preserved. No config/state reset or push.

The agent cannot access the running PM2 daemon: `connect EPERM //./pipe/rpc.sock`,
including from a separate Orca shell. Therefore copying source does **not** establish
that running workers have switched transports. Existing pooled connections need
the targeted worker restart from the user's PM2-capable console:
```powershell
.\.venv\Scripts\python.exe scripts\binance_runtime_network.py restart-paper
```
The script refuses REAL mode/processes, checks the checkout, snapshots PAPER JSON
and the SQLite budget, and restarts only online `paper-*` plus `supervisor-service`.
Stopped Telegram/other stopped workers are not started. Snapshots are observational,
not an atomic historical account snapshot. It never resets positions or restores files.

Runtime acceptance remains pending: inspect the restarted worker PIDs' connections
for physical source addresses, fresh heartbeats and absence of new 451 errors.
Private account/user-stream delivery and interface-failure behavior on actual
hardware are not certified by public probes and mocked tests.
