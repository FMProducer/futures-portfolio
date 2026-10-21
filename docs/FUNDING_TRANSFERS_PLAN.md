# Funding and Transfers Implementation Plan

Status: B1 SPECIFICATION ACCEPTED (2026-10-21); implementation not authorized. This is a planning document; no code, config, tests, live operations, or optimization are changed. The target behavior is defined in `docs/FUNDING_TRANSFERS_SPEC.md`.

Baseline checkout: `C:\Python\Prosperous_Bot\futures_portfolio`, HEAD `aa8185d` (`docs: backtest_rebalance.py fix`). The worktree changes are this untracked plan, the untracked specification, and the tracked ZEC plan update; no code, config, tests, or results are changed. Line references target HEAD `aa8185d`; function names remain authoritative if files shift.

## 1. Scope and sequencing

Implement in three separately reviewable stages:

- **B1 (specification accepted; implementation not authorized)** - funding accounting, insufficient-funding close/halt, paper/backtest event replay.
- **B2** - confirmed transfers, capital basis, and PnL corrections.
- **B3** - transfer-neutral performance index and reporting.

Live read-only reconciliation, live transfers, withdrawals, and trailing-stop changes are separate approvals.

Proposed B1 boundary (pending approval): one `portfolio_id`, one ordered scenario journal, and isolated backtest/paper simulation. B1 implements funding and replay only. All transfer accounting and the transfer-vs-funding ordering test remain B2. B1 excludes shared-account allocation (OPEN-3), all transfer accounting (B2; OPEN-4/OPEN-5 unresolved), and live execution/reconciliation (OPEN-7); those items do not block B1 while their code paths and fixtures are absent. B1 fixtures use SAFE=0; PROPOSED-1 is outside B1. Late events are quarantined for reconciliation, not applied to current state. Do not change general Clean Slate semantics; B1 only protects the funding halt and event journal from existing startup/reset paths.

Each stage must include:

- the exact files/functions changed;
- callers affected;
- persisted-state format and backward compatibility;
- tests for the accepted behavior and negative cases;
- no silent rebase, clamp, or double application.

## 2. Accepted decisions

- `CASH` is the internal strategy budget from executed surplus reductions (released margin and realized PnL, net of fees) minus executed deficit purchases. Exchange available balance is not CASH; funding/transfers never create CASH; initial formation is separate.
- Funding is charged to V, not CASH.
- `funding_paid` is a signed cost: `funding_paid += -signed_amount`; positive means paid, negative means received. `accounting_equity` subtracts it, so TPV and accounting residual must agree.
- If funding exceeds V: apply the full debit once, preserve negative V/shortfall, do not use CASH or automatic Spot coverage, block new increases, emergency-close positions, record closure facts and fees, and enter a stable funding halt until operator action. The halt is not a cooldown and is not cleared by restart/clean-slate paths.
- A confirmed Spot-to-Futures top-up increases V, TPV, `initial_capital`, and `external_net_flow`; CASH and absolute PnL do not change; `initial_capital_original` is preserved.
- `capital_basis = initial_capital_original + external_net_flow`; do not subtract the flow twice.
- The performance index issues/redeems units at the pre-transfer unit price, so transfers do not change return or drawdown.
- Backtest/paper use one canonical simulation order: funding (0), fills (1), transfers (2), then valuation; source arrival order is not used across kinds. Confirmed-fact replay preserves source sequence separately and quarantines ambiguous order.
- `nav_to_basis_pct = NAV/capital_basis - 1` is a diagnostic profit-to-basis ratio, not transfer-neutral. `index_return_pct` is the primary performance metric.
- Event application is idempotent: `received` has no effect, `applied` is durable, same ID with a different payload is a conflict, and the event ledger + strategy state + paper wallet share one commit boundary.
- Event atomicity does not make the multi-leg close atomic: commit the halt before closing, persist each leg's actual fill/fee/status, and resume only unfinished reductions.
- A1/A2a behavior and evidence remain unchanged.

Not approved: withdrawals, SAFE-in-PnL reporting change, trailing-stop conversion to the index, shared-account allocation, live integration, parameter optimization.

## 3. Current behavior and discrepancies

### 3.1 Funding

`backtest/backtest_rebalance.py`:

