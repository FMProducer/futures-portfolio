# Funding and Transfers Specification

Status: B1 SPECIFICATION ACCEPTED (2026-10-21). Implementation is not authorized. This document defines target behavior only and grants no implementation, live-integration, transfer, or execution permission. Accepted A1/A2a behavior is unchanged.

Last updated: 2026-10-21.

## 1. Scope

In scope:

- accounting for funding and confirmed Spot/Futures transfers in backtest, paper, and read-only live reconciliation;
- one-time event application, restart safety, and duplicate suppression;
- capital basis, PnL, and a transfer-neutral performance index;
- insufficient-funding handling and the emergency close/halt path.

Out of scope:

- real transfers, deposits, withdrawals, or order execution;
- live trading enablement, A2b, Clean Slate, and parameter optimization;
- changing accepted A1/A2a baselines or their evidence;
- shared-account allocation rules that are not explicitly approved.

### 1.1 B1 boundary (specification accepted; implementation not authorized)

B1 is limited to one `portfolio_id`, one ordered scenario journal, and an isolated backtest/paper simulation. B1 implements funding and replay only. All transfer accounting (top-ups, withdrawals, and transfer-vs-funding ordering) remains B2. B1 excludes shared-account allocation (OPEN-3), all transfer accounting (B2; OPEN-4/OPEN-5 unresolved), and live execution/reconciliation (OPEN-7). Those open items do not block B1 while the excluded paths are absent from the fixtures and code paths.

B1 test fixtures use SAFE=0 as an explicit constraint. SAFE inclusion in NAV/PnL/index remains PROPOSED-1 and is outside B1. Do not leave SAFE included in some metrics and excluded from others.

B1 accepts only ordered events. A late event is quarantined for reconciliation, not applied to current state (section 10). Do not change general Clean Slate semantics; B1 only protects the funding halt and event journal from the existing startup/reset paths. This boundary is accepted as the B1 specification; no implementation is authorized until separately approved.

## 2. Definitions and invariants

Definitions:

- `CASH` - internal strategy budget formed by executed surplus reductions (released margin and realized PnL, net of fees) minus executed deficit purchases. The exchange available balance is not CASH; funding and transfers never create CASH; initial formation is accounted separately.
- `V` - the VIRTUAL USDT bucket (`virt_debt` in backtest).
- `TPV` - `CASH + long_margin + long_upnl + short_margin + short_upnl + V`.
- `TPV_total` - `TPV + siphoning_reserve` (SAFE); the reserve is always kept separate from CASH.
- `NAV` - reported net asset value used for PnL/index. Its SAFE treatment is PROPOSED-1; do not silently change legacy reporting.
- `Spot` - simulated or read-only Spot USDT reservoir outside the Futures strategy.
- `initial_capital_original` - the original capital basis before any external flow.
- `initial_capital` - the adjusted compatibility basis. It changes on a confirmed top-up and must not be confused with the immutable config value.
- `external_net_flow` - cumulative confirmed Spot-to-Futures transfers minus approved Futures-to-Spot transfers.
- `capital_basis` - `initial_capital_original + external_net_flow`.
- `signed_amount` - positive means credit to the Futures account; negative means debit.

Invariants:

- A funding event changes `TPV` exactly once by its signed amount and changes CASH never.
- A confirmed Spot-to-Futures transfer changes `TPV`, `V`, `initial_capital`, and `external_net_flow`; it never changes CASH.
- A transfer is an external flow, not PnL.
- `initial_capital` and `external_net_flow` must not both be subtracted from NAV. `external_net_flow` is an audit component of the adjusted basis, not a second deduction.
- SAFE must not be counted as CASH or as a new external flow. Whether SAFE is included in reported NAV/PnL/index is PROPOSED-1 and requires confirmation.
- A duplicate event is a no-op; the same `event_id` with a different payload is a conflict and has no accounting effect.
- A pending or failed transfer is not an accounting event.
- `received` means no state effect yet; `applied` means the effect and its state are durable; `quarantined` means no accounting effect.
- Wallet reconciliation observes events; it never posts them a second time.
- Funding shortfalls are not hidden by clamping V, CASH, or NAV.
- A funding shortfall must not create new risk. It blocks increases and triggers the existing emergency close path.

