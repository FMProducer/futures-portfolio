PS C:\Python\Prosperous_Bot\futures_portfolio> (Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned) ; (& c:\Python\Prosperous_Bot\futures_portfolio\.venv\Scripts\Activate.ps1)
(.venv) PS C:\Python\Prosperous_Bot\futures_portfolio> pm2 start ecosystem.config.js --update-env
[PM2][WARN] Applications supervisor-service, swarm-aggregator, telegram-sender not running, starting...
[PM2] App [supervisor-service] launched (1 instances)
[PM2] App [swarm-aggregator] launched (1 instances)
[PM2] App [telegram-sender] launched (1 instances)
┌────┬───────────────────────┬─────────────┬─────────┬─────────┬──────────┬────────┬──────┬───────────┬──────────┬──────────┬──────────┬──────────┐
│ id │ name                  │ namespace   │ version │ mode    │ pid      │ uptime │ ↺    │ status    │ cpu      │ mem      │ user     │ watching │
├────┼───────────────────────┼─────────────┼─────────┼─────────┼──────────┼────────┼──────┼───────────┼──────────┼──────────┼──────────┼──────────┤
│ 0  │ supervisor-service    │ default     │ N/A     │ fork    │ 4132     │ 0s     │ 0    │ online    │ 0%       │ 3.9mb    │ svsma    │ disabled │
│ 1  │ swarm-aggregator      │ default     │ N/A     │ fork    │ 9028     │ 0s     │ 0    │ online    │ 0%       │ 3.9mb    │ svsma    │ disabled │
│ 2  │ telegram-sender       │ default     │ N/A     │ fork    │ 4672     │ 0s     │ 0    │ online    │ 0%       │ 3.9mb    │ svsma    │ disabled │
└────┴───────────────────────┴─────────────┴─────────┴─────────┴──────────┴────────┴──────┴───────────┴──────────┴──────────┴──────────┴──────────┘
(.venv) PS C:\Python\Prosperous_Bot\futures_portfolio> pm2 logs
[TAILING] Tailing last 15 lines for [all] processes (change the value with --lines option)
C:\Users\svsma\.pm2\pm2.log last 15 lines:
PM2        | 2026-07-14T22:08:29: PM2 log: RPC socket file      : \\.\pipe\rpc.sock
PM2        | 2026-07-14T22:08:29: PM2 log: BUS socket file      : \\.\pipe\pub.sock
PM2        | 2026-07-14T22:08:29: PM2 log: Application log path : C:\Users\svsma\.pm2\logs
PM2        | 2026-07-14T22:08:29: PM2 log: Worker Interval      : 30000
PM2        | 2026-07-14T22:08:29: PM2 log: Process dump file    : C:\Users\svsma\.pm2\dump.pm2
PM2        | 2026-07-14T22:08:29: PM2 log: Concurrent actions   : 2
PM2        | 2026-07-14T22:08:29: PM2 log: SIGTERM timeout      : 1600
PM2        | 2026-07-14T22:08:29: PM2 log: Runtime Binary       : C:\Program Files\nodejs\node.exe
PM2        | 2026-07-14T22:08:29: PM2 log: ===============================================================================
PM2        | 2026-07-14T22:09:52: PM2 log: App [supervisor-service:0] starting in -fork mode-
PM2        | 2026-07-14T22:09:52: PM2 log: App [swarm-aggregator:1] starting in -fork mode-
PM2        | 2026-07-14T22:09:52: PM2 log: App [supervisor-service:0] online
PM2        | 2026-07-14T22:09:52: PM2 log: App [swarm-aggregator:1] online
PM2        | 2026-07-14T22:09:52: PM2 log: App [telegram-sender:2] starting in -fork mode-
PM2        | 2026-07-14T22:09:52: PM2 log: App [telegram-sender:2] online

C:\Users\svsma\.pm2\logs\telegram-sender-out.log last 15 lines:
C:\Python\Prosperous_Bot\futures_portfolio\logs\out_supervisor_service.log last 15 lines:
C:\Python\Prosperous_Bot\futures_portfolio\logs\err_aggregator.log last 15 lines:
C:\Python\Prosperous_Bot\futures_portfolio\logs\err_supervisor_service.log last 15 lines:
0|supervis | 2026-07-14 22:09:52: 2026-07-14 22:09:52,377 INFO: Supervisor Service started (Async Mode).
0|supervis | 2026-07-14 22:09:52: 2026-07-14 22:09:52,377 INFO: --- Running Supervisor Cycle at Tue Jul 14 22:09:52 2026 ---

