# PAPER / REAL parity with 01e4df4

Status: 514 targeted offline tests passed after implementing the accepted single-REAL/shared-reserve contract. PAPER annual parity was verified again on the final financial paths and 1 REAL / 8 PAPER configuration. Actual exchange acceptance remains open. Not a live-trading approval.

## Scope and boundary

- Branch: `codex/paper-real-backtest-parity`, based on `01e4df480be4dcfaf06d6d9625866b889bb7c19e`.
- The reference `backtest/backtest_rebalance.py`, calculator and financial parameters remain unchanged. Only slot capacity changes: `max_bots=9`, `paper_mode_bots=8`, leaving exactly one REAL slot.
- Strategy decisions follow the reference minute-bar model. REAL uses confirmed exchange fills, commissions and signed funding; it never books the synthetic legacy funding charge.
- The running main checkout, operational configuration, PAPER budget and PM2 processes have not been changed.
- The selected trading/risk fields of the working config match the control (`working_config_comparison.json`); this does not prove that already-running workers loaded this branch.
- No commit, push, exchange order, account-settings change or worker restart was executed.

## Implemented changes

1. Closed-minute clock, contiguous-history validation and duplicate-bar suppression. A missed bar with exposure requires reconciliation. A new flat session may resume across its flat interval only after checking its reset state and, for REAL, current exchange positions.
2. Guard ordering and lookback windows match the reference. Guards filter ordinary actions without skipping funding or protective accounting.
3. PAPER uses the reference 0.0005 directional execution slippage, 0.0004 commissions and once-per-bar legacy funding. Funding receipt boundaries remain durable.
4. Existing current-CASH sizing is retained; executor funding and execution costs use the same model. Unnecessary four-decimal wallet and eight-decimal entry truncation was removed.
5. MaxDD uses realized wallet equity and its realized high watermark. A limit >=100 disables it, matching the oracle. Equity trailing uses active TPV, resets the violation timer at a new high and uses minute-quantized timeout.
6. Terminal REAL closes persist intent before submission and book only confirmed facts. Unknown, failed and partial outcomes remain blocked for reconciliation. Full-close quantity is passed explicitly to the actual executor path.
7. Equity trailing now closes once and commits final accounting and cooldown together. MaxDD and predictive-stop lifecycle uses the reference cooldown; confirmed strategy exits no longer permanently blacklist a losing session.
8. Supervisor restart uses the confirmed remaining active capital, including profits or losses, without automatic replenishment. Reset does not release a fabricated wallet into the PAPER pool and uses the existing two-file WAL. Market clock and velocity cooldown survive session rollover.
9. REAL launch requires a matching funding snapshot pair. Hedge, leverage or margin setup failure refuses trading. Unexpected exchange quantities block the strategy without silently rewriting wallet facts.

## Evidence so far

Artifacts are in `reports/parity_reference_01e4df4/` in this checkout.

| Run | BACKTEST | PAPER replay |
|---|---:|---:|
| First 6000 ZECUSDT minutes, final TPV | 1020.1832316063836 | 1020.1832316063836 |
| Funding paid | 1.2238292821083334 | 1.2238292821083334 |
| Cycles / exchange fills / stops | 9 / 16 / 2 | 9 / 16 / 2 |
| Remaining LONG / SHORT | 2.983 / 2.976 | 2.983 / 2.976 |

The complete reference year, 2025-10-20 00:00 through 2026-10-19 23:59 UTC, contains 525600 minutes. With the committed config and the current local file it produced Net +89.17732964795633%, MaxDD 80.9689511062944%, 236 cycles and reconciled accounting. Do not substitute earlier conversation metrics from another config for this result.

The first complete PAPER replay was interrupted because Mock call history consumed excessive memory. It is not PASS. The plain-function adapter completed the year with 236 cycles but one extra exchange fill: Net 89.17273073610575%, MaxDD 81.01413131032749%, versus the reference 89.17732964795633% / 80.9689511062944%. This run is a failed parity check, not acceptance.

The first differing equity point was bar 26950: reference SHORT addition 0.041, runtime 0.056. The preceding reference VIRTUAL designation at bar 26923 was 6.346166904332388 USDT. Runtime had wrongly applied the global/exchange 7-USDT floor to the calculator and internal transfer, whereas the reference calculator uses its native 6-USDT floor and exchange minima apply to futures executions. This left extra CASH and altered the later addition. The runtime now uses the same calculator floor, preserves internal transfers below the exchange minimum, and passes the actual configured/exchange floor through to the futures executor. A regression with 6.35 USDT available CASH and a 7-USDT exchange minimum passes.