## 3. Canonical event order

Two contexts are separate and must not be mixed.

### 3.1 Simulation order (backtest, paper, scenario fixtures)

Use one deterministic order, independent of fixture insertion order:

1. Validate the event and append it to the ledger with a unique `event_id` and `status=received`.
2. Sort events by `(event_time, kind_rank, source_sequence, event_id)`, with `kind_rank`: `FUNDING_FEE=0`, `FILL=1`, `TRANSFER=2`. Source arrival order is not used across kinds.
3. Apply funding, fills, and then transfers in that order. A funding debit is never covered by a transfer with the same timestamp.
4. Compute `NAV` and the pre-transfer unit price after funding and fills.
5. Apply confirmed external transfers at that unit price. A transfer itself does not change the unit price.
6. Commit the event ledger (including `status=applied`) and all affected state in one atomic boundary.
7. Write the timestamp snapshot after all events at that timestamp.

This is the only order used for backtest/paper parity. The ordering version is recorded with the run.

### 3.2 Replay of confirmed facts

Live/recovery replay preserves the confirmed source sequence and event IDs. It does not re-sort confirmed facts into the simulation order. If the source sequence for a timestamp is missing, ambiguous, or inconsistent, quarantine the affected stream instead of guessing. Replay requires a state snapshot at or before the first replayed event; applying a historical fact to current state is forbidden.

B1 replay is limited to a funding-only ordered scenario journal. Live read-only replay remains design-only. Once transfer accounting is in scope, a source-order transfer-before-funding stream may produce a different result from the simulation order; mark such a stream non-replayable rather than silently rewriting it.

After transfer accounting enters scope in B2, a test must cover a timestamp where source order would avoid a funding shortfall but the canonical simulation order creates one.

## 4. Funding

### 4.1 Signed amount

For simulation with funding rate `r` and mark price `p` at funding time:

```text
long_funding  = -q_long  * p * r
short_funding = +q_short * p * r
funding       = long_funding + short_funding
```

A positive rate means LONG pays and SHORT receives for the same notional. Both legs are calculated at the funding timestamp and then netted. Do not add a separate positive funding drag to the same run.

Live funding uses the signed amount reported by the exchange. It is not reconstructed from a locally assumed rate.

### 4.2 Sufficient V

Effect when the resulting V remains non-negative:

```text
V += signed_amount
TPV += signed_amount
CASH unchanged
initial_capital unchanged
external_net_flow unchanged
```

Paid funding (`signed_amount < 0`) reduces V/TPV; received funding (`signed_amount > 0`) increases V/TPV. CASH is never topped up from funding.

`funding_paid` is the signed cumulative funding cost and uses the opposite sign:

```text
funding_paid += -signed_amount
```

It is positive for paid funding and negative for received funding. `accounting_equity` subtracts `funding_paid`, so a received amount of 3 increases V/TPV and expected equity by 3 while decreasing `funding_paid` by 3. A paid amount of 3 does the reverse. Reconcile both V/TPV and the accounting residual; `funding_paid` and `funding_from_virtual` are not a second PnL deduction.

### 4.3 Funding exceeds V

This is an accepted policy, not a proposal:

1. Apply the full signed funding debit exactly once. In the same durable commit record `funding_shortfall`, `halt_reason=funding_shortfall`, `halt_event_id`, and the block on all non-reduction actions. No close starts before this commit.
2. Allow V to become negative and preserve the negative V/shortfall. Do not clamp V to zero, do not silently move the shortfall to CASH, and do not hide it in diagnostics.
3. Do not automatically transfer from Spot to cover the shortfall.
4. Close open positions through the emergency close path, using the existing commission and slippage model. Persist each actual fill, fee, and per-leg close status before proceeding; recovery resumes only unfinished reductions (section 4.5). The block covers initial-build continuation, new positions, and V designation.
5. Keep the stable funding halt after the closes. This is not a cooldown: no timer, retry, next-cycle reset, clean-slate path, or ordinary restart may clear it. Only explicit operator action may change the halt, and that action must be recorded.

The event must still be recorded as applied. It is not acceptable to quarantine the event and omit its accounting effect.