- `BacktestState.pay_funding` at lines 208-214 currently draws `val_cash` first and releases V only for the remainder. This is a CASH-first waterfall and conflicts with the accepted V-only rule.
- `funding_drag_step` at lines 485-487 is a constant positive daily rate; `run_backtest` applies `(long_notional + short_notional) * funding_drag_step` at lines 927-930. This is not signed actual funding.
- `tests/test_backtest_rebalance.py:53-75` explicitly asserts the old CASH-first/negative-CASH behavior. Those tests describe the old technical model, not the target policy.
- `BacktestState.get_tpv` at lines 139-141 already excludes `val_cash` from V, so a V-only debit is structurally possible. The bar-close assert at line 1111 (`current_tpv > 0`) must not fire before the emergency close path handles a negative shortfall.
- Predictive liquidation closes both legs inline at lines 945-983 and hard liquidation wipes state at lines 987-1014. Neither path persists a per-leg close intent or resumable close status, so B1 cannot reuse it unchanged for funding-shortfall recovery.

`backtest/dashboard_attribution.py`:

- Lines 20 and 139-140 reconstruct funding from a positive `funding_rate_per_bar` applied to `pos_long + pos_short`. It ignores signed actual funding, event timestamps, and funding received. It will disagree with the corrected ledger.
- `scripts/visualize_zec_dashboard.py:484-487` passes this constant-rate model to the attribution report.

`core/main.py` and `core/connector.py`:

- No funding event source, event ledger, or funding application exists in the paper/live loop.
- There is no `FUNDING_FEE` handling in `core/`; `core/connector.py` has no funding-history method in the reviewed code.

### 3.2 Transfers and basis

`core/main.py`:

- `initial_tpv` is loaded at lines 468-470 and used as `PortfolioCalculator.initial_capital` at lines 893, 1337, and 1444.
- The config rebase block at lines 621-660 treats any `initial_tpv != target_initial` as a config change and can overwrite a transfer-adjusted basis.
- `_apply_clean_slate` at lines 92-106 resets `paper_state["balance"]`, `initial_tpv`, `reference_tpv`, `tpv_ath`, and `virt_debt`. A top-up followed by a clean start/restart would be lost or treated as a phantom buffer.
- The paper balance path at lines 856-863 computes `real_equity = paper_state["balance"] - virt_debt`; because `paper_state["balance"]` includes V, top-up/funding must update both fields to keep CASH and NAV consistent.
- `emergency_stop` at lines 1566-1689 resets paper balance to the config `initial_capital` when `close_only=False`; that would erase a confirmed top-up. With `close_only=True`, the paper branch only logs positions and does not close or charge fees.
- `_handle_liquidation_guard` at lines 207-283 closes paper positions at `liq_price` but has no commission/slippage charge; it also does not persist a funding-specific halt record.
- `supervisor/swarm_manager.py:298-373` resets state to `last_tpv`/config capital and recreates `initial_tpv`, `reference_tpv`, and `tpv_ath`; it does not preserve `initial_capital_original`, `external_net_flow`, or a funding halt. `start_bot` at line 383 always calls this reset for paper, so B1 must bypass it when a funding halt is present; B2 must preserve confirmed top-ups across the same lifecycle.

`backtest/backtest_rebalance.py`:

- `BacktestState.initial_capital` (line 83) is the only basis; `ledger_base` (line 118) is set at initialization and recomputed during cooldown rebase at lines 548-569. A top-up must increase both the adjusted basis and `ledger_base`, or `check_accounting` will show an X residual.
- `profit_pct` at line 1116 uses the original `initial_capital` local, while state-level basis changes later. The result also returns the original `initial_capital` at line 1200. New basis fields are needed; old fields must remain for compatibility.
- Cooldown rebase resets `initial_capital`, `ledger_base`, `siphoning_reserve`, and `tpv_ath`; it is a separate lifecycle event, not an external flow, and must not be confused with a top-up.
- `tpv_ath` and trailing-stop checks at lines 1077-1107 use absolute TPV. A top-up can change the apparent drawdown; do not silently change this in B2.

### 3.3 PnL, SAFE, and reporting

`core/calculator.py`:

- `total_tpv` at line 70 includes `siphoning_reserve`.
- `total_pnl` at line 73 excludes the reserve (`tpv - initial_capital`), while `core/main.py:1454-1457` sets `last_profit = total_tpv_final - initial_tpv`, which includes the reserve. Dashboard/backtest paths also differ.
- This inconsistency must be resolved only after the SAFE reporting proposal is approved. Until then, label legacy fields and avoid using them for transfer validation.

