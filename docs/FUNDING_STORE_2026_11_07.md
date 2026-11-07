# Funding receipt storage

Funding event identities now live in `<config path>.funding.sqlite`, using the standard-library SQLite driver and FULL synchronous transactions. No new dependency or trading parameter is introduced. The config retains compatible scalar reserve balances and bounded `funding_reserve_totals` counters; it no longer contains a growing `funding_reserve_receipts` map. Reserve balances remain a JSON projection for existing supervisor, siphon, allocation and collateral consumers.

Each settlement holds the existing config lock, records its identity and a pending projection in one SQLite transaction, then atomically updates JSON. A failed JSON write leaves the pending record intact. The next settlement or locked config writer restores that projection before proceeding. Duplicate identities verify amount, reserve key and event time; they do not debit again. A conflicting projection blocks reconciliation rather than guessing a balance. Missing receipt databases for migrated configs block settlement.

Legacy receipts are validated and imported transactionally before removal from JSON. Their debits are already included in the reserve and are never reapplied. An interrupted migration can import the same identities again safely. Stale config writers merge cumulative deltas rather than rebuilding the full journal; siphon-funded reserve checks accept the same bounded counters. No receipts are pruned.

## Validation

- Shared-account concurrency, duplicate/conflicting identities, both reserve keys, positive and negative funding, stale writers and siphon preservation.
- Failed migration/projection writes after SQLite commit, repeated recovery, retained bot funding WAL, and missing-database refusal.
- 100 receipts increase SQLite history while JSON size changes by less than 32 bytes.
- Isolated working-config migration: 6663 receipts, 2583732 bytes to 3198 bytes; both reserve balances unchanged, duplicate event replay unchanged. Source snapshot SHA256: `b90bcc671ab66c1afa42b14abf0474057d1b24df61a96ddfd6078d242e1c5312`.
- AGTUSDT, 2025-10-20 through 2026-10-19, 525600 minutes, frozen `reports/agt_external_funding_20261106/config.json`: the complete native backtest result equals the verified baseline. Active TPV return +421.25640353%, MaxDD 67.38204877%, final TPV 5212.564035311434 USDT, funding 274.35420149127003 USDT. This storage change does not rerun annual PAPER/REAL; their isolated short runtime comparisons exercise the real SQLite store.
- First full suite: 1182 passed, one replay-adapter failure; the obsolete in-memory config-write hook was replaced with an isolated real SQLite adapter. The final suite result is recorded in CHANGELOG.md.
- Final full suite: 1183 passed, 9 warnings, 137.51 seconds, exit 0. Targeted store/runtime replay tests: 12 passed; prior reserve/siphon/storage paths: 117 passed. Final `git diff --check` passed.

## Deployment boundary

No running worker, production config, or live state was modified. Upgrade all writers together; old binaries must not continue writing legacy receipt maps. Back up the config and bot journals before deployment. The first settlement migrates the existing map automatically. Config and its SQLite sidecar form one recovery/backup set: do not delete the database, restore either file independently, or reset totals. The database and migration must stay on a local disk with working SQLite locking; live deployment acceptance remains separate from offline validation. The two compatible reserve projections still cause bounded config writes; this change removes unbounded history rewrites, not every JSON write.
