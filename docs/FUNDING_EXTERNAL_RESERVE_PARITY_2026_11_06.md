# External-reserve funding and three-mode replay

Funding is an external-account cashflow. A positive signed payment credits the reserve; a negative signed payment debits it. PAPER uses `paper_account_free_margin`; REAL and the BACKTEST account model use `cash_reserve_usdt` / `account_free_margin`. Funding must not mutate the worker wallet, CASH, VIRTUAL, positions or its expansion-profit ledger.

The `legacy_constant` PAPER helper estimates the minute payment and emits an identified funding event. Both PAPER funding models then use the same idempotent reserve settlement and funding boundary as signed REAL income. BACKTEST uses the same reserve arithmetic. Insufficient reserve records an unpaid liability and enters the existing funding halt without borrowing from worker buckets. Credits do not silently clear an existing halt.

Active portfolio TPV and its return exclude the external reserve. Account profit after funding is reported separately in `portfolio_profit_after_funding`: active portfolio profit minus net funding paid. Funding expenses have not disappeared merely because active TPV is unaffected. REAL shared-wallet reconciliation subtracts cumulative funding paid from the allocation proof exactly once.

Decimal amounts that cannot round-trip exactly through a JSON float are stored as decimal strings. Funding receipts, stale-writer merges and siphon credits preserve that representation. The capital validator accepts finite, nonnegative decimal reserve snapshots.

## Execution consistency

- REAL sizing includes the observed quote displacement and confirmed taker rate; execution CASH admission uses the bid/ask-to-mid ratio instead of assuming execution at mid.
- A confirmed flat REAL restart initializes VIRTUAL like PAPER. A pending initial build is preserved.
- Both modes release the CASH invariant block after actual CASH has recovered.
- Internal VIRTUAL transfers do not require exchange trade-financial acknowledgements and cannot block a following confirmed futures order in the same batch. Unconfirmed exchange fills still block admission.
- Initial residual allocation into VIRTUAL rounds down when the nearest JSON float would exceed the exact available amount. The remaining fraction stays in CASH.

These are shared monetary-cost and lifecycle rules. Target shares, trailing distance, thresholds and stop parameters are unchanged.

## Existing snapshots

Freshly processed funding carries `funding_strategy_accounting=external_reserve_only`. A snapshot with nonzero prior funding but no matching accounting marker is blocked before further funding settlement. The code does not automatically reimburse historical worker losses or invent a new starting balance. Existing running snapshots need explicit accounting reconciliation before using this version.

## Validation boundaries

The annual comparison uses AGTUSDT minute history for 2025-10-20 00:00 through 2026-10-19 23:59 UTC and the frozen config at `reports/agt_external_funding_20261106/config.json`. The independent exchange adapter supplies immediate, fully filled market orders, 0.0005 adverse slippage, 0.0004 USDT commission and 0.001 quantity steps. Synthetic funding is 0.0002 per day on gross open value in all modes. Historical AGT funding rates are not used in this controlled comparison.

Runtime replays use isolated I/O. Applied funding events are archived with bounded hot-journal overlap; native serialization throughput and annual WAL growth are not certified. A simulated REAL result does not certify live fills, latency, crash recovery or production deployment. The main checkout and running swarm are not modified by the replay.

Final artifacts and assertions are reproduced by `python scripts/verify_agt_external_funding_results.py reports/agt_external_funding_20261106`: all minute equity points, exchange fills, exact funding events, reserve conservation, worker-wallet conservation and nonnegative bucket observations are checked. Intermediate or interrupted runs are excluded from the final comparison. The compact committed record is `docs/evidence/agt_external_funding_2026_11_06.json`; large raw histories, curves and receipts remain local under `reports/` and `data/`.

## Session protocol and provenance