C:\Users\svsma\.pm2\logs\telegram-sender-error.log last 15 lines:
2|telegram | 2026-07-14 19:30:43: 2026-07-14 19:30:43,349 INFO: Telegram Sender Worker started.
2|telegram | 2026-07-14 19:31:52: 2026-07-14 19:31:52,154 INFO: Telegram Sender Worker started.
2|telegram | 2026-07-14 19:32:27: 2026-07-14 19:32:27,747 INFO: Telegram Sender Worker started.
2|telegram | 2026-07-14 21:03:05: 2026-07-14 21:03:05,700 INFO: Telegram Sender Worker started.
2|telegram | 2026-07-14 21:03:09: 2026-07-14 21:03:09,749 ERROR: Connection error: ServerDisconnectedError - Server disconnected
2|telegram | 2026-07-14 21:13:56: 2026-07-14 21:13:56,841 INFO: Telegram Sender Worker started.
2|telegram | 2026-07-14 21:28:41: 2026-07-14 21:28:41,005 INFO: Telegram Sender Worker started.
2|telegram | 2026-07-14 21:43:49: 2026-07-14 21:43:49,764 ERROR: Connection error: ClientConnectorError - Cannot connect to host api.telegram.org:443 ssl:default [None]
2|telegram | 2026-07-14 21:43:59: 2026-07-14 21:43:59,775 ERROR: Connection error: ClientConnectorError - Cannot connect to host api.telegram.org:443 ssl:default [None]
2|telegram | 2026-07-14 21:44:09: 2026-07-14 21:44:09,780 ERROR: Connection error: ClientConnectorError - Cannot connect to host api.telegram.org:443 ssl:default [None]
2|telegram | 2026-07-14 21:44:19: 2026-07-14 21:44:19,789 ERROR: Connection error: ClientConnectorError - Cannot connect to host api.telegram.org:443 ssl:default [None]
2|telegram | 2026-07-14 21:44:29: 2026-07-14 21:44:29,802 ERROR: Connection error: ClientConnectorError - Cannot connect to host api.telegram.org:443 ssl:default [None]
2|telegram | 2026-07-14 21:44:39: 2026-07-14 21:44:39,812 ERROR: Connection error: ClientConnectorError - Cannot connect to host api.telegram.org:443 ssl:default [None]
2|telegram | 2026-07-14 21:44:49: 2026-07-14 21:44:49,829 ERROR: Connection error: ClientConnectorError - Cannot connect to host api.telegram.org:443 ssl:default [None]
2|telegram | 2026-07-14 22:09:52: 2026-07-14 22:09:52,589 INFO: Telegram Sender Worker started.

C:\Python\Prosperous_Bot\futures_portfolio\logs\out_aggregator.log last 15 lines:
1|swarm-ag | 2026-07-14 22:09:52: 2026-07-14 22:09:52,939 INFO: Status Aggregator started.
1|swarm-ag | 2026-07-14 22:09:52: 2026-07-14 22:09:52,939 INFO: Telegram Queue ENABLED: C:\Python\Prosperous_Bot\futures_portfolio\core\signals\telegram_queue

