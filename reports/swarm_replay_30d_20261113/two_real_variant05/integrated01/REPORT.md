# Integrated two-REAL-slot swarm replay

Status: PASS for this completed offline run. No live exchange orders or production changes.

Period: 2026-10-13 21:45:00+00:00 through 2026-11-12 21:45:00+00:00.
Initial modeled REAL account: 7,000 USDT; two initial 1,000-USDT slots and one shared 5,000-USDT reserve.
Initial PAPER pool: 8,000 USDT; dedicated ON/HOT admission universe. Gate 48/36, modeled volume >=15M, MaxDD=99.99, cumulative 15 PAPER cycles.
Commission 4bp, slippage 5bp, funding disabled, immediate synthetic fills at closed-minute prices. Positions remain marked at the final close; hypothetical final liquidation costs are not deducted.

| Ticker | Market fills | Net USDT | Commission USDT | First actual modeled fill UTC |
|---|---:|---:|---:|---|
| HOTUSDT | 16 | 122.564333 | 1.768626 | 2026-11-05T06:20:21.900149822+00:00 |
| ONUSDT | 40 | 686.967543 | 7.281216 | 2026-10-30T21:29:15.600147247+00:00 |

Final account equity: 7809.531875936564935339999998; Net: 809.531875936564935339999998 USDT.
Account MaxDD: 2.054007%.
Both tickers have simultaneously open positions from 2026-11-05T06:20:21.900149822+00:00.
Native supervisor selections: 2725; recorded failures: 0.

Independent verification reconstructs positions, weighted entries, realized PnL, commissions and the single wallet from exchange receipts; checks closed candle timestamps and fill prices; matches final account equity, both strategy shadows and the allocation ledger. All 50 data hashes remain unchanged.
The experimental allocator supports two owners; each executor sees its own positions and funded capital, while the supervisor sees and validates the whole account. Global cash is reconciled to confirmed fills; each owner shadow is verified at its own stable checkpoint, allowing known in-flight cash movements of the other owner.
Production single-owner allocation and process locks are untouched. This is a historical modeled execution result, not certification for live two-owner trading. Earlier variant02/03/04 outputs are not accepted results.

Evidence: summary.json, two_real_verification.json, real_comparison.csv, fills.jsonl, events.jsonl, core/real_allocations.json, equity.csv and daily checkpoints.