`core/main.py` also uses `last_profit`, `total_pnl_pct`, `tpv_ath`, and `initial_tpv` in exit metrics (lines 108-136), blacklist rebases (lines 632-649), drawdown stops (lines 967-997), and trailing stops (lines 999-1017). All are consumers that must be audited before changing the basis.

`supervisor/aggregator.py:109`, `supervisor/swarm_analyzer.py:42-44`, `monitoring/send_monitor_report.py:30-40`, `monitoring/health_check.py:66-67`, and `dashboard/data_collector.py:94,132,279` consume `last_profit`/`initial_capital`. They must either keep legacy fields or be updated in the same stage as the reporting change.

### 3.4 Event ownership and idempotency

- There is no integrated funding/transfer event ledger in the trading or paper cycle. `BacktestState.events` and `log_event` are diagnostics, not an idempotency mechanism.
- `accounting/ledger.py:85` is an existing offline SQLite ledger with one transaction for events/register state, unique event keys, payload-conflict detection, out-of-order rejection, and a state/journal consistency check. It is a different domain model, is not connected to the trading loop, and does not contain the paper wallet or strategy state; it is prior art, not a drop-in.
- `BacktestState.events` is bounded only by the fill cap and is not a source of applied IDs.
- No transfer event, pending/confirmed status, or restart-safe processed set exists.
- `docs/ZECUSDT_backtest_fix_plan.md` active rules are updated for the funding/top-up policy; historical sections below the active header are not implementation guidance.

## 4. B1 - funding and funding-shortfall halt

### 4.1 State additions

Add to paper/backtest state (names are proposed; keep one spelling across modes):

```text
funding_model              # signed_actual | legacy_constant
funding_shortfall          # non-negative amount; 0 when no shortfall
halt_reason                # funding_shortfall | null
halt_event_id              # event that caused the halt
halted_at                  # timestamp
event_ledger               # received/applied/quarantined records, payload hash, survives restart
close_state                # close intent(s), per-leg pending|filled|partial|error|skipped|quarantined, fill IDs, fees
```

The event ledger, strategy state, and paper wallet need one atomic commit boundary; separate sequential JSON saves are insufficient. Existing `accounting/ledger.py` is prior art only; map its semantics or add equivalent guarantees for these stores.

Persist the shortfall/halt/increases-blocked state before the first close. The close sequence is intentionally not one atomic transaction: each leg has a stable `close_event_id`, and each actual fill/fee/remaining position is committed before the next action.

Do not overload `trailing_stop_triggered` as the only halt marker. A funding halt must not be cleared by a normal trailing-stop reset, clean-slate path, or ordinary supervisor restart. It has no cooldown timer.

### 4.2 Backtest

Target files/functions:

- `backtest/backtest_rebalance.py`: `BacktestState.pay_funding`, `accounting_equity`, `check_accounting`, `run_backtest`, emergency close path.
- `backtest/dashboard_attribution.py`: replace constant funding reconstruction with ledger/event data where available.
- `tests/test_backtest_rebalance.py`: replace old waterfall assertions with target-policy tests; keep a separately labeled legacy-mode test if needed for accepted baseline reproduction.

Required behavior:

- A paid event reduces `virt_debt` only; `val_cash` remains unchanged.
- A received funding event increases `virt_debt`; `val_cash` remains unchanged.
- `funding_paid += -signed_amount`: positive for paid funding, negative for received funding. `accounting_equity` subtracts `funding_paid` at line 160, so a +3 credit must raise V/TPV and expected equity by 3 while reducing `funding_paid` by 3; check both TPV and the accounting residual.
- If `virt_debt` becomes negative, preserve it and commit the funding event together with `funding_shortfall`, the halt, and increases-blocked state before closing; then close each leg with the simulator (commission/slippage included). Persist each leg's fill/fee/status before the next leg.
- Recovery resumes only legs not durably filled/flat; funding is never reapplied and a filled leg is never re-closed. Halt remains through ERROR/PARTIAL/SKIPPED and after successful closure.
- Log closure as fills/events with `trigger="funding_shortfall"`, record commissions and final state, and keep the stable halt; no cooldown or automatic restart.
- Handle the shortfall before the bar-close `TPV > 0` assert can abort the run.
- `signed_actual` is the target model. `legacy_constant` remains a separately labeled compatibility path only for accepted baseline evidence; it must not be mixed with the new model in one run.

