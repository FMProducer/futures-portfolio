# Strict neutral rebalance correction

Status: SUPERSEDED proposal. The user rejected notional balancing and clarified leg value as allocated entry cost plus unrealized PnL minus commissions. Equal quantities and paired resizing below are not the accepted strategy specification. See IMPLEMENTATION_EQUITY_SIGNAL_FIX.md. This proposal preserves the rejected interpretation for provenance.

## Confirmed requirement

At every completed portfolio decision, LONG units equal SHORT units for the same linear contract. Step size is common. A mismatched state is a failed hedge requiring reconciliation, not a successful rebalance. Accounting must still record actual partial fills truthfully.

## Defects found

- Entry sized the legs independently; spread and rounding could produce unequal units. Corrected to a shared smaller size with both-leg funding preflight.
- The existing SHORT decision value contains signed unrealized PnL and differs from current notional. Equal decision shares do not imply equal delta.
- `next_action` selects one leg; historical, paper, public-paper and durable callers consume a single action.
- Historical and candle-paper paths can continue after a funding rejection of an individual fill, leaving an incomplete pair.
- Existing trailing tests explicitly expect independent one-leg reductions and additions; those expectations conflict with the required invariant.

## Proposed policy requiring confirmation

Use common current notional `quantity * mark` for each leg against `trading_equity / 4`. Preserve configured surplus/deficit/trailing thresholds as signal conditions. Resize both legs by identical step units. Preflight the complete pair, including fees, available shared margin and minimum notional; never execute only the affordable half. Re-evaluate the actual resulting share after execution. No new numeric thresholds are proposed.

The existing durable single-order path cannot claim atomic paired execution. It must fail closed for a paired decision until paired intent, partial-fill recovery and restart reconciliation are implemented and verified. Offline simultaneous application is not evidence of simultaneous exchange fills.

For equal quantities of one linear ticker, price changes cancel in aggregate PnL between trades. Equal opposite executions at the same price do not create portfolio profit by realizing one leg's gains; spread, fees and slippage are costs, and same-rate funding cancels. A profitable price-harvesting claim under continuous exact neutrality would require another economic mechanism and cannot be manufactured by an accounting change.

## Verification scope

Kernel entry tests: 18 passed after entry correction. Full rebalance correction, affected caller integration tests and historical replay are pending policy confirmation. Historical market inputs are restricted to `C:\Python\Prosperous_Bot\futures_portfolio\data`; no timestamp or funding assumptions may be invented for its timestamp-free Feather files. Existing frozen research snapshots and cancelled paper evidence remain unchanged.