PM2      | App [paper-lumia:3] starting in -fork mode-
PM2      | App [paper-lumia:3] online
PM2      | App [paper-dodox:4] starting in -fork mode-
PM2      | App [paper-dodox:4] online
PM2      | App [paper-kaito:5] starting in -fork mode-
PM2      | App [paper-kaito:5] online
PM2      | App [paper-vanry:6] starting in -fork mode-
PM2      | App [paper-vanry:6] online
PM2      | App [paper-hei:7] starting in -fork mode-
PM2      | App [paper-hei:7] online
3|paper-lumia  | 2026-07-14 22:10:21: 2026-07-14 22:10:21,255 INFO: BinanceConnector initialized and verified successfully.
3|paper-lumia  | 2026-07-14 22:10:21: 2026-07-14 22:10:21,256 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_LUMIAUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_LUMIAUSDT.json | Mode: PAPER
PM2            | App [paper-egld:8] starting in -fork mode-
PM2            | App [paper-egld:8] online
4|paper-dodox  | 2026-07-14 22:10:21: 2026-07-14 22:10:21,801 INFO: BinanceConnector initialized and verified successfully.
4|paper-dodox  | 2026-07-14 22:10:21: 2026-07-14 22:10:21,801 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_DODOXUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_DODOXUSDT.json | Mode: PAPER
PM2            | App [paper-syn:9] starting in -fork mode-
PM2            | App [paper-syn:9] online
5|paper-kaito  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,353 INFO: BinanceConnector initialized and verified successfully.
5|paper-kaito  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,353 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_KAITOUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_KAITOUSDT.json | Mode: PAPER
3|paper-lumia  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,733 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | LUMIAUSDT=0.07349 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
3|paper-lumia  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,733 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
3|paper-lumia  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,733 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
3|paper-lumia  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,733 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
3|paper-lumia  | 2026-07-14 22:10:22: TPV: 20.00
PM2            | App [paper-aiot:10] starting in -fork mode-
PM2            | App [paper-aiot:10] online
6|paper-vanry  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,991 INFO: BinanceConnector initialized and verified successfully.
6|paper-vanry  | 2026-07-14 22:10:22: 2026-07-14 22:10:22,991 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_VANRYUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_VANRYUSDT.json | Mode: PAPER
3|paper-lumia  | 2026-07-14 22:10:23: 2026-07-14 22:10:23,040 INFO: 📝 PAPER: BUY 954.1 LUMIAUSDT_LONG @ 0.07349
3|paper-lumia  | 2026-07-14 22:10:23: 2026-07-14 22:10:23,040 INFO: 📝 PAPER: SELL 954.1 LUMIAUSDT_SHORT @ 0.07349
3|paper-lumia  | 2026-07-14 22:10:23: 2026-07-14 22:10:23,040 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.07349
3|paper-lumia  | 2026-07-14 22:10:23: 2026-07-14 22:10:23,040 INFO: Rebalance #1 complete. TPV: 19.94
3|paper-lumia  | 2026-07-14 22:10:23: 2026-07-14 22:10:23,040 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
PM2            | App [paper-dexe:11] starting in -fork mode-
PM2            | App [paper-dexe:11] online
PM2            | App [paper-zec:12] starting in -fork mode-
PM2            | App [paper-zec:12] online
8|paper-egld   | 2026-07-14 22:10:24: 2026-07-14 22:10:24,135 INFO: BinanceConnector initialized and verified successfully.
8|paper-egld   | 2026-07-14 22:10:24: 2026-07-14 22:10:24,135 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_EGLDUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_EGLDUSDT.json | Mode: PAPER
PM2            | App [paper-the:13] starting in -fork mode-
PM2            | App [paper-the:13] online
PM2            | App [paper-virtual:14] starting in -fork mode-
PM2            | App [paper-virtual:14] online
10|paper-aiot  | 2026-07-14 22:10:25: 2026-07-14 22:10:25,317 INFO: BinanceConnector initialized and verified successfully.
10|paper-aiot  | 2026-07-14 22:10:25: 2026-07-14 22:10:25,317 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_AIOTUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_AIOTUSDT.json | Mode: PAPER
8|paper-egld   | 2026-07-14 22:10:25: 2026-07-14 22:10:25,719 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | EGLDUSDT=3.45764 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
8|paper-egld   | 2026-07-14 22:10:25: 2026-07-14 22:10:25,719 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
8|paper-egld   | 2026-07-14 22:10:25: 2026-07-14 22:10:25,719 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
8|paper-egld   | 2026-07-14 22:10:25: 2026-07-14 22:10:25,719 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
8|paper-egld   | 2026-07-14 22:10:25: TPV: 20.00
PM2            | App [paper-t:15] starting in -fork mode-
PM2            | App [paper-t:15] online
11|paper-dexe  | 2026-07-14 22:10:25: 2026-07-14 22:10:25,802 INFO: BinanceConnector initialized and verified successfully.
11|paper-dexe  | 2026-07-14 22:10:25: 2026-07-14 22:10:25,802 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_DEXEUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_DEXEUSDT.json | Mode: PAPER
8|paper-egld   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,036 INFO: 📝 PAPER: BUY 20.2 EGLDUSDT_LONG @ 3.45764
8|paper-egld   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,036 INFO: 📝 PAPER: SELL 20.2 EGLDUSDT_SHORT @ 3.45764
8|paper-egld   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,036 INFO: 🎯 Baseline Updated: Last rebalance price set to 3.45764
8|paper-egld   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,036 INFO: Rebalance #1 complete. TPV: 19.94
8|paper-egld   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,036 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,213 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | DODOXUSDT=0.0203239 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,213 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,213 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,213 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
4|paper-dodox  | 2026-07-14 22:10:26: TPV: 20.00
12|paper-zec   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,385 INFO: BinanceConnector initialized and verified successfully.
12|paper-zec   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,385 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_ZECUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_ZECUSDT.json | Mode: PAPER
PM2            | App [paper-1000xec:16] starting in -fork mode-
PM2            | App [paper-1000xec:16] online
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,540 INFO: 📝 PAPER: BUY 3450.0 DODOXUSDT_LONG @ 0.0203239
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,540 INFO: 📝 PAPER: SELL 3450.0 DODOXUSDT_SHORT @ 0.0203239
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,540 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.0203239
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,540 INFO: Rebalance #1 complete. TPV: 19.94
4|paper-dodox  | 2026-07-14 22:10:26: 2026-07-14 22:10:26,540 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
PM2            | App [paper-jto:17] starting in -fork mode-
13|paper-the   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,992 INFO: BinanceConnector initialized and verified successfully.
13|paper-the   | 2026-07-14 22:10:26: 2026-07-14 22:10:26,992 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_THEUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_THEUSDT.json | Mode: PAPER
PM2            | App [paper-jto:17] online
PM2            | App [paper-ldo:18] starting in -fork mode-
PM2            | App [paper-ldo:18] online
14|paper-virtual  | 2026-07-14 22:10:27: 2026-07-14 22:10:27,643 INFO: BinanceConnector initialized and verified successfully.
14|paper-virtual  | 2026-07-14 22:10:27: 2026-07-14 22:10:27,643 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_VIRTUALUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_VIRTUALUSDT.json | Mode: PAPER
15|paper-t        | 2026-07-14 22:10:28: 2026-07-14 22:10:28,178 INFO: BinanceConnector initialized and verified successfully.
15|paper-t        | 2026-07-14 22:10:28: 2026-07-14 22:10:28,178 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_TUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_TUSDT.json | Mode: PAPER
PM2               | App [paper-wld:19] starting in -fork mode-
PM2               | App [paper-wld:19] online
16|paper-1000xec  | 2026-07-14 22:10:28: 2026-07-14 22:10:28,769 INFO: BinanceConnector initialized and verified successfully.
16|paper-1000xec  | 2026-07-14 22:10:28: 2026-07-14 22:10:28,769 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_1000XECUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_1000XECUSDT.json | Mode: PAPER
PM2               | App [paper-1000pepe:20] starting in -fork mode-
PM2               | App [paper-1000pepe:20] online
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,019 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | VIRTUALUSDT=0.5877 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,019 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,019 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,019 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
14|paper-virtual  | 2026-07-14 22:10:29: TPV: 20.00
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,343 INFO: 📝 PAPER: BUY 119.1 VIRTUALUSDT_LONG @ 0.5877
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,344 INFO: 📝 PAPER: SELL 119.1 VIRTUALUSDT_SHORT @ 0.5877
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,344 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.5877
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,344 INFO: Rebalance #1 complete. TPV: 19.94
14|paper-virtual  | 2026-07-14 22:10:29: 2026-07-14 22:10:29,344 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
17|paper-jto      | 2026-07-14 22:10:29: 2026-07-14 22:10:29,362 INFO: BinanceConnector initialized and verified successfully.
17|paper-jto      | 2026-07-14 22:10:29: 2026-07-14 22:10:29,364 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_JTOUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_JTOUSDT.json | Mode: PAPER
PM2               | App [paper-ena:21] starting in -fork mode-
PM2               | App [paper-ena:21] online
18|paper-ldo      | 2026-07-14 22:10:30: 2026-07-14 22:10:30,028 INFO: BinanceConnector initialized and verified successfully.
18|paper-ldo      | 2026-07-14 22:10:30: 2026-07-14 22:10:30,028 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_LDOUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_LDOUSDT.json | Mode: PAPER
19|paper-wld      | 2026-07-14 22:10:30: 2026-07-14 22:10:30,568 INFO: BinanceConnector initialized and verified successfully.
19|paper-wld      | 2026-07-14 22:10:30: 2026-07-14 22:10:30,569 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_WLDUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_WLDUSDT.json | Mode: PAPER
PM2               | App [real-aiot:22] starting in -fork mode-
PM2               | App [real-aiot:22] online
20|paper-1000pepe | 2026-07-14 22:10:31: 2026-07-14 22:10:31,141 INFO: BinanceConnector initialized and verified successfully.
20|paper-1000pepe | 2026-07-14 22:10:31: 2026-07-14 22:10:31,141 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_1000PEPEUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_1000PEPEUSDT.json | Mode: PAPER
PM2               | App [real-tlm:23] starting in -fork mode-
PM2               | App [real-tlm:23] online
21|paper-ena      | 2026-07-14 22:10:31: 2026-07-14 22:10:31,789 INFO: BinanceConnector initialized and verified successfully.
21|paper-ena      | 2026-07-14 22:10:31: 2026-07-14 22:10:31,789 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_ENAUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_ENAUSDT.json | Mode: PAPER
PM2               | App [real-vanry:24] starting in -fork mode-
PM2               | App [real-vanry:24] online
20|paper-1000pepe | 2026-07-14 22:10:32: 2026-07-14 22:10:32,787 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | 1000PEPEUSDT=0.00281541 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
20|paper-1000pepe | 2026-07-14 22:10:32: 2026-07-14 22:10:32,787 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
20|paper-1000pepe | 2026-07-14 22:10:32: 2026-07-14 22:10:32,787 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
20|paper-1000pepe | 2026-07-14 22:10:32: 2026-07-14 22:10:32,787 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
20|paper-1000pepe | 2026-07-14 22:10:32: TPV: 20.00
PM2               | App [real-zec:25] starting in -fork mode-
PM2               | App [real-zec:25] online
20|paper-1000pepe | 2026-07-14 22:10:33: 2026-07-14 22:10:33,101 INFO: 📝 PAPER: BUY 24841.0 1000PEPEUSDT_LONG @ 0.00281541
20|paper-1000pepe | 2026-07-14 22:10:33: 2026-07-14 22:10:33,101 INFO: 📝 PAPER: SELL 24841.0 1000PEPEUSDT_SHORT @ 0.00281541
20|paper-1000pepe | 2026-07-14 22:10:33: 2026-07-14 22:10:33,101 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.00281541
20|paper-1000pepe | 2026-07-14 22:10:33: 2026-07-14 22:10:33,101 INFO: Rebalance #1 complete. TPV: 19.94
20|paper-1000pepe | 2026-07-14 22:10:33: 2026-07-14 22:10:33,101 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
0|supervisor-service  | 2026-07-14 22:10:33: 2026-07-14 22:10:33,256 INFO: Cycle completed successfully.
0|supervisor-service  | 2026-07-14 22:10:33: 2026-07-14 22:10:33,256 INFO: SUPERVISOR: 2026-07-14 22:09:53,401 INFO: --- Starting Pure Live Supervisor (Strict Scanner Sync) ---
0|supervisor-service  | 2026-07-14 22:10:33: 2026-07-14 22:10:33,256 INFO: SUPERVISOR: 2026-07-14 22:10:30,357 INFO: Cycle Complete. REAL Swarm: ['AIOTUSDT', 'TLMUSDT', 'VANRYUSDT', 'ZECUSDT']
0|supervisor-service  | 2026-07-14 22:10:33: 2026-07-14 22:10:33,256 INFO: Sleeping for 0.1 hours...
21|paper-ena          | 2026-07-14 22:10:35: 2026-07-14 22:10:35,937 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | ENAUSDT=0.08395 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
21|paper-ena          | 2026-07-14 22:10:35: 2026-07-14 22:10:35,937 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
21|paper-ena          | 2026-07-14 22:10:35: 2026-07-14 22:10:35,938 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
21|paper-ena          | 2026-07-14 22:10:35: 2026-07-14 22:10:35,938 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
21|paper-ena          | 2026-07-14 22:10:35: TPV: 20.00
25|real-zec           | 2026-07-14 22:10:36: 2026-07-14 22:10:36,205 INFO: BinanceConnector initialized and verified successfully.
25|real-zec           | 2026-07-14 22:10:36: 2026-07-14 22:10:36,205 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\real_state_ZECUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\shadow_state_ZECUSDT.json | Mode: REAL
21|paper-ena          | 2026-07-14 22:10:36: 2026-07-14 22:10:36,266 INFO: 📝 PAPER: BUY 833.0 ENAUSDT_LONG @ 0.08395
21|paper-ena          | 2026-07-14 22:10:36: 2026-07-14 22:10:36,266 INFO: 📝 PAPER: SELL 833.0 ENAUSDT_SHORT @ 0.08395
21|paper-ena          | 2026-07-14 22:10:36: 2026-07-14 22:10:36,266 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.08395
21|paper-ena          | 2026-07-14 22:10:36: 2026-07-14 22:10:36,266 INFO: Rebalance #1 complete. TPV: 19.94
21|paper-ena          | 2026-07-14 22:10:36: 2026-07-14 22:10:36,267 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
15|paper-t            | 2026-07-14 22:10:36: 2026-07-14 22:10:36,813 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | TUSDT=0.004299 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
15|paper-t            | 2026-07-14 22:10:36: 2026-07-14 22:10:36,813 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
15|paper-t            | 2026-07-14 22:10:36: 2026-07-14 22:10:36,813 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
15|paper-t            | 2026-07-14 22:10:36: 2026-07-14 22:10:36,813 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
15|paper-t            | 2026-07-14 22:10:36: TPV: 20.00
16|paper-1000xec      | 2026-07-14 22:10:36: 2026-07-14 22:10:36,869 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | 1000XECUSDT=0.0058049 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
16|paper-1000xec      | 2026-07-14 22:10:36: 2026-07-14 22:10:36,870 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
16|paper-1000xec      | 2026-07-14 22:10:36: 2026-07-14 22:10:36,870 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
16|paper-1000xec      | 2026-07-14 22:10:36: 2026-07-14 22:10:36,870 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
16|paper-1000xec      | 2026-07-14 22:10:36: TPV: 20.00
23|real-tlm           | 2026-07-14 22:10:36: 2026-07-14 22:10:36,912 INFO: BinanceConnector initialized and verified successfully.
23|real-tlm           | 2026-07-14 22:10:36: 2026-07-14 22:10:36,912 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\real_state_TLMUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\shadow_state_TLMUSDT.json | Mode: REAL
15|paper-t            | 2026-07-14 22:10:37: 2026-07-14 22:10:37,156 INFO: 📝 PAPER: BUY 16284.0 TUSDT_LONG @ 0.004299
15|paper-t            | 2026-07-14 22:10:37: 2026-07-14 22:10:37,157 INFO: 📝 PAPER: SELL 16284.0 TUSDT_SHORT @ 0.004299
15|paper-t            | 2026-07-14 22:10:37: 2026-07-14 22:10:37,157 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.004299
15|paper-t            | 2026-07-14 22:10:37: 2026-07-14 22:10:37,157 INFO: Rebalance #1 complete. TPV: 19.94
15|paper-t            | 2026-07-14 22:10:37: 2026-07-14 22:10:37,157 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
16|paper-1000xec      | 2026-07-14 22:10:37: 2026-07-14 22:10:37,177 INFO: 📝 PAPER: BUY 12061.0 1000XECUSDT_LONG @ 0.0058049
16|paper-1000xec      | 2026-07-14 22:10:37: 2026-07-14 22:10:37,177 INFO: 📝 PAPER: SELL 12061.0 1000XECUSDT_SHORT @ 0.0058049
16|paper-1000xec      | 2026-07-14 22:10:37: 2026-07-14 22:10:37,177 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.0058049
16|paper-1000xec      | 2026-07-14 22:10:37: 2026-07-14 22:10:37,177 INFO: Rebalance #1 complete. TPV: 19.94
16|paper-1000xec      | 2026-07-14 22:10:37: 2026-07-14 22:10:37,178 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,515 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | JTOUSDT=0.642068 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,515 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,515 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,516 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
17|paper-jto          | 2026-07-14 22:10:37: TPV: 20.00
25|real-zec           | 2026-07-14 22:10:37: 2026-07-14 22:10:37,639 INFO: Hedge Mode verified.
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,816 INFO: 📝 PAPER: BUY 109.0 JTOUSDT_LONG @ 0.642068
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,816 INFO: 📝 PAPER: SELL 109.0 JTOUSDT_SHORT @ 0.642068
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,816 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.642068
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,816 INFO: Rebalance #1 complete. TPV: 19.94
17|paper-jto          | 2026-07-14 22:10:37: 2026-07-14 22:10:37,816 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
25|real-zec           | 2026-07-14 22:10:37: 2026-07-14 22:10:37,947 INFO: Leverage set to 7x for ZECUSDT
25|real-zec           | 2026-07-14 22:10:38: 2026-07-14 22:10:38,256 INFO: Margin Type set to CROSS for ZECUSDT
23|real-tlm           | 2026-07-14 22:10:38: 2026-07-14 22:10:38,396 INFO: Hedge Mode verified.
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,658 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | KAITOUSDT=0.770365 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,658 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,658 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,658 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
5|paper-kaito         | 2026-07-14 22:10:38: TPV: 20.00
23|real-tlm           | 2026-07-14 22:10:38: 2026-07-14 22:10:38,697 INFO: Leverage set to 7x for TLMUSDT
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,946 INFO: 📝 PAPER: BUY 90.7 KAITOUSDT_LONG @ 0.770365
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,946 INFO: 📝 PAPER: SELL 90.7 KAITOUSDT_SHORT @ 0.770365
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,946 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.770365
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,946 INFO: Rebalance #1 complete. TPV: 19.94
5|paper-kaito         | 2026-07-14 22:10:38: 2026-07-14 22:10:38,946 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
23|real-tlm           | 2026-07-14 22:10:38: 2026-07-14 22:10:38,990 INFO: Margin Type set to CROSS for TLMUSDT
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,212 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | VANRYUSDT=0.005454 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,212 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,212 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,212 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
6|paper-vanry         | 2026-07-14 22:10:39: TPV: 20.00
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,512 INFO: 📝 PAPER: BUY 12836.0 VANRYUSDT_LONG @ 0.005454
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,512 INFO: 📝 PAPER: SELL 12836.0 VANRYUSDT_SHORT @ 0.005454
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,512 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.005454
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,512 INFO: Rebalance #1 complete. TPV: 19.94
6|paper-vanry         | 2026-07-14 22:10:39: 2026-07-14 22:10:39,512 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
7|paper-hei           | 2026-07-14 22:10:39: 2026-07-14 22:10:39,548 INFO: BinanceConnector initialized and verified successfully.
7|paper-hei           | 2026-07-14 22:10:39: 2026-07-14 22:10:39,548 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_HEIUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_HEIUSDT.json | Mode: PAPER
25|real-zec           | 2026-07-14 22:10:39: 2026-07-14 22:10:39,584 INFO: Heartbeat: TPV=17.72 | PnL=-2.28 | ZECUSDT=536.87 | L:42.4% [-7.6%] {+7.52$} | S:53.5% [+3.5%] {+9.48$} | V:0.0% [+0.0%] {+0.00$} | C:4.0% {0.72$}
23|real-tlm           | 2026-07-14 22:10:40: 2026-07-14 22:10:40,268 INFO: Heartbeat: TPV=16.06 | PnL=-3.94 | TLMUSDT=0.00169714 | L:50.5% [+0.6%] {+8.12$} | S:49.5% [-0.6%] {+7.94$} | V:0.0% [+0.0%] {+0.00$} | C:0.0% {0.00$}
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,600 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | AIOTUSDT=0.0373423 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,600 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,600 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,600 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
10|paper-aiot         | 2026-07-14 22:10:41: TPV: 20.00
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,918 INFO: 📝 PAPER: BUY 1874.0 AIOTUSDT_LONG @ 0.0373423
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,918 INFO: 📝 PAPER: SELL 1874.0 AIOTUSDT_SHORT @ 0.0373423
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,918 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.0373423
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,918 INFO: Rebalance #1 complete. TPV: 19.94
10|paper-aiot         | 2026-07-14 22:10:41: 2026-07-14 22:10:41,920 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,255 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | HEIUSDT=0.106106 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,255 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,255 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,262 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
7|paper-hei           | 2026-07-14 22:10:44: TPV: 20.00
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,269 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | THEUSDT=0.0554166 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,270 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,270 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,270 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
13|paper-the          | 2026-07-14 22:10:44: TPV: 20.00
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,587 INFO: 📝 PAPER: BUY 659.7 HEIUSDT_LONG @ 0.106106
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,587 INFO: 📝 PAPER: SELL 659.7 HEIUSDT_SHORT @ 0.106106
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,587 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.106106
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,587 INFO: Rebalance #1 complete. TPV: 19.94
7|paper-hei           | 2026-07-14 22:10:44: 2026-07-14 22:10:44,587 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,596 INFO: 📝 PAPER: BUY 1263.7 THEUSDT_LONG @ 0.0554166
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,598 INFO: 📝 PAPER: SELL 1263.7 THEUSDT_SHORT @ 0.0554166
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,598 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.0554166
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,598 INFO: Rebalance #1 complete. TPV: 19.94
13|paper-the          | 2026-07-14 22:10:44: 2026-07-14 22:10:44,598 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
11|paper-dexe         | 2026-07-14 22:10:44: 2026-07-14 22:10:44,927 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | DEXEUSDT=41.68 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
11|paper-dexe         | 2026-07-14 22:10:44: 2026-07-14 22:10:44,927 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
11|paper-dexe         | 2026-07-14 22:10:44: 2026-07-14 22:10:44,927 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
11|paper-dexe         | 2026-07-14 22:10:44: 2026-07-14 22:10:44,927 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
11|paper-dexe         | 2026-07-14 22:10:44: TPV: 20.00
11|paper-dexe         | 2026-07-14 22:10:45: 2026-07-14 22:10:45,220 INFO: 📝 PAPER: BUY 1.67 DEXEUSDT_LONG @ 41.68
11|paper-dexe         | 2026-07-14 22:10:45: 2026-07-14 22:10:45,220 INFO: 📝 PAPER: SELL 1.67 DEXEUSDT_SHORT @ 41.68
11|paper-dexe         | 2026-07-14 22:10:45: 2026-07-14 22:10:45,220 INFO: 🎯 Baseline Updated: Last rebalance price set to 41.68
11|paper-dexe         | 2026-07-14 22:10:45: 2026-07-14 22:10:45,220 INFO: Rebalance #1 complete. TPV: 19.94
11|paper-dexe         | 2026-07-14 22:10:45: 2026-07-14 22:10:45,220 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
12|paper-zec          | 2026-07-14 22:10:45: 2026-07-14 22:10:45,530 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | ZECUSDT=536.839 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
12|paper-zec          | 2026-07-14 22:10:45: 2026-07-14 22:10:45,530 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
12|paper-zec          | 2026-07-14 22:10:45: 2026-07-14 22:10:45,530 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
12|paper-zec          | 2026-07-14 22:10:45: 2026-07-14 22:10:45,530 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
12|paper-zec          | 2026-07-14 22:10:45: TPV: 20.00
12|paper-zec          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,308 INFO: 📝 PAPER: BUY 0.13 ZECUSDT_LONG @ 536.839
12|paper-zec          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,308 INFO: 📝 PAPER: SELL 0.13 ZECUSDT_SHORT @ 536.839
12|paper-zec          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,308 INFO: 🎯 Baseline Updated: Last rebalance price set to 536.839
12|paper-zec          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,308 INFO: Rebalance #1 complete. TPV: 19.94
12|paper-zec          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,308 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,356 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | LDOUSDT=0.3273 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,356 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,357 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,357 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
18|paper-ldo          | 2026-07-14 22:10:46: TPV: 20.00
9|paper-syn           | 2026-07-14 22:10:46: 2026-07-14 22:10:46,632 INFO: BinanceConnector initialized and verified successfully.
9|paper-syn           | 2026-07-14 22:10:46: 2026-07-14 22:10:46,632 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_state_SYNUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\paper_shadow_SYNUSDT.json | Mode: PAPER
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,658 INFO: 📝 PAPER: BUY 213.0 LDOUSDT_LONG @ 0.3273
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,658 INFO: 📝 PAPER: SELL 213.0 LDOUSDT_SHORT @ 0.3273
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,658 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.3273
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,658 INFO: Rebalance #1 complete. TPV: 19.94
18|paper-ldo          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,658 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
19|paper-wld          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,901 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | WLDUSDT=0.4126 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
19|paper-wld          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,902 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
19|paper-wld          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,902 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
19|paper-wld          | 2026-07-14 22:10:46: 2026-07-14 22:10:46,902 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
19|paper-wld          | 2026-07-14 22:10:46: TPV: 20.00
19|paper-wld          | 2026-07-14 22:10:47: 2026-07-14 22:10:47,198 INFO: 📝 PAPER: BUY 169.0 WLDUSDT_LONG @ 0.4126
19|paper-wld          | 2026-07-14 22:10:47: 2026-07-14 22:10:47,198 INFO: 📝 PAPER: SELL 169.0 WLDUSDT_SHORT @ 0.4126
19|paper-wld          | 2026-07-14 22:10:47: 2026-07-14 22:10:47,198 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.4126
19|paper-wld          | 2026-07-14 22:10:47: 2026-07-14 22:10:47,198 INFO: Rebalance #1 complete. TPV: 19.94
19|paper-wld          | 2026-07-14 22:10:47: 2026-07-14 22:10:47,201 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
22|real-aiot          | 2026-07-14 22:10:48: 2026-07-14 22:10:48,472 INFO: BinanceConnector initialized and verified successfully.
22|real-aiot          | 2026-07-14 22:10:48: 2026-07-14 22:10:48,472 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\real_state_AIOTUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\shadow_state_AIOTUSDT.json | Mode: REAL
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,551 INFO: Heartbeat: TPV=20.00 | PnL=+0.00 | SYNUSDT=0.265696 | L:0.0% [-50.0%] {+0.00$} | S:0.0% [-50.0%] {+0.00$} | V:0.0% [+0.0%] {+0.00$} | C:100.0% {20.00$}
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,551 INFO: Rebalance triggered: LONG deviation -50.00% exceeds limit 4.00%
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,551 INFO: Rebalance triggered: SHORT deviation -50.00% exceeds limit 4.00%
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,551 INFO: Rebalance needed (2 fused actions). Shares: L:0.0% S:0.0% V:0.0%
9|paper-syn           | 2026-07-14 22:10:48: TPV: 20.00
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,867 INFO: 📝 PAPER: BUY 263.0 SYNUSDT_LONG @ 0.265696
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,867 INFO: 📝 PAPER: SELL 263.0 SYNUSDT_SHORT @ 0.265696
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,867 INFO: 🎯 Baseline Updated: Last rebalance price set to 0.265696
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,867 INFO: Rebalance #1 complete. TPV: 19.94
9|paper-syn           | 2026-07-14 22:10:48: 2026-07-14 22:10:48,867 INFO: ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.
24|real-vanry         | 2026-07-14 22:10:56: 2026-07-14 22:10:56,621 INFO: BinanceConnector initialized and verified successfully.
24|real-vanry         | 2026-07-14 22:10:56: 2026-07-14 22:10:56,621 INFO: 💾 State files: REAL=C:\Python\Prosperous_Bot\futures_portfolio\core\real_state_VANRYUSDT.json, PAPER=C:\Python\Prosperous_Bot\futures_portfolio\core\shadow_state_VANRYUSDT.json | Mode: REAL
22|real-aiot          | 2026-07-14 22:10:57: 2026-07-14 22:10:57,037 INFO: Hedge Mode verified.
22|real-aiot          | 2026-07-14 22:10:57: 2026-07-14 22:10:57,346 INFO: Leverage set to 7x for AIOTUSDT
22|real-aiot          | 2026-07-14 22:10:57: 2026-07-14 22:10:57,660 INFO: Margin Type set to CROSS for AIOTUSDT
24|real-vanry         | 2026-07-14 22:10:58: 2026-07-14 22:10:58,293 INFO: Hedge Mode verified.
24|real-vanry         | 2026-07-14 22:10:58: 2026-07-14 22:10:58,609 INFO: Leverage set to 7x for VANRYUSDT
24|real-vanry         | 2026-07-14 22:10:58: 2026-07-14 22:10:58,932 INFO: Margin Type set to CROSS for VANRYUSDT
22|real-aiot          | 2026-07-14 22:10:59: 2026-07-14 22:10:59,609 INFO: Heartbeat: TPV=19.89 | PnL=-0.11 | AIOTUSDT=0.0373353 | L:48.5% [-1.5%] {+9.65$} | S:51.5% [+1.4%] {+10.23$} | V:0.0% [+0.0%] {+0.00$} | C:0.0% {0.01$}
24|real-vanry         | 2026-07-14 22:11:00: 2026-07-14 22:11:00,603 INFO: Heartbeat: TPV=17.46 | PnL=-2.54 | VANRYUSDT=0.00546154 | L:49.8% [-0.2%] {+8.69$} | S:50.0% [-0.0%] {+8.72$} | V:0.0% [+0.0%] {+0.00$} | C:0.2% {0.04$}
3|paper-lumia         | 2026-07-14 22:11:40: 2026-07-14 22:11:40,406 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | LUMIAUSDT=0.0733257 | L:50.0% [-0.0%] {+9.96$} | S:50.3% [+0.3%] {+10.04$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
8|paper-egld          | 2026-07-14 22:11:42: 2026-07-14 22:11:42,973 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | EGLDUSDT=3.4569 | L:49.8% [-0.2%] {+9.93$} | S:50.3% [+0.3%] {+10.04$} | V:0.0% [+0.0%] {+0.00$} | C:-0.1% {0.00$}
4|paper-dodox         | 2026-07-14 22:11:43: 2026-07-14 22:11:43,616 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | DODOXUSDT=0.020166 | L:48.0% [-2.0%] {+9.58$} | S:52.2% [+2.2%] {+10.42$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
14|paper-virtual      | 2026-07-14 22:11:46: 2026-07-14 22:11:46,061 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | VIRTUALUSDT=0.5868 | L:49.6% [-0.4%] {+9.90$} | S:50.6% [+0.6%] {+10.10$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
20|paper-1000pepe     | 2026-07-14 22:11:50: 2026-07-14 22:11:50,500 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | 1000PEPEUSDT=0.00281921 | L:50.3% [+0.3%] {+10.03$} | S:50.0% [-0.0%] {+9.97$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
15|paper-t            | 2026-07-14 22:11:53: 2026-07-14 22:11:53,944 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | TUSDT=0.0042961 | L:49.9% [-0.1%] {+9.96$} | S:50.3% [+0.3%] {+10.04$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
16|paper-1000xec      | 2026-07-14 22:11:54: 2026-07-14 22:11:54,246 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | 1000XECUSDT=0.00579205 | L:49.5% [-0.6%] {+9.86$} | S:50.8% [+0.8%] {+10.14$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
21|paper-ena          | 2026-07-14 22:11:54: 2026-07-14 22:11:54,324 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | ENAUSDT=0.08387 | L:49.8% [-0.2%] {+9.93$} | S:50.4% [+0.4%] {+10.05$} | V:0.0% [+0.0%] {+0.00$} | C:-0.2% {0.00$}
17|paper-jto          | 2026-07-14 22:11:55: 2026-07-14 22:11:55,078 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | JTOUSDT=0.642372 | L:50.4% [+0.4%] {+10.04$} | S:49.9% [-0.1%] {+9.95$} | V:0.0% [+0.0%] {+0.00$} | C:-0.2% {0.00$}
5|paper-kaito         | 2026-07-14 22:11:56: 2026-07-14 22:11:56,316 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | KAITOUSDT=0.772478 | L:50.5% [+0.5%] {+10.07$} | S:49.8% [-0.2%] {+9.93$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
6|paper-vanry         | 2026-07-14 22:11:56: 2026-07-14 22:11:56,562 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | VANRYUSDT=0.00546328 | L:50.8% [+0.8%] {+10.13$} | S:49.5% [-0.5%] {+9.87$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
10|paper-aiot         | 2026-07-14 22:11:58: 2026-07-14 22:11:58,940 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | AIOTUSDT=0.03736 | L:50.2% [+0.2%] {+10.02$} | S:50.0% [+0.0%] {+9.98$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
7|paper-hei           | 2026-07-14 22:12:01: 2026-07-14 22:12:01,660 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | HEIUSDT=0.10562 | L:48.5% [-1.5%] {+9.68$} | S:51.7% [+1.7%] {+10.32$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
13|paper-the          | 2026-07-14 22:12:01: 2026-07-14 22:12:01,846 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | THEUSDT=0.05543 | L:50.4% [+0.4%] {+10.05$} | S:49.9% [-0.1%] {+9.95$} | V:0.0% [+0.0%] {+0.00$} | C:-0.3% {0.00$}
25|real-zec           | 2026-07-14 22:12:02: 2026-07-14 22:12:02,063 INFO: Heartbeat: TPV=17.71 | PnL=-2.29 | ZECUSDT=536.94 | L:42.5% [-7.5%] {+7.52$} | S:53.5% [+3.5%] {+9.47$} | V:0.0% [+0.0%] {+0.00$} | C:4.1% {0.72$}
23|real-tlm           | 2026-07-14 22:12:02: 2026-07-14 22:12:02,363 INFO: Heartbeat: TPV=15.87 | PnL=-4.13 | TLMUSDT=0.001684 | L:47.2% [-2.8%] {+7.50$} | S:52.8% [+2.8%] {+8.38$} | V:0.0% [+0.0%] {+0.00$} | C:0.0% {0.00$}
11|paper-dexe         | 2026-07-14 22:12:02: 2026-07-14 22:12:02,850 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | DEXEUSDT=41.6743 | L:49.9% [-0.2%] {+9.94$} | S:49.9% [-0.1%] {+9.94$} | V:0.0% [+0.0%] {+0.00$} | C:0.3% {0.06$}
12|paper-zec          | 2026-07-14 22:12:03: 2026-07-14 22:12:03,062 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | ZECUSDT=536.94 | L:50.1% [+0.1%] {+9.99$} | S:49.9% [-0.2%] {+9.94$} | V:0.0% [+0.0%] {+0.00$} | C:0.0% {0.01$}
18|paper-ldo          | 2026-07-14 22:12:03: 2026-07-14 22:12:03,379 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | LDOUSDT=0.3271 | L:49.8% [-0.2%] {+9.93$} | S:50.1% [+0.1%] {+9.99$} | V:0.0% [+0.0%] {+0.00$} | C:0.1% {0.03$}
19|paper-wld          | 2026-07-14 22:12:04: 2026-07-14 22:12:04,646 INFO: Heartbeat: TPV=19.94 | PnL=-0.06 | WLDUSDT=0.411577 | L:49.1% [-0.9%] {+9.80$} | S:50.8% [+0.8%] {+10.12$} | V:0.0% [+0.0%] {+0.00$} | C:0.1