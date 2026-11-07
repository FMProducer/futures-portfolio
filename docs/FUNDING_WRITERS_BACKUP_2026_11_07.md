# Funding writer and backup follow-up

Branch: `codex/funding-journal-storage`, based on `5014857`. The main checkout was observed at `7a7647b`, before the SQLite change; no production deployment or worker restart is part of this verification.

## Proven gaps and fixes

- Restoring old JSON alongside a newer database was previously accepted. A repro returned reserve 4997 instead of the already-booked 4993. A transactional SQLite checkpoint now binds the config projection to the database. Different snapshots, including net-zero sequences with identical reserve totals, fail before new settlements or config writes. Existing pre-checkpoint databases are reconciled against exact cumulative receipts before checkpoint initialization.
- Missing databases previously blocked funding settlement but not ordinary config writers. All locked writers now reject a migrated config with a missing sidecar without changing JSON.
- A separate-process test exposed a stale config writer overwriting Siphon credits while preserving funding deltas. Shared storage now merges immutable Siphon identities and their reserve credits, rejecting changed receipt amounts. Four processes concurrently exercise two duplicate funding producers, a stale config writer and actual `siphoning.credit_reserve` calls. Eight debits and eight credits leave the expected reserve and receipt counts.

## Writer inventory

| Producer | Entry point | Shared protection |
|---|---|---|
| PAPER/REAL funding and late REAL handover | `core.funding.settle_funding_reserve` | config FileLock, SQLite transaction, pending projection, checkpoint |
| Supervisor config/blacklist/swarm updates and Siphon | `safe_save_json` / `safe_save_json_sync` | same lock, projection recovery, cumulative funding merge and immutable Siphon merge |
| Atomic read-modify-write config callers | `update_json_sync` | same lock and projection recovery before updater |
| Funding config projection/migration | private `_atomic_save_json` inside funding store | caller holds the same lock; SQLite checkpoint validated |
| Dashboard | reads trading config/state | no trading-config writer found |
| Paired snapshot | `scripts/backup_funding_config.py` | same lock, pending recovery, SQLite backup API, matching checkpoint, SHA256 |

Old loaded binaries do not acquire new behavior merely because source files have changed. All production writers must be stopped/upgraded/restarted as one deployment; this audit does not claim the running swarm has already been updated.

## Paired backup

After the new ledger has been initialized, create a fresh directory:

```powershell
python scripts/backup_funding_config.py backup C:\Backups\futures-portfolio-20261107 --config config.json
python scripts/backup_funding_config.py verify C:\Backups\futures-portfolio-20261107
```

The bundle contains `config.json`, `config.json.funding.sqlite` and `manifest.json`. The manifest is written last. An incomplete or modified bundle is rejected. SQLite backup API captures committed WAL contents; copying only the main `.sqlite` file while writers are active is not a backup procedure. Existing destination directories are refused.

Restore only a verified bundle with all destination writers stopped: copy both files to the destination config path and its `<config path>.funding.sqlite` sidecar. Do not combine snapshots or restore one file independently. Local tests restore into isolated directories and verify repeated funding remains idempotent. No production restore was performed.

A matched pair is not a whole-account rollback: reconcile the bot snapshots/funding intents and, for REAL, actual exchange balances and income before resuming from a backup. Restoring files does not reverse exchange events.

## Annual replay boundary

Both annual modes use the same frozen AGTUSDT config and 525600-minute history as the accepted BACKTEST comparison. Synthetic funding remains identical across modes. Native disk SQLite and memory SQLite replay adapters match in a short replay including funding and private exchange fill facts. Annual accounting comparisons use SQLite `:memory:` with actual ledger SQL and monetary logic; config projection I/O is isolated. Disk retries, pending recovery, cross-process writers and backup durability are separately tested on disk. This is not a certification of annual native JSON/WAL I/O throughput or live exchange execution.

Preliminary annual runs were interrupted or superseded when additional writer gaps were fixed and when disk-only execution proved too slow. They are excluded from PASS results. Accepted candidate hashes and final artifacts live under `reports/funding_store_annual_accepted_20261107/`.

Disk/writer regression on the accepted candidate: **1193 passed, 9 warnings, 167.92 seconds**, exit 0 (`full_tests.log`). Focused final store, backup, storage and runtime-adapter checks: **32 passed**, exit 0 (`reports/funding_store_annual_20261107/final_targeted.log`).

Fresh production-config copy, isolated from the swarm: **7111 legacy receipts**, **2758436 bytes to 3267 bytes** after migration. The paired snapshot passed verification, was restored into another isolated directory and replayed the same booked event twice. Both reserves stayed exactly unchanged and all 7111 receipt identities survived. Source snapshot SHA256: `2b3ea5a5007ca8571600e1d83442f0ce9d62132adfaac3acf3fcbb5799d2b5f7` (`migration_backup_verification.json`).

## Accepted annual results

PAPER and simulated REAL each completed 525600 AGTUSDT minutes, 2025-10-20 through 2026-10-19 UTC, exit 0. Both use targets 25/25/50 and the unchanged frozen config SHA256 `9f192358cb5fdd7522cc264fc0b89147dc9c3cc1bc72c448d9c879b17742deb0`; history SHA256 `c6cf752e9d204a19c1e85ba6b75c5b60d817bf81b7e0cfc8eebc52e75fbc5308`.

| Mode | Trading TPV return | Trading MaxDD | Cycles | Stops | Exchange fills |
|---|---:|---:|---:|---:|---:|
| Verified BACKTEST reference | +421.26% | 67.38% | 391 | 85 | 588 |
| New PAPER | +421.26% | 67.38% | 391 | 85 | 588 |
| New simulated REAL | +421.26% | 67.38% | 391 | 85 | 588 |

The aggregate verifier passed: all 588 fills and 514100 funding events match; all 525600 equity points match within 8.19e-12 USDT for PAPER and 4.55e-12 USDT for REAL. Independent REAL worker-wallet residual is 3.248400000012e-12 USDT. Observed CASH and VIRTUAL are nonnegative, with zero REAL cash-guard block messages. Funding paid is 274.35420149127003 USDT; external reserve remains 4725.64579850873 USDT. Profit after that external funding expense is +393.82098338% of initial trading capital. Trading MaxDD is not a funding-adjusted total-account MaxDD.

Runtime source hashes were checked unchanged after completion. `verification.json` records the base HEAD; the tested working-tree sources are identified by their hashes, not misrepresented as the unmodified base commit. Compact evidence: `docs/evidence/funding_writers_backup_2026_11_07.json`. No deployment, production snapshot restore or live exchange order was performed.
