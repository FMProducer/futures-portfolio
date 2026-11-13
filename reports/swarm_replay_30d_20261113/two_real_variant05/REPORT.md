# Completed reproducible ON/HOT swarm with two simulated REAL slots

Final accepted variant: two_real_variant05. Both integrated01 and integrated02
completed and independently verified PASS. two_real_reproducibility.json is PASS
for all nine comparison checks. Earlier variants are not accepted results.

Evaluation: 2026-10-13 21:45 through 2026-11-12 21:45 UTC, 30 days after the
original 48h warmup. Initial modeled REAL wallet 7,000 USDT: two initial
1,000-USDT slots and one shared 5,000-USDT reserve. PAPER has its separate
8,000-USDT virtual pool. Only ON/HOT are admitted by the dedicated target policy.
MaxDD=99.99, closed admission windows 48/36, modeled 24h volume >=15M USDT,
positive cumulative PAPER Net and 15 unique historical PAPER cycles for new
REAL admission. Existing REAL owners follow native retention and risk lifecycle.

| Ticker | Market executions | Net USDT | First modeled REAL execution UTC |
|---|---:|---:|---|
| ONUSDT | 40 | +686.967543 | 2026-10-30 21:29:15.600147 |
| HOTUSDT | 16 | +122.564333 | 2026-11-05 06:20:21.900150 |
| Total | 56 | +809.531876 | |

Final account equity 7,809.531875936565 USDT. Return on the 7,000 account:
11.564741%; account MaxDD 2.054007%. Return on the initial combined strategy
capital of 2,000: 40.476594%; corresponding strategy MaxDD 6.476632%.
Final cash wallet 7,879.149115822432; net unrealized PnL -69.617239885867.
Global initial margin 1,489.215474317436; modeled available cash 6,389.933641504996.
PAPER equity separately ends at 8,803.60853744002; this is not added to REAL equity.

The two tickers have actual overlapping modeled positions from November 5,
06:20:21.900150 UTC. This is verified from exchange receipts, not merely from
the number of registered processes. Commissions are 4bp and slippage is 5bp;
funding is disabled. Equity marks open positions at the final close. Further
hypothetical liquidation fees/slippage are not deducted.

## Execution and accounting

The integrated driver runs native core.main.rebalance_loop for PAPER and both
REAL workers under one virtual clock and native supervisor functions loaded
in isolation. It does not clear the selected REAL target before execution.
Orders execute only against the offline exchange adapter. No live endpoints,
credentials, PM2 or operational account snapshots are used.

An experimental allocator replaces the production single-owner ledger only
inside this process. It issues distinct owner proofs against one common wallet,
checks global available funds, and retains actual capital after a confirmed
flat close instead of injecting money to restore a 1,000 balance. Each worker
receives its own position view and a config view using its funded capital.
The supervisor sees and validates the complete account position set.

Global wallet reconciliation uses all confirmed exchange fills. Each owner
shadow is checked against its fills since its current allocation boundary.
This accepts known cash effects while another owner is still committing its
shadow, while rejecting unexplained cash and inconsistent owner accounting.
At completion all shadows, retired cashflows and the common wallet are also
reconciled together. Eight focused tests pass, including second-owner native
snapshot creation, funded-capital isolation, retirement and known concurrent
cash effects versus unknown money.

## Reproducibility and independent verification

Both runs start with empty histories and wallets, with no state or pure-gate
cache transferred. Exact equality passes for PAPER/REAL receipts, frozen source
hashes, all 50 dataset hashes, equity CSV, selection decisions, PAPER ledger,
REAL allocation ledger, independently reconstructed ticker results and daily
financial checkpoint hashes.

The independent verifier reconstructs weighted entries, positions, realized
PnL, commissions, wallet, margin and final equity directly from the modeled
exchange receipts. It checks that every price uses an already closed candle,
that the synthetic fill matches the specified slippage, that both tickers
actually overlap, and that no final worker is blocked or in flight. PAPER
allocation capital matches the funded strategy state. No blocked returns,
worker errors or selection errors remain in either accepted run.

## Artifacts and reproduction

- integrated01/REPORT.md and integrated02/REPORT.md: individual run reports.
- integrated01/two_real_verification.json and integrated02/two_real_verification.json.
- two_real_reproducibility.json: complete comparison and source hashes.
- integrated01/real_comparison.csv: ticker results.
- Each run retains fills.jsonl, events.jsonl, equity.csv, sessions.json,
  core/real_allocations.json, original gate evidence and financial checkpoints.

From the repository root, use a fresh run name:

```powershell
$env:PYTHONHASHSEED='0'
.venv\Scripts\python.exe -B reports/swarm_replay_30d_20261113/two_real_variant05/replay.py --name integrated03 --minutes 43200
.venv\Scripts\python.exe -B reports/swarm_replay_30d_20261113/verify_two_real.py reports/swarm_replay_30d_20261113/two_real_variant05/integrated03
```

Production source, config, account and processes remain unchanged. The actual
production allocator and account execution lock still enforce a single owner.
This result is a reproducible historical simulation, not a live two-owner
deployment or a guarantee of future profits. Native inherited log labels may
still mention 20M/24h; the frozen adapter and gate evidence apply 15M/36h.