### 4.3 Paper

Target files/functions:

- `core/main.py`: load/persist new state fields near lines 348-363; apply funding in the loop before rebalance; reuse `PortfolioCalculator` only for valuation.
- `core/executor.py`: use existing paper execution/commission logic or add a small reduction-only close helper; `PAPER_COMMISSION_RATE` is at line 16 and paper fills are at lines 387-433.
- `core/main.py`: `_handle_liquidation_guard` and `emergency_stop` need a funding-specific path or a shared close helper; neither current path satisfies the full requirement as-is.
- `core/main.py`: `_update_final_metrics_for_exit` must persist final basis and halt facts, not only legacy PnL.

Required behavior:

- Apply a confirmed funding event once, updating `paper_state["balance"]` and `virt_debt` together; CASH must remain unchanged.
- Allow negative V/shortfall; do not clamp it or let the `real_equity` clamp at lines 862-863 hide it.
- On shortfall, persist the halt and increases-blocked state before any rebalance/close, close each side, charge paper commissions, persist each fill/fee/leg status, and stop the bot. A restart resumes only unfinished legs.
- Do not use `emergency_stop(close_only=True)` as the funding close path: current paper code only logs positions and charges no fee. `_handle_liquidation_guard` paper closes also lack fees/close-state persistence.
- Do not reset paper balance to config capital on the halt path.
- Reject duplicate/replayed event IDs; same ID with a different payload is a conflict. Persist `received`/`applied`/`quarantined` status and the applied set atomically with strategy state and paper wallet.
- Paper has no funding source today; add an explicit journal/scenario input, not an inferred balance delta.

### 4.4 Live

No implementation in B1. Define the read-only mapping to `GET /fapi/v1/income` and `GET /sapi/v1/futures/transfer`, validate stable IDs and status fields, and keep it behind a separate approval. Never place or close orders in this stage.

## 5. B2 - confirmed transfers and capital basis

### 5.1 State additions

```text
initial_capital_original
external_net_flow
capital_basis              # derived and persisted for audit/recovery
spot_balance               # simulation/read-only source; not a CASH alias
transfer_ledger            # or shared event_ledger with transfer records
```

Migration defaults for old saves:

- `initial_capital_original = initial_capital` (or the config basis used at load);
- `external_net_flow = 0`;
- `capital_basis = initial_capital_original`;
- `spot_balance = UNKNOWN` unless the scenario supplies it;
- no transfer events are inferred from balance deltas.

### 5.2 Backtest

- `initial_capital_original` stays fixed.
- A confirmed top-up: `virt_debt += X`, `initial_capital += X`, `ledger_base += X`, `external_net_flow += X`, `capital_basis += X`; `val_cash` unchanged.
- Do not rerun initial build, reset positions, or change the existing target V share.
- Keep `initial_capital` as the adjusted compatibility basis and return both original and adjusted values.
- Do not auto-adjust `tpv_ath` or trailing-stop thresholds in B2. The current absolute-TPV behavior remains a known limitation until OPEN-6 is approved.
- PnL reporting should use the approved basis; do not subtract `external_net_flow` a second time.

### 5.3 Paper/core

Target files/functions:

- `core/main.py`: state load/defaults (348-363), config rebase (621-660), initial build (816-854), paper TPV (856-863), execution balance update (1314-1315), initial residual/VIRTUAL path (1326-1392), SAFE (1401-1479), exit/emergency (1566-1689).
- `core/calculator.py`: accept/receive the approved basis; keep legacy fields until all consumers are migrated.
- `supervisor/swarm_manager.py:298-373`: preserve or explicitly archive external-flow fields on reset.
- `dashboard/data_collector.py`: use the adjusted basis or expose both values; do not silently display a top-up as profit.

Rules:

- Top-up: `paper_state["balance"] += X`, `virt_debt += X`, `initial_tpv += X`, `reference_tpv += X`, `external_net_flow += X`, `capital_basis += X`; CASH and absolute PnL unchanged.
- Preserve `initial_capital_original`; do not let the config-comparison block at lines 650-655 rebase the transfer-adjusted basis back to config.
- Do not trigger initial build, reset positions/pending, create CASH, or clear a confirmed flow.
- A clean start or operator reset must explicitly archive flows; it must not mistake a top-up for a phantom balance.
- `emergency_stop` must not reset paper balance to config capital while a confirmed flow is part of the baseline.
- `spot_balance` is scenario/read-only data. Insufficient Spot behavior is OPEN-5; no partial event.
- Withdrawals remain unimplemented until OPEN-4.