The corrected complete replay (`paper_verified_525600.json`) reproduces 236 cycles, 337 exchange fills, 46 stops, funding 187.0365173283611 USDT, final TPV 1891.7732964814545, Net 89.17732964814545% and MaxDD 80.9689511063661%. Final comparison (`comparison_verified.json`, source hashes in `manifest_verified.json`) checked every minute and every exchange fill: no differing bar/side/quantity/price, maximum absolute equity difference 1.8189894035458565e-12 USDT. The tiny reported-percentage difference comes from the reference's epsilon denominators.

Final targeted acceptance: **441 passed in 28.53 seconds**, exit 0, `parity-final-tests.log`. This includes 15 runtime integration cases, REAL funding without a synthetic debit, confirmed closes at actual prices and fees different from PAPER, MaxDD and equity trailing, missing one/both legs, account margin warning/critical stop, exact quantities, settings refusal, two-file WAL crash recovery, duplicate receipts, CASH/minimum-transfer regression, session rollover, PAPER budget and supervisor cooldown. Legacy main fixtures were updated to use the same actual-file scenarios rather than bypass required snapshots or expect fabricated Clean Slate balances. No failures were hidden with xfail or skipped to obtain this result. This is a targeted suite, not a claim about every repository test.

Reproduce the offline PAPER replay with:

```powershell
python scripts/verify_runtime_parity.py config.json C:\Python\Prosperous_Bot\futures_portfolio\data\ZECUSDT_live.feather 525600 reports/parity_reference_01e4df4/paper_verified_525600.json
```

## Outstanding acceptance

- The replay replaces network and persistence with isolated adapters; separate passing tests exercise real temporary-file WAL recovery. It does not certify wall-clock exchange operation.
- Verify non-default enabled-siphon behavior and shared-account reserve reconciliation. A passing zero-siphon year does not cover these branches.
- Confirm an actual REAL opening snapshot, wallet allocation, funding boundary, account settings and exchange filters. No `real_state_*.json` was found in the main checkout during this work. No live acceptance or exchange test order was performed.

- Review ambiguous-close recovery operationally: automatic resubmission is deliberately refused; an operator must reconcile unknown outcomes before clearing the block.

Do not deploy this branch or describe it as fully production-ready while these acceptance items remain open.

## Accepted shared-account deployment contract (2026-11-05)

The user confirmed one common 5000-USDT margin reserve, a separate 1000-USDT worker allocation from `initial_capital`, exclusive REAL ticker ownership and an initial capacity of one REAL worker plus eight PAPER slots. No live deployment is included in this implementation.

`core/real_allocation.py` records observed USDT wallet/available funds and an exclusive durable allocation in `core/real_budget.sqlite`. For a confirmed 6000-USDT account, worker accounting starts at 1000, VIRTUAL at 500 and the common 5000 reserve stays outside worker TPV. The preparation refuses existing positions/orders, insufficient funds, changing snapshots, missing/corrupt existing state and a competing owner. It performs no exchange transfers or orders and does not touch the PAPER budget. A native cross-process lock prevents workers/manual closers using the same credentials from running concurrently across checkouts.

REAL account reconciliation compares actual wallet changes against confirmed worker fills, fees, funding and retained reserve offsets. Unexplained credits/debits and foreign positions block trading; no balancing PnL is fabricated. Confirmed terminal owners retain their allocation until late funding and final account balances reconcile. Handover archives the closed owner before allocating the next ticker. Ambiguous closure or funding retains ownership. After confirmed session reset, SAFE is carried as an external account offset rather than credited again to the new worker wallet.

Supervisor rotation now stops the old process before the confirmed close child, awaits its successful exit, and closes each stray ticker once rather than once per hedge leg. Failed close propagates and blocks replacement. PAPER selection still caps at eight, independently of total nine slots.

Final targeted command: the same 30 test modules listed in `parity-final-tests.log`, plus `tests/test_real_allocation.py`, `tests/test_shared_real_runtime.py`, and `tests/test_connector.py`. Result: **514 passed in 33.17s**, exit 0; `parity-shared-final-tests.log`. Covers 6000/1000/5000 allocation, concurrent ownership, actual funding handover/quarantine, process exclusion, worker stop/close ordering, failed-close retention, SAFE offsets and actual REAL runtime fills with unexplained cashflow rejection. These are offline/mock-exchange tests, not evidence of an executed exchange order.

Final annual repeat: `paper_shared_525600.json`, exit 0, 525600 minutes. Net **89.17732964814545%**, MaxDD **80.9689511063661%**, 236 cycles, 337 exchange fills, 46 stops. All minute equity points and all fill facts are identical to the previously verified PAPER replay; maximum equity difference from BACKTEST is 1.8189894035458565e-12 USDT. `comparison_shared.json` and `manifest_shared.json` record this check and final source/config hashes. The supervisor consistency cleanup was finalized while the replay ran; the replay extracts only `reset_bot_state_files`, whose source was unchanged during the run. Its final close/rotation paths were tested separately in the 514-test suite. The sole configuration difference from the committed control is max_bots 8 -> 9; all financial fields and both oracle source files were checked unchanged.