If the emergency close changes the final balance, record both the pre-close shortfall and the post-close result. A later funding credit, same-timestamp transfer, or close proceeds do not clear the halt automatically. A same-timestamp transfer is still applied at its canonical position, but it cannot undo the emergency close.

Backtest and paper must use the same order. The old rule "if there is insufficient funding, do not account for it" is forbidden.

### 4.4 Paper mapping

In paper, the shadow wallet balance already includes V. A funding debit must therefore update both the shadow wallet and V by the same signed amount:

```text
paper_state["balance"] += signed_amount
virt_debt               += signed_amount
CASH unchanged
TPV_total changes by signed_amount
```

A credit applies the same rule in the opposite direction. Updating only `virt_debt` would reclassify cash without reducing NAV and is incorrect. The existing `real_equity` clamp must not mask a negative V/shortfall.

For backtest, `val_cash` is already separate from `virt_debt`; only `virt_debt` receives the funding amount, and `val_cash` stays unchanged.

### 4.5 Emergency-close recovery

Funding application is one commit; the multi-leg close is a separate workflow and is not atomic as a whole. Per-event atomicity must never be mistaken for atomicity of the entire close sequence.

Required protocol:

1. Commit the funding application together with the shortfall, stable halt, and increases-blocked flag before the first close action.
2. Before submitting a leg reduction, persist a durable close intent with a stable `close_event_id`. Allow at most one active intent per leg.
3. After each actual execution, persist the fill, commission, realized result, remaining position, and that leg's close status before starting the next leg. A filled leg must never be submitted again.
4. On ERROR, PARTIAL, or SKIPPED, persist the reported facts and leg status; keep the halt. Only the unfinished reduction may resume. Do not reset the leg to pending by guesswork after a crash; reconcile the order/position first.
5. A close is complete only when every leg is durably confirmed flat. Persisting the first leg as flat is not completion of the sequence.
6. After restart, never reapply funding, never re-close a filled leg, and resume only legs whose durable status is not filled/flat. The halt remains.
7. Partial fills persist the filled quantity and fee; only the remainder may be retried. An unknown outcome becomes `quarantined_reconciliation`, not a retry with a new ID.
8. A successful close does not clear the halt. Operator action is still required.

B1 covers simulated/paper closes. Live close-order reconciliation and exchange-side idempotency remain outside B1; live must not reuse this protocol as proof of exchange atomicity.

## 5. Spot to Futures top-up

A top-up exists only after a confirmed transfer result. A request, pending transfer, or failed transfer is not an event.

Canonical fields:

```text
event_id, kind=SPOT_TO_FUTURES, portfolio_id, asset,
amount > 0, event_time, source, source_ref, status=confirmed
```

Effect:

```text
Spot                  -= amount
V                     += amount
TPV                   += amount
initial_capital        += amount
external_net_flow      += amount
CASH                   unchanged
initial_capital_original unchanged
```

The transfer reallocates existing capital; it does not create capital at the `Spot + TPV` level. Absolute PnL is unchanged because NAV and capital basis both increase by the same amount.

Paper mapping:

```text
paper_state["balance"] += amount
virt_debt               += amount
initial_tpv             += amount
reference_tpv           += amount
```

This keeps CASH unchanged and increases NAV by `amount`.

Backtest mapping:

```text
virt_debt          += amount
initial_capital     += amount
ledger_base         += amount
external_net_flow   += amount
val_cash             unchanged
```

Changing `initial_capital` must not restart the initial build, create CASH, reset positions or pending state, or mask PnL. It may change V's share and therefore trigger an ordinary rebalance; deficit purchases remain limited by CASH.

### 5.1 Insufficient Spot

A simulated top-up must debit an explicit tracked Spot balance. The behavior when that balance is insufficient is not approved: no partial credit, no retry loop, and no minting are allowed. Mark the case `OPEN` until the user approves a rule.

## 6. Futures to Spot withdrawal

Withdrawal symmetry is a proposal, not an approved feature. Do not implement it until OPEN-4 is resolved.

If approved, the symmetric effect would be:

```text
V                  -= amount
Spot               += amount
TPV                -= amount
initial_capital     -= amount
external_net_flow   -= amount
CASH                 unchanged
```

