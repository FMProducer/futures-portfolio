# Equity-share signal correction — 2026-10-18

Historical implementation, superseded on 2026-10-19 by the user's explicit request to adapt last_current_trading_logic.md. Its None consumption and threshold-activation policy were replaced by the source's documented seed/pins and latest owner sizing ruling. Earlier checks and artifacts below remain evidence only of this historical version. See IMPLEMENTATION_TINVEST_ADAPTATION.md.

## Result

Threshold activation and trailing consumption defects were corrected. The user's formula is retained: remaining allocated entry cost (`qty * avg`) + unrealized PnL − remaining opening fees. Closing fees remain charged to wallet/Equity without double subtraction. The previous forced equal-entry-quantity change was withdrawn following the user's rejection of notional/equal-quantity balancing.

The correction does not eliminate the resulting inventory's price sensitivity. On the designated historical sequence, the worst Equity step remains −18.2138 USDT, exactly +0.011 BTC times −1655.80 USDT. This is not certification that the entire strategy meets the user's original market-neutral expectation.

## Confirmed defects and changes

- Readiness previously required only reversal from extrema seeded at 25; surplus/deficit thresholds did not gate activation. REDUCE now observes shares >=25+surplus_pp; ADD observes shares <=25−deficit_pp. Accumulated signals may execute inside the band after the configured reversal.
- Fills previously wrote artificial extrema 25+half_width / 25−0.9*half_width. The executed direction is now consumed to None and requires a new qualifying observation. The other observed direction is retained.
- Fill records its mark for both legs because execution changes the shared share denominator. An unchanged mark cannot arm or execute another signal, including at trail_pp=0. No fictitious reversal is inferred from the execution itself.
- Shared core callers are historical, durable, candle-paper, public-paper and replay. No separate replacement planner was added.
- Durable version v2 rejects v1 rather than adopting artificial old extrema. No existing DB was migrated, reset or modified.

Unchanged: 25/25/50 equity-share targets, liability exclusion, monetary accounting, step rounding, margin/fees/funding, economic gates, ADD .825 sizing, 2*initial_units cap, numeric configuration and optional cycle policy. No paired-trade/notional policy was introduced.

## Verification and exact scope

Base HEAD 88f7745, master; existing diagnostic working changes preserved. Full rebalance scenario suite: 265 passed. Two further state tests were added afterward; affected durable suite: 17 passed, including v1 rejection and consumed-signal/mark serialization. No production code changed after the 265-test run. No commit/push, optimization, network data fetch or paper restart.

One diagnostic consumed all 525600 OHLCV rows in `C:\Python\Prosperous_Bot\futures_portfolio\data\BTCUSDT_live_365d.feather`; SHA256 de0ab2af9e54b58b8a3f9751687b8d91ee6d11d8dd4cf0fcd02268dd87aa471d, checked before/after. The identical live.feather is not an independent sample. Generator: reports/strategy_diagnostic_2026-10-18/check_corrected_history.py. Result: corrected_history_check.json in the same directory, with exact code hashes/settings.

Existing historical_example.json settings: capital 10000, spread 2 bps, slippage 1 bps, fee .0004, step .001, thresholds .7/.7/.09. Previous close drives the signal; current open drives execution. No cycle-policy selection or parameter search.

Results: 181 fills including entry; 179 rebalances; fees 7.701314414440 USDT; final open-position Equity 9943.889826925560 USDT; Net excluding unavailable funding −56.110173074440 USDT; close-observed max drawdown 3.916914582362448%; maximum absolute quantity difference 0.021 BTC. Every row reconciles Equity changes to old delta price PnL plus signed fill close-minus-price effect minus fees, maximum residual 1E-23 USDT. Each row also asserts the leg-value formula. No funding-balance execution rejection or simulated collateral exhaustion occurred.

These metrics are not directly comparable with calendar-year B00: source window alignment is unknown and funding is excluded. Source has RangeIndex without timestamps/funding; calendar coverage, missing-minute detection and full funded annual economics are unverified. Positions were not terminally closed. Assumed spread/slippage is not execution realism or a depth/liquidation model. Frozen research and cancelled paper evidence remain unchanged. No trading admission follows.
