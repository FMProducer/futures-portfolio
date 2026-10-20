# Saved dashboard comparison — 2026-10-19

The supplied BSXperpA HTML was read without rerunning or modifying its source bot. Its Economic Equity series confirms final Equity 2008070.35 RUB from a 2000000 RUB capital baseline: Net +8070.35 RUB, return +0.4035175%, maximum series drawdown 0.07161118114%. This is the portfolio economic series, not a sum of decision values and wallets. The source Net remains a saved-run result, not a newly executed PASS.

| Property | Supplied BSX dashboard | Adapted BTC diagnostic |
|---|---|---|
| Observations | 26696 candles plus terminal point | 525600 source rows |
| Window | 2026-06-18 to 2026-07-17 MSK, displayed | Calendar unknown; timestamps absent |
| Initial capital | 2000000 RUB | 10000 USDT |
| Return | +0.4035175% | −1.28672636%, excluding funding |
| Max observed drawdown | 0.07161118% | 3.63696912% |
| Initial step as portfolio share | 0.182883985 pp (44.83*81.59 / 2000000*100) | 1.069687 pp (106968.7*.001 / 10000*100) |
| Price range | 42.23–46.60, start44.83/end44.00 | 57843.0–116226.8, start close106979.9/end80877.7 |
| End state | Both legs closed in saved data | LONG .042 / SHORT .046 BTC open |

The configured BTC step is about 5.85 times coarser as a share of portfolio capital. Identical .7/.7/.09 signal thresholds therefore do not imply comparable sizing precision. The BTC close sequence fell about 24.4% over its own window, whereas BSX ended about 1.85% below its own start. These are observed differences, not an isolated causal attribution of the performance gap.

A read-only hypothetical final BTC close under its existing spread/slippage/fee settings would cost an additional 1.42344752 USDT price adjustment and 2.846920920864 USDT exit commission: Net_flat excluding funding −132.943004795064 USDT. This arithmetic adjustment does not rerun the strategy or reconstruct funding, does not change the saved OPEN result, and does not explain away its loss.

Source dashboard labels contain metadata inconsistencies: trail=0.00% despite the supplied CLI's .09, and lots/leg=100 despite recorded initial fills of 136 each. Final Net agrees with the supplied CLI. Effective parameters should be verified from a run manifest, not inferred from those labels. Its displayed initial-to-final Equity span excludes the entry debit; that is why the dashboard itself flags a 1095.01 RUB Equity-span/Net gap. Starting from pre-entry capital reconciles final Equity to +8070.35 RUB.

No strategy parameters, instrument steps, source histories, plot values, workers or policies were changed by this comparison. Full event-level source/target parity remains unverified; this comparison establishes that the current economic profiles differ and that the inputs/conventions are not matched. Raw summary and source HTML SHA256: reports/strategy_diagnostic_2026-10-18/dashboard_comparison_2026-10-19.json.