The transfer must be atomic. No CASH fallback and no negative V are allowed. A withdrawal that would make the capital basis non-positive remains OPEN-4.

## 7. Capital basis, PnL, and SAFE

Target basis:

```text
capital_basis     = initial_capital_original + external_net_flow
absolute_pnl      = NAV - capital_basis
nav_to_basis_pct  = (NAV / capital_basis - 1) * 100
```

`nav_to_basis_pct` is a profit-to-basis ratio, not a transfer-neutral return. It must not be labeled `return_pct` or used as the primary performance metric. The transfer-neutral metric is `index_return_pct` from section 8.

For the simple state with no config rebase and no excluded component, `initial_capital` equals `capital_basis`. `external_net_flow` is already included in that adjusted value; it must not be subtracted again.

SAFE treatment:

- SAFE is not an external flow and is not CASH.
- PROPOSED-1: include SAFE in NAV, absolute PnL, and the performance index.
- SAFE does not change `capital_basis` under either proposal branch.
- A physical withdrawal of SAFE would be an external flow and is not approved.

Current code is inconsistent: `PortfolioCalculator.total_pnl` excludes `siphoning_reserve`, while `core/main.py` `last_profit` and the backtest equity curve include it in some paths. Unifying this is a proposed reporting correction; it must not silently overwrite accepted evidence.

Config changes, blacklist rebases, cooldown rebases, and supervisor resets are not external flows. They must not be represented as transfers and must not erase a confirmed flow accidentally.

Example:

```text
initial_capital_original = 1000
profit before top-up     = 100
top-up                   = 200

NAV                      = 1300
capital_basis            = 1200
absolute_pnl             = 100
nav_to_basis_pct         = 8.3333%
index_return_pct         = 10.0%
CASH                     unchanged
```

The example shows why the two metrics need different names: the top-up raises NAV but must not be counted as an extra 10% strategy return. `nav_to_basis_pct` is diagnostic; `index_return_pct` is the transfer-neutral performance metric. If SAFE reporting is unresolved, both metrics must use the same NAV definition and state it explicitly.

## 8. Performance index

### 8.1 Purpose

The index measures strategy performance independently of external flows. A transfer must not create a false return or a false drawdown.

### 8.2 Rules

- `unit_price = NAV / units_outstanding`.
- A top-up issues `amount / unit_price_pre_transfer` units; a withdrawal redeems units at the same pre-transfer price.
- The transfer changes NAV and units together, so the unit price, return, and drawdown are unchanged by the transfer itself.
- Funding, trading PnL, commissions, and slippage change NAV without changing units, so they change the unit price.
- The index is calculated after the canonical event order in section 3.
- New backtest: seed `unit_price = 1.0`, `units_outstanding = initial NAV` before the first commission, fee, or fill. Initial build costs must reduce the unit price; do not seed from the first post-fee equity value.
- Migration/paper restart: seed at the current snapshot, record `index_seed_reason`, and do not claim pre-upgrade history is transfer-neutral.
- `index_return_pct` uses the unit-price series. Drawdown uses the unit-price high-water mark, not absolute TPV.
- A non-positive seed NAV cannot be used; reject/quarantine the index until OPEN-4 is resolved.

Acceptance example:

```text
Start NAV 1000, unit price 1.0, units 1000
Profit 100 -> NAV 1100, unit price 1.1
Top-up 200 -> NAV 1300, units += 200/1.1, unit price remains 1.1
Later NAV 1300 -> 1170 means unit price 1.1 -> 0.99, a 10% drawdown; the 200 top-up does not change it
```

### 8.3 Trailing stop

Do not automatically convert the existing trailing stop or risk thresholds to the index. The current high-water mark is stored as absolute TPV and some checks use `initial_capital`/`initial_tpv`. Find all consumers and show the effect of a top-up before proposing a change. Any such change requires separate approval.

Known consumers to audit:

- `core/main.py`: `tpv_ath`, `initial_tpv`, `last_profit`, `total_pnl_pct`, emergency and trailing-stop checks;
- `core/calculator.py`: `initial_capital`, `total_pnl`, `total_pnl_pct`;
- `backtest/backtest_rebalance.py`: `initial_capital`, `tpv_ath`, `trailing_stop_*`, `profit_pct`, `max_dd_pct`;
- `backtest/dashboard_attribution.py`;
- `supervisor/swarm_manager.py`, `supervisor/aggregator.py`, `supervisor/swarm_analyzer.py`;
- `monitoring/send_monitor_report.py`, `monitoring/health_check.py`;
- `dashboard/data_collector.py` and dashboard scripts;
- optimization scripts, which are not to be run or changed under this plan.

## 9. Event sources

### 9.1 Backtest

- Funding uses historical funding rates (`GET /fapi/v1/fundingRate`) and mark price at the funding timestamp (`GET /fapi/v1/markPriceKlines`, 1-minute).
- Every run records the funding model and source-data hashes.
- `signed_actual` is the target model.
- `legacy_constant` may be retained only as an explicitly labeled compatibility mode to reproduce accepted baseline evidence; it is not mixed with `signed_actual` in the same run and is not the final policy.
- Missing or ambiguous signed data fails explicitly or uses the approved legacy mode; it must not silently invent a rate.

### 9.2 Paper

- Events come from an explicit append-only journal or scenario fixture.
- Every event has a stable scenario-provided `event_id`, `portfolio_id`, `source`, `source_ref`, and status.
- No event is inferred from a balance delta.
- A simulated top-up requires an explicit Spot balance and a confirmed source debit.
- Restart and duplicate replay use the same event IDs as backtest.

### 9.3 Live read-only

Candidate sources, to be validated before any implementation:

- Binance USDⓈ-M income history: `GET /fapi/v1/income` with `incomeType=FUNDING_FEE`; use `income`, `asset`, `symbol`, `time`, and `tranId`.
- Binance Futures transfer history: `GET /sapi/v1/futures/transfer`; use `asset`, `amount`, `type`, `status`, `tranId`, and `timestamp`.
- Wallet snapshots only for reconciliation.

Requirements:

- read-only API key; no trade, transfer, or withdrawal permission;
- verify endpoint fields and stable identifiers against the actual account mode;
- never infer an event from a wallet delta;
- never treat reconciliation as PnL;
- overlapping polling windows plus idempotent event IDs;
- income-history retention requires an explicit opening balance or export; do not reconstruct missing history from the current wallet;
- no live execution or transfer is authorized by this document.

## 10. Event identity and idempotency

Every event is persisted before it affects state.

Required fields:

```text
event_id, kind, portfolio_id, symbol, asset, signed_amount,
event_time, source, source_ref, raw_payload_hash,
applied_at, status
```

Event ID rules:

- Funding: `binance:funding:<tranId>`.
- Transfer: `binance:transfer:<tranId>`.
- Paper/backtest: explicit scenario ID.
- Do not use amount plus timestamp as the sole key unless uniqueness across the account is proven.

Application protocol:

1. Validate ownership and payload, then persist `status=received` with a unique `event_id`. A received event has no accounting effect.
2. On retry, inspect the existing record:
   - same `event_id`, same payload, `applied`: no-op;
   - same `event_id`, same payload, `received`: resume application;
   - same `event_id`, different payload: conflict; quarantine and do not reapply;
   - `quarantined`: no effect without operator resolution.
3. Apply the accounting effect, persist the ledger and all affected state, and mark `applied` in one durable commit boundary.
4. On crash before commit, the event remains `received` and state is unchanged; recovery applies it once.
5. On crash after commit, retry is a no-op; never apply the effect twice.
6. Unknown or ambiguous events are quarantined without accounting effect.

The commit boundary must cover the event ledger, strategy state, and paper wallet together. Sequential writes to separate files are not atomic and are not sufficient: a crash between them can leave an applied event with stale strategy/paper state. If the storage layer cannot provide one transaction, use a durable write-ahead intent plus idempotent recovery, and test every crash boundary. This is per-event/per-state atomicity; it does not make a multi-leg emergency close atomic (section 4.5).

The existing `accounting/ledger.py` is prior art: it commits its own events and register state in one SQLite transaction, rejects out-of-order events, detects payload conflicts, and checks state/journal consistency. It is an offline, domain-specific ledger and is not integrated with the trading/paper cycle; it does not contain the paper wallet or strategy state. It must not be treated as a drop-in solution.

