# PM2 recovery and PAPER observation protocol

## 2026-11-20 — Protocol PM2 recovery and verified PAPER swarm observations

- **Cause and correction:** PM2 CLI status checks could auto-start detached daemons after Windows RPC `EPERM`; inaccessible PM2 was also treated as an absent trading system by the pytest guard. Added existing-daemon RPC-only status, migrated supervisor/dashboard/collector/health callers, and made unknown test/supervisor status fail closed. Installed PM2 6.0.14 received a separately backed-up EPERM auto-start guard; the repository stores its patch, not third-party source.
- **Authorized recovery on November 19:** individually verified and terminated 149 childless orphan `Daemon.js` processes, preserving working daemon PID 14544 and all eight original worker PIDs. Revalidated executable, command line, creation time and descendants immediately before termination. Restored `pm2.pid` from 14468 to verified owner 14544 after backup. Terminated private memory totaled 9,343,774,720 bytes (8.70 GiB). No worker restart, trading action, account reset or ACL change was performed.
- **Verification:** final isolated mock suite: 19 passed, 158 deselected, 10 subtests passed; installed-client VM check PASS. Real medium-integrity CLI and default pytest guard returned explicit EPERM failure without spawning. The working daemon has an elevated token; exact pipe ACL cause was not established. Natural supervisor cycle completed 2026-11-19 23:20:35 Kaliningrad, with no errors. All deployed source/test hashes matched the tested worktree; unrelated modified files were preserved. No full-suite PASS is claimed.
- **Fresh read-only observation:** 2026-11-20 08:06:45 Kaliningrad: eight live PAPER workers, REAL disabled, all strategy-minute checkpoints advanced, state age 30–41 seconds, no observed ERROR/CRITICAL in the checked last-hour logs, no active funding/execution blocks. Active-session Net -262.869588 USDT; active equity 7,890.673560; free budget 140.10868085; total equity 8,030.782241; Net versus initial 8,000 is +30.782241 (+0.384778%). Budget residual zero and wallet/state residuals approximately 1e-13 USDT. These are saved-strategy-price marks, not executable BID/ASK liquidation values.
- **Change since November 19 18:26:** total marked equity -118.647830 USDT. RARE confirmed flat closure at 22:12:04 returned 1,004.37288714, Net +4.37288714; 1,000 was allocated to 1000LUNC at 22:28:08. Telegram remains intentionally disabled. On November 20 the verified PM2 owner and pid file still matched 14544; no extra daemons were found.
- **Evidence and limits:** `docs/PM2_RECOVERY_20261119.md`, `docs/patches/pm2-client-eperm.patch`, `docs/pm2_recovery_verification_20261119.json`; full local manifests/backups under `reports/pm2_recovery_20261119/`. Changes were applied to the primary runtime and original Orca checkout after exact preimage checks. A PM2 update can replace the local installed CLI patch; the repository RPC-only reader remains independent of that patch. Non-elevated control access remains denied; no privilege bypass was attempted.
- **Earlier context:** minute-gap recovery remains documented in the November 16 entry and `docs/STRATEGY_GAP_RECOVERY_20261116.md`; the approved policy preserves positions without retroactive trades. Historical ON/HOT selection investigation located user-approved experimental 48/36 and candidate 48/30, separately from deployed 48/24; it did not deploy that research policy. The newly requested BTW BID/ASK and annual/session backtests have not yet been run at this documentation checkpoint.
- **Obsidian:** `Projects/futures-portfolio-pm2-recovery-and-health-2026-11-20.md`, linked from `Docs/changelog.md`.

## Final observed active sessions

| Ticker | Initial USDT | Session Net USDT | Cycles |
|---|---:|---:|---:|
| AKE | 1042.38343131 | -1.3822459384 | 1 |
| BLESS | 1000 | -1.0833309343 | 1 |
| BREV | 1000 | -1.5505450093 | 1 |
| BTW | 1000 | -197.0235347658 | 12 |
| BULLA | 1111.15971670 | -1.5027606396 | 1 |
| IDOL | 1000 | -18.7375807754 | 3 |
| ON | 1000 | -40.1402375657 | 3 |
| 1000LUNC | 1000 | -1.4493524169 | 1 |

Snapshot: 2026-11-20 08:06:45 Europe/Kaliningrad. SQLite was opened with mode=ro.