### 5.4 PnL and SAFE

- Use `capital_basis` for absolute PnL and `nav_to_basis_pct`; label the latter as a diagnostic ratio, not a transfer-neutral return.
- Keep `external_net_flow` as an audit component; do not subtract it from adjusted capital.
- SAFE inclusion is PROPOSED-1. If approved, use NAV = TPV + SAFE and keep SAFE out of CASH and basis. If not approved, document the alternative and keep the old fields; do not silently change accepted metrics. B1 fixtures use SAFE=0; PROPOSED-1 is outside B1.
- Reconcile the inconsistency between `PortfolioCalculator.total_pnl` (excludes reserve) and `last_profit`/backtest equity (includes reserve) only under the approved reporting decision.

## 6. B3 - transfer-neutral performance index

### 6.1 State and output additions

State/result fields (proposed):

```text
units_outstanding
unit_price
unit_price_ath
index_start_utc
index_seed_reason
unit_price_curve
index_return_pct
max_dd_pct_index
```

Keep legacy `profit_pct`, `max_dd_pct`, `equity_curve`, `final_equity`, and `last_profit` additive until consumers migrate. Do not overwrite accepted evidence files.

Initial seeding for old state/result data:

- New backtest: seed `unit_price=1.0`, `units=initial NAV` before the first commission, fee, or fill; initial build costs must reduce unit price.
- Existing state: seed at the current snapshot, mark `index_seed_reason=migration`, and do not pretend the pre-upgrade history was transfer-adjusted.
- If historical transfer events are known complete, a reconstruction may be offered separately; it is not required for B3.
- Reject a non-positive seed NAV; this remains tied to OPEN-4.

### 6.2 Calculation

- Use the canonical order from the specification.
- Top-up: issue units at the pre-transfer unit price; NAV and units rise; unit price unchanged.
- Funding, fills, commissions, slippage: change NAV, not units; unit price changes.
- `index_return_pct` = unit-price ratio from index start; drawdown = decline from unit-price high-water mark. Keep `nav_to_basis_pct` as a separately named diagnostic.
- SAFE is included in NAV if PROPOSED-1 is approved.
- Backtest output should include the index curve and summary; paper/live state should persist units and unit-price ATH for restart.

### 6.3 Consumers

Update only after B3 is approved:

- `backtest/backtest_rebalance.py` result metrics and `BacktestState`;
- `backtest/dashboard_attribution.py`, `scripts/visualize_zec_dashboard.py`;
- `backtest/run_all_backtests.py`, `backtest/run_backtest_30d.py`, `scripts/visualize_365d_dashboard.py`;
- `dashboard/data_collector.py` and dashboard views;
- `monitoring/send_monitor_report.py`, `monitoring/health_check.py`;
- `core/main.py` state/reporting fields;
- supervisor/aggregator/analyzer reports.

Do not change the trailing stop or other risk thresholds in B3. Audit and present their use of `initial_capital`, `initial_tpv`, and `tpv_ath` first.

## 7. Test matrix

### B1 funding

| ID | Scenario | Expected |
|---|---|---|
| F1 | Paid funding < V | V and TPV decrease once; CASH unchanged; `funding_paid` diagnostic correct |
| F2 | Paid funding = V | V reaches zero; no CASH fallback; no negative shortfall |
| F3 | Paid funding > V | Full debit applied; V negative; `funding_shortfall` recorded; positions closed with fees; halt persisted |
| F4 | Received funding | V and TPV increase once; CASH unchanged |
| F5 | Duplicate/replayed event | No second accounting effect |
| F6 | Same ID, different payload | Conflict/quarantine; no accounting effect |
| F7 | Crash before commit | Event remains `received`; state unchanged; recovery applies once |
| F8 | Crash after commit | Event is `applied`; retry is a no-op |
| F9 | Late funding event | Quarantined; not applied to current state; halt not cleared |
| F10 | Paper funding | `paper_state["balance"]` and `virt_debt` both change; CASH unchanged |
| F11 | Funding shortfall close | Both legs closed; commissions/slippage recorded; no reopen; increases blocked |
| F12 | Close proceeds or later credit | Halt remains; no automatic restart |
| F13 | Confirmed replay order | Funding-only source sequence preserved; same ordered stream yields same effects |
| F14 | Backtest/paper parity | Same ordered event sequence yields same V/TPV/CASH effects |
| F15 | `funding_paid` sign | Received 3: V/TPV +3, `funding_paid` -3, equity +3, residual unchanged; paid reverses |
| F16 | Crash after first close | Funding not reapplied; closed leg not closed again; remaining leg resumes; halt remains |
| F17 | Second close ERROR/PARTIAL/SKIPPED | Actual fills/fees persisted; halt remains; only unfinished reduction resumes; no increases |
| F18 | Crash before first close | Halt/increases-blocked durable; recovery closes both legs once |
| F19 | Duplicate close retry | Filled leg is a no-op; no duplicate fee/PnL; halt survives success |

