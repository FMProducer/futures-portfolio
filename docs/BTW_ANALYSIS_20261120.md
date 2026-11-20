# BTW investigation — 20 November 2026

## Delivery sequence and provenance

The prior PM2 recovery and read-only swarm observations were first recorded in CHANGELOG and Obsidian `Projects/futures-portfolio-pm2-recovery-and-health-2026-11-20.md` / `Docs/changelog.md`, then committed as `cd85539d7f46f18ae14a6625518126c6c59ca79e` on `codex/pm2-no-spawn`. This research was performed afterwards and is not part of that earlier commit. No push or merge was performed.

Runtime source: `C:\Python\Prosperous_Bot\futures_portfolio`, HEAD `da99dd4ad0bac91f2ca91ad01c55b7477c9bf5f1`. Research uses a frozen copy of actual runtime Python sources and a sanitized financial configuration. Exact hashes, timestamps, inputs and allocation identity are in `reports/btw_analysis_20261120/manifest.json`. Working strategy files, configuration, positions, PM2 and budgets were not changed by this investigation. No private API calls, orders or optimization were used.

## History coverage and model

A full 365-day request was made. Binance's BTWUSDT PERPETUAL onboard time is 2026-06-04 14:15 UTC. Available data cover **2026-06-04 14:15 through 2026-11-20 06:20 UTC**, end-exclusive 06:21: **242,886 closed 1-minute bars, 168 days 16 hours 6 minutes**. There are no timestamp gaps or duplicates. Before listing, the requested year has no BTW futures history; no synthetic pre-listing candles were added. This is an available-history backtest, not a completed 365-day test.

Current PAPER session started 2026-11-17 19:56:28.750 UTC (21:56:28 Kaliningrad). Its first decision used the 19:55 candle. The comparable replay interval is **17 Nov 19:55 through 20 Nov 06:20 UTC**, 3,506 bars, 58h26m. Frozen strategy mark: 0.6239; strategy equity: 802.59626523 USDT.

All native backtests use the same current financial settings: strategy initial capital 1,000, external modeled margin reserve 5,000 (outside strategy CASH), LONG/SHORT/VIRTUAL shares 25/25/50, leverage 3/3/1, share-trail distance 0.0580088734, surplus/deficit 0.04, commission 0.04%, synthetic slippage 0.05%, funding disabled. No historical funding costs or historical order books are implied. The native engine does not execute supervisor selection or actual shared-account margin management.

The native backtest hardcodes quantity step 0.001, while downloaded BTW exchangeInfo requires LOT_SIZE and MARKET_LOT_SIZE step **1**. Additional isolated probes replace only `quantize_qty`'s step with the exchange-confirmed value. Production code and risk parameters are unchanged.

## Completed results

| Case | Final strategy equity | Net USDT | Return on 1,000 | Strategy MaxDD | Cycles | Market fills |
|---|---:|---:|---:|---:|---:|---:|
| Available history, native step .001 | 6757.085948 | +5757.085948 | +575.708595% | 219.001758% | 626 | 827 |
| Available history, confirmed step 1 | 6753.299250 | +5753.299250 | +575.329925% | 218.990872% | 626 | 827 |
| Session, native step .001 | 876.036221 | -123.963779 | -12.396378% | 19.759178% | 8 | 7 |
| Session, confirmed step 1 | 877.639645 | -122.360355 | -12.236036% | 19.480097% | 8 | 7 |
| Actual PAPER at frozen mark / recorded-price native PAPER replay | 802.596265 | -197.403735 | -19.740373% | 31.162983% | 12 | 9 |

Both requested native runs completed with exit 0. The two lot-size probes and the two isolated native PAPER replays also completed successfully. Commands, per-run exits and complete results are retained in their subdirectories. Independent verification reconstructed each market fill's realized PnL, all fees, final positions and the entire equity curve; maximum curve residual was approximately 5e-12 USDT. Frozen source and data hashes are unchanged. Slippage is already included in fill prices and was not deducted twice.

### Long-period risk

On confirmed lot size, the model's external-reserve-inclusive account starts at 6,000 and ends at **11753.299250 USDT**, return **95.888321%**, account MaxDD **74.593914%**. These are single-bot model values, not verified exchange collateral or the swarm PAPER budget.

Strategy equity reached **-3073.470148 USDT**, remaining negative for **1007 minutes**. Minimum reserve-inclusive model collateral was 1926.529852. The configured external reserve allows this path in the model. Strategy MaxDD over 100% and zero modeled liquidations do not establish exchange safety. There were 111 modeled trailing closes. No universal horizon or risk setting was optimized or selected.

## Why the current session loses

Confirmed accounting from the 9 market receipts: realized PnL **+25.69784635**, paid fees **0.99581647**, wallet balance **1024.70202988**. At the frozen 0.6239 mark, LONG unrealized **+232.36214700**, SHORT unrealized **-454.46791165**. Their sum independently gives equity 802.59626523.

Initial quantities were LONG 1,753 / SHORT 1,749. The strategy reduced LONG by 568 and added 1,337 SHORT as price rose. Final quantities are **LONG 1,185 / SHORT 3,086**, net **SHORT 1,901 BTW**. At the latest book midpoint, net directional notional is about -1,190.98 USDT; a further +1% price move costs about 11.91 USDT while quantities remain unchanged.