### 10.1 Late events

A late event is one whose `event_time` precedes the last applied accounting boundary. It must not be applied to current state: a late funding debit could have required a halt before trades that have already happened.

- Persist it as `quarantined` with reason `late_event`.
- Do not clear a funding halt and do not post a balancing entry.
- Apply it only by replaying from a durable state snapshot taken at or before that event, then recomputing all later effects; this reconstruction is not part of B1.
- Report the affected interval until reconciliation resolves it.

In-memory deduplication is insufficient; the processed set must survive restart.

## 11. Insufficient balances and quarantine

- Funding debit greater than V: apply the full debit, preserve negative V/shortfall, block increases, emergency close, halt, and require operator action.
- Insufficient Spot for a simulated top-up: `OPEN`; no partial credit or minting.
- Insufficient V for an approved withdrawal: atomic rejection; no CASH fallback.
- Unknown event type or ambiguous ownership: quarantine with no accounting effect.
- Never mint V, CASH, or Spot to make an event fit.
- Never clear a funding halt through a normal supervisor restart or clean-slate path.

## 12. Reconciliation

For read-only live operation, compare like with like:

- shadow wallet balance vs exchange wallet balance, per asset/account;
- shadow equity (wallet + unrealized PnL) vs exchange equity (wallet + unrealized PnL);
- SAFE reserve vs its own explicit ledger, never mixed into the wallet comparison;
- report NAV separately and state whether SAFE is included; do not compare NAV directly with wallet balance.

Verify every wallet delta is explained by funding, transfer, trading PnL, or fees. If the residual is unexplained, alert and quarantine the event stream. Do not auto-post a balancing entry and do not mark the residual as profit. Retain overlap between polling windows.

Reconciliation is a check, not a second accounting path.

## 13. Portfolio ownership

Every event must resolve to exactly one portfolio.

- Funding belongs to the portfolio holding the funded symbol/position.
- Transfers require an explicit portfolio allocation.
- Shared-account rules must not split an event by guesswork.
- Ambiguous events are rejected or quarantined until ownership is explicit.

Same-symbol funding across multiple portfolios is OPEN-3.

## 14. Tests required before implementation is accepted

- Funding paid decreases V/TPV exactly once; CASH unchanged.
- Funding received increases V/TPV exactly once; CASH unchanged.
- Funding received of 3 increases V/TPV and accounting equity by 3, changes `funding_paid` by -3, and leaves the accounting residual within tolerance; paid funding reverses the signs.
- Funding paid equal to V leaves V at zero and does not transfer from CASH.
- Funding paid greater than V applies the full debit, preserves negative V/shortfall, closes positions, records fees, and halts.
- The halt and increases-blocked flag are durable before the first close action.
- Crash after the first leg is closed but before the second: funding is not reapplied, the first leg is not re-closed, the second leg resumes, and the halt remains.
- Crash after funding/halt commit but before the first close: recovery closes both legs once and never reopens increases.
- Second-leg ERROR/PARTIAL/SKIPPED: actual fills and fees are persisted; the halt remains; only the unfinished reduction may resume.
- A duplicate close event or retry after a filled leg is a no-op; a successful close never clears the halt.
- Funding halt and the event journal survive restart/startup-reset paths; no automatic reopen; increases stay blocked.
- Duplicate funding event is a no-op; restart does not double-apply.
- Same `event_id` with a different payload is rejected as a conflict with no effect.
- Crash before commit leaves `received` and unchanged state; recovery applies once. Crash after commit leaves `applied`; retry is a no-op.
- A late funding event is quarantined, not applied to current state; a halt is not cleared.
- Paper funding updates `paper_state["balance"]` and `virt_debt` together; CASH is unchanged.
- Backtest and paper produce the same ordered event effects.
- Ordering test (B2, once transfer accounting is in scope): source order transfer-before-funding would avoid a shortfall, but canonical simulation order applies funding first and records the shortfall; replay of that source stream is quarantined as non-replayable.
- Replay of a confirmed ordered stream preserves source sequence and produces the same result; missing/ambiguous source order is quarantined.
- Confirmed top-up increases V, TPV, `initial_capital`, and `external_net_flow`; CASH and absolute PnL are unchanged.
- Top-up does not trigger initial build, reset positions/pending, create CASH, or mask PnL.
- Top-up followed by restart/clean start does not lose the confirmed flow.
- Top-up during a drawdown does not change the index drawdown.
- Transfer is not applied from a request or pending status.
- Index unit price is unchanged by top-up and changes by funding/fees/PnL.
- New backtest index is seeded before the first commission/fill; initial expenses reduce the unit price.
- `nav_to_basis_pct` is 8.3333% while `index_return_pct` is 10.0% in the section 7 example; reports do not relabel one as the other.
- Reconciliation compares wallet with wallet, equity with equity, and SAFE with SAFE; it does not compare NAV directly with wallet.
- SAFE is neither CASH nor an external flow; B1 fixtures use SAFE=0 and do not change legacy SAFE reporting.
- Legacy state files without new fields migrate with `initial_capital_original=initial_capital`, `external_net_flow=0`, and a documented index seed.
- Legacy constant funding mode reproduces accepted baseline evidence separately; it is not used as the final model.
- A1/A2a pending, guard, and halt regressions remain green.