### B2 transfers and basis

| ID | Scenario | Expected |
|---|---|---|
| T1 | Confirmed top-up | V/TPV/basis/flow increase; CASH and absolute PnL unchanged |
| T2 | Top-up during drawdown | No initial build, no reset, no CASH creation |
| T3 | Top-up then restart | Flow and adjusted basis survive |
| T4 | Top-up then clean start/operator reset | Explicit archive; no phantom-buffer wipe |
| T5 | Pending/failed transfer | No accounting effect |
| T6 | Duplicate transfer | No second effect |
| T7 | Config rebase after top-up | Does not silently reset the flow-adjusted basis |
| T8 | Old state without new fields | Defaults applied; no transfer inferred; old behavior otherwise unchanged |
| T9 | Insufficient Spot | OPEN-5; no partial credit or minting |
| T10 | SAFE present | Proposed SAFE reporting policy is explicit; CASH/basis unaffected |
| T11 | Transfer/funding order conflict | Canonical funding-first creates shortfall where source transfer-first would not; source stream is non-replayable |

### B3 index

| ID | Scenario | Expected |
|---|---|---|
| I1 | Top-up at current unit price | Unit price and return unchanged |
| I2 | Top-up during drawdown | Index drawdown unchanged |
| I3 | Funding/fees/PnL | Unit price changes by the economic result |
| I4 | Multiple same-timestamp events | Canonical order is reproducible |
| I5 | Old state seeded | Index starts cleanly; migration seed is labeled |
| I6 | New backtest seed | Seeded before first fee/fill; initial expenses reduce unit price |
| I7 | Metric naming | `nav_to_basis_pct` and `index_return_pct` are distinct; the top-up example reports 8.3333% vs 10.0% |
| I8 | Reporting consumers | Legacy fields remain valid; new fields reconcile |

### Regression

| ID | Scenario | Expected |
|---|---|---|
| R1 | A1 initial CASH/pending | Accepted behavior unchanged |
| R2 | A2a collateral guard | Accepted behavior unchanged |
| R3 | P1 post-critical halt | No reopen regression |
| R4 | Funding halt + supervisor restart/clean start | Halt, shortfall, and event journal survive; paper reset does not erase them |
| R5 | Reconciliation quantities | Wallet↔wallet, equity↔equity, SAFE↔SAFE; NAV is not compared to wallet |
| R6 | Accepted baseline evidence | Legacy mode reproduces separately; new policy is not presented as old evidence |

## 8. Compatibility and rollout

- Add fields; do not rename/remove existing JSON keys in B1/B2.
- Old saves may omit all new fields; apply documented defaults.
- Do not auto-generate transfer history from state differences.
- Keep accepted A1/A2a result files untouched.
- Run the exact targeted tests once on the final candidate per stage; do not combine unrelated optimization or live checks.
- No backtest or optimization run is part of this documentation task.

## 9. Remaining approvals

1. PROPOSED-1: include SAFE in reported NAV/absolute PnL/index; outside B1, which uses SAFE=0.
2. OPEN-3: shared-account allocation for funding/transfers; excluded from B1.
3. OPEN-4: withdrawal that exhausts the capital basis; excluded from B1.
4. OPEN-5: insufficient Spot for simulated top-up; excluded from B1.
5. OPEN-6: trailing stop/risk thresholds on index versus absolute TPV.
6. Live read-only source/API validation and separate execution approval; excluded from B1.

B1 is not blocked by items 1-4 while SAFE=0 and the excluded transfer paths are used.

## 10. Stop point

This plan stops at documentation. No implementation begins until B1 is explicitly approved. B2/B3 acceptance criteria are defined, but they remain future work.