The session's observed best strategy equity was 1,012.71 (state ATH 1,012.70579837614), below the configured 1.55% activation level 1,015.50. Profit trailing therefore never armed. `max_drawdown_limit=100` explicitly disables the max-DD stop (`core/capital.py:drawdown_threshold`). The selected supervisor policy retains losing open PAPER positions. These settings explain continued holding; no new policy was imposed here.

The observed session low was **697.12 USDT**, price 0.695, at **19 Nov 10:21:14 Kaliningrad**. Current cycles include four internal VIRTUAL releases: 12 cycles are not 12 market trades. Virtual debt fell from 500 to 365.22743139086. Full chronological fills and heartbeats are in CSV/JSON; the DNS outage produced three logged resolution errors and the reconciled three-minute gap, without fabricated fills.

## Explain the backtest/PAPER gap

1. Correcting quantity step alone changes session equity from 876.036221 to 877.639645; it does not explain the PAPER loss.
2. On identical final candles and step 1, the existing native PAPER loop and the backtest agree to **1.14e-13 USDT**, with identical positions. This isolates the runtime strategy implementation from the input-price effect for this case.
3. **358 of 3503 observed PAPER prices (10.2198%)** differ from later historical closes for the same minute. Maximum absolute difference is 0.00570000. All differing observations were logged in the first five seconds after the minute boundary; 284 were within the first two seconds.
4. Replaying the recorded heartbeat price tape with native PAPER logic and step 1 reproduces the actual **equity, 12 cycles, wallet balance, weighted entries, both position quantities and all 9 confirmed market receipt payloads exactly**. The replay fills the three unobserved minutes with actual historical closes; raw full 56-bar API responses and intra-minute timing were not archived. This is an exact reproduction of the observed final accounting/fills, not proof of every original API response.

The timestamp pattern is consistent with candle finalization/latency effects, but the upstream reason cannot be established from retained logs alone. The retained evidence does not justify calling it a proven Binance defect, VPN error or look-ahead bug. Small quote differences change trailing extrema and subsequent action paths materially.

## Current full-close BID/ASK estimate

Quote time: **2026-11-20T08:37:34.009256+02:00**. Positions still LONG 1185.0 / SHORT 3086.0. Best BID **0.6264000**, ASK **0.6266000**. Full LONG sale uses 1 bid level, VWAP **0.6264000**; full SHORT buyback uses 7 ask levels, VWAP **0.6270247569669475048606610499**.

Estimated equity before exit commission: **795.91576523**. Exit fee at the independently verified PAPER rate 0.04%: **1.07091296**. Estimated equity after closing both legs: **794.84485227 USDT**, Net **-205.15514773**. Spread plus depth impact versus the simultaneous midpoint: 1.73790000; market movement since the saved strategy mark is separately +2.28120000.

This is a hypothetical visible-book estimate, not an order or guaranteed execution. Both legs have sufficient displayed depth. No last/mid/entry substitution is used for the closing prices. Midpoint appears only as a labeled decomposition baseline. The private exchange fee tier was not queried; historical BID/ASK cost cannot be reconstructed from OHLCV. Public Binance requests used the physical interface (192.168.1.3) with proxies disabled and no VPN configuration changes.

## Proposals, in priority order

1. **Make replay input parity a gate before further parameter tuning or REAL admission.** Persist raw candle responses, exchange/local timestamps, candle identity and the price used for each decision; measure finalization delay and confirm stable closed-bar values. Consider exchange-confirmed closed-kline events with a REST consistency check. Validate against the observed execution tape. This case proves that final historical OHLCV alone can materially understate PAPER loss.
2. **Use exchange filters in the backtest.** Replace the native hardcoded .001 by verified per-symbol LOT_SIZE/MARKET_LOT_SIZE, and require first-fill and full-session parity. This is an execution-model correction, separate from risk policy.
3. **Specify explicit risk limits before relying on the long-period profit.** Candidates for user-approved separate experiments are a net-directional-exposure cap, a finite portfolio loss budget and a defined policy for adverse additions. The current strategy can accumulate a large short against a rising market, while DD=100 disables its loss stop and profit trailing never activates. No thresholds were invented or applied here.
4. **Validate the external reserve, funding and forward behavior before REAL.** The long-period result assumes 5,000 additional modeled margin, disabled funding and synthetic fills. Test on an independently recorded forward period with confirmed candle inputs and historical funding; retain supervisor/shared-account limitations. The result is not evidence to increase leverage or remove safeguards.

## Evidence

Local study root: `reports/btw_analysis_20261120/`. Files: `manifest.json`, `data_manifest.json`, `exchange_info.json`, raw pages, frozen config/source, coherent PAPER snapshots, `bid_ask_valuation.json`, `latest_bid_ask_valuation.json`, `paper_receipt_reconciliation.json`, `paper_timeline.json`, `paper_fills.csv`, `paper_heartbeats.csv`, `price_history_comparison.json`, `runtime_parity_comparison.json`, `independent_verification.json`, each run's command/result/exit, and `BTW_analysis.png`.

Binance primary API references: [closed-minute historical klines](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data), [order book](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Order-Book), [kline stream](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Kline-Candlestick-Streams).

No strategy modification, live close, bot restart or second commit was performed after the requested initial commit. Full-year coverage, historical order-book replay, actual funding and private margin/fee verification remain unavailable or unperformed as stated above.