## 15. Acceptance criteria

- The same ordered event sequence produces consistent backtest and paper states under the canonical simulation order.
- A repeated event, restart, or overlapping reconciliation window does not duplicate flows; a payload conflict is rejected.
- Crash before/after the funding commit recovers without partial accounting; a crash during the multi-leg close resumes only unfinished reductions and never repeats a completed leg.
- The transfer-neutral index is the primary performance metric; `nav_to_basis_pct` is reported separately.
- Funding and transfers do not create CASH.
- External flows do not appear as strategy profit in the index or absolute PnL.
- A funding shortfall is fully accounted once, closes risk, and leaves a stable halt.
- A confirmed top-up is transfer-neutral for performance and does not reset the strategy.
- Accepted A1/A2a baselines remain reproducible and are not overwritten.
- Live execution/transfer integration requires separate explicit approval.

## 16. Decisions and open items

### ACCEPTED-1: funding source and overflow

Funding is charged to V. If funding exceeds V, apply the full debit, preserve the negative shortfall, do not use CASH or automatic Spot coverage, block increases, emergency-close positions, save closure facts and fees, and halt until operator intervention.

### ACCEPTED-2: top-up accounting

A confirmed Spot-to-Futures top-up increases V, TPV, `initial_capital`, and `external_net_flow`; CASH and absolute PnL are unchanged; `initial_capital_original` is preserved.

### ACCEPTED-3: capital basis

`capital_basis = initial_capital_original + external_net_flow`; do not double-subtract the flow.

### ACCEPTED-4: transfer-neutral index

The performance index issues/redeems units at the pre-transfer unit price. Transfers do not change unit price, return, or drawdown; funding/fees/PnL do.

### PROPOSED-1: SAFE in reported PnL

Include SAFE in NAV, absolute PnL, and the index; keep it out of CASH and capital basis. This corrects current inconsistent reporting but changes some reported values, so it requires confirmation before implementation. This proposal is outside B1, whose fixtures use SAFE=0.

### PROPOSED-2: withdrawal symmetry

Symmetric withdrawal rules are not approved. They remain blocked by OPEN-4.

### OPEN-3: shared-account allocation

How funding and transfers are assigned when multiple portfolios share one exchange account, especially when two portfolios hold the same symbol. The default draft rule is explicit `portfolio_id`; ambiguous events are quarantined.

### OPEN-4: withdrawal that exhausts the capital basis

A withdrawal that would make `initial_capital` or `capital_basis` non-positive is conservatively rejected until an explicit policy exists.

### OPEN-5: insufficient Spot for a simulated top-up

No partial credit, retry loop, or minting is allowed. The exact rejection/alert policy requires approval.

### OPEN-6: trailing stop and risk thresholds

Whether the trailing stop and other risk thresholds should use the transfer-neutral index instead of absolute TPV/initial capital is not approved. Audit consumers and present the effect first.

### OPEN-7: live integration

Live read-only reconciliation remains design-only. Live transfers, orders, and execution require separate explicit approval.