- Branch: `codex/agt-annual-runtime-parity`; base `89223439a0e4d84f57dbcdc65d137d2e7bfb7f0d`. The commit contains both the initial annual comparison and the subsequent reserve-only funding implementation requested by the user.
- Config SHA256: `9f192358cb5fdd7522cc264fc0b89147dc9c3cc1bc72c448d9c879b17742deb0`. AGT history SHA256: `c6cf752e9d204a19c1e85ba6b75c5b60d817bf81b7e0cfc8eebc52e75fbc5308`.
- Targets 25/25/50; LONG/SHORT leverage 3/3; initial/paper initial capital 1000 USDT; each simulated account has a separate 5000-USDT reserve. Share-trailing distance stays `0.05800887342949769`. No optimization or live order was performed.
- Original BACKTEST/PAPER: +379.927489%, MaxDD 67.421727%, 386 cycles, 82 stops, 573 fills, TPV 4799.274890958196 USDT, funding 262.94369740647585 USDT. Every equity point and fill matched; equity error at most 3.64e-12 USDT. These figures used the prior funding accounting contract.
- Unchanged REAL refused the first nonzero synthetic payment, bar 1 (2025-10-20 00:01 UTC), because the reserve remainder could not round-trip through float. The payment was `-0.0002059397495034722222222222222` USDT. Decimal-string persistence removed the refusal without rounding the payment.
- REAL after that storage fix, before reserve-only accounting: +542.946549%, MaxDD 67.547623%, 441 cycles, 86 stops, 579 fills, TPV 6429.4654898268445 USDT, funding 284.35918786160624 USDT. The completed simulation still differed from BACKTEST/PAPER; higher return did not qualify it as parity.
- Confirmed earlier CASH incident: 2026-03-29 16:44 UTC CASH -0.19566239238526667 USDT; by 2026-04-01 17:20 UTC CASH +45.08383581657893, but REAL retained the additions block because recovery was PAPER-only. Initial SHORT also differed: PAPER 147516.020 versus REAL 147442.263 at the same modeled fill price. Both root causes are fixed in this change set.
- After external-only funding and the sizing/restart fixes, the first long REAL run returned +420.622295%, MaxDD 67.382049%, 392 cycles, 85 stops, 588 fills, funding 274.0999168817106 USDT and TPV 5206.222947387493 USDT. Its minimum CASH was -5.35056008667e-13 USDT. This was explicitly rejected, not rounded into a PASS.
- Its first different fill was index 191, bar 246880: PAPER bought LONG 8039.128 at 0.0146623275; REAL bought 9259.199 four bars later at 0.014605299. Prior equity error was at most 1.14e-12 USDT. A 246901-bar diagnostic prefix reproduced the issue (PAPER 139 cycles versus REAL 140; both 193 fills). The internal-transfer financial-confirmation bug explained the skipped order; dedicated mixed-batch tests verify the fix while retaining exchange-financial protection.
- The first 7000 bars, including a stop and restart, were separately checked. Initial allocation exposed a -1.66666666667e-14-USDT rounding fraction; flooring the allocation left +1.66666666667e-14 in CASH without inventing funds. The final annual runs subsequently observed no negative buckets.
- A replay-clock defect advanced historical time on exchange confirmation sleeps. That long run was interrupted and excluded. A later diagnostic CASH-block log was initially treated as a fatal harness error; this was corrected without disabling the underlying trade protection. Neither interrupted run is reported as an annual PASS.
- Final full suite: 1170 passed, 0 failed, 9 warnings in 138.21 seconds, exit 0. Native versus archived journal tests cover a short run and confirmed stop/restart; uncertainty, duplicate/conflict, partial-close and crash-recovery protections remain tested. Documentation and compact-evidence packaging followed the validated financial source; all recorded Python source hashes were rechecked before packaging.
- No main checkout modification, swarm restart, broker order, production snapshot migration or push occurred. Existing nonzero legacy-funded snapshots are deliberately blocked until reconciled. The simulation's bounded hot-journal I/O is not proof of native annual JSON/WAL scalability.

## Verified annual result

| Mode | Trading return | Trading MaxDD | Cycles | Stops | Exchange fills |
|---|---:|---:|---:|---:|---:|
| BACKTEST | +421.26% | 67.38% | 391 | 85 | 588 |
| PAPER | +421.26% | 67.38% | 391 | 85 | 588 |
| SIMULATED REAL | +421.26% | 67.38% | 391 | 85 | 588 |

Funding paid from the external reserve: 274.35420149 USDT; remaining reserve: 4725.64579851 USDT. Profit after external funding: 3938.20983382 USDT (+393.82% of the initial 1000 USDT trading capital). All 514100 funding events and 588 exchange fills matched. Observed CASH and VIRTUAL were nonnegative. See `reports/agt_external_funding_20261106/verification.json` for tolerances, hashes and conservation residuals.
