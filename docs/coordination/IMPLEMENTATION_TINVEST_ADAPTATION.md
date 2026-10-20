# T-Invest decision-kernel adaptation — 2026-10-19

## Source and status

The user explicitly requested adaptation of `C:\Python\Prosperous_Bot\t-invest-2-build\docs\last_current_trading_logic.md` to this checkout. The governing text is its first owner ruling dated 2026-10-18, R6S4_095, which supersedes the historical sections. Source document SHA256: `0fa1e5a6a856d95763e7dbd0a0e1e5ef20ff4bf76e3021a1c8b4662cdb9f130c`, verified unchanged after the work. Source checkout HEAD: `26edf8e3c25ad7334632f35870d4b79d5162d230`. Source planner formulas were also inspected read-only. Target base HEAD: `88f7745`, master, with previous working changes preserved.

This implements the documented decision kernel in the existing offline `rebalance` namespace. It is not a complete migration of the donor's runners, broker integration or risk supervisors. No live process, paper worker, transfer, commit or push was started. Frozen evidence and existing DBs were not modified.

## Mapping and implemented rules

| Source | USDT adaptation |
|---|---|
| A LONG / B SHORT | LONG / SHORT |
| Integer lots times RUB multiplier | Integer exchange-step units times BTC step |
| D=qty*M*avg+UPL−remaining opening fees | Same formula with qty=units*step |
| Current E_total/4 | Current (shared wallet+UPL−unpaid liability)/4, preserving accepted shared-wallet model |
| Two separately funded accounts and equalizer | One shared funded wallet; no artificial transfers or duplicated balances |
| Broker margin_rate | Existing fixed-leverage simulation; not a Binance margin engine |
| RUB penny commission rounding | Existing Decimal USDT model, no invented exchange rounding rule |
| LONG then SHORT entry, floor sizing | Entry now floors target/unit, caps to shared affordability and recomputes SHORT target after trial LONG fill; both legs preflighted |

Latest ruling is implemented literally for core sizing:

`REDUCE units = nearest(excess * .9 * (1.5 if unit>0 and abs(excess)>=5*unit else 1), unit, held_units)`

`ADD natural = nearest(deficit * .81875 * (.9 if held_units<other_units else 1), unit_ADD, 10**12)`

ADD then respects shared affordability, minimum notional, and 2*initial_units cap; legacy initial_units=0 uses 2*held_units fallback. Existing ADD edge>=one closing commission, REDUCE edge>=three closing commissions and full roundtrip-fee coverage remain enforced. Edge uses the original distance, not the sizing-scaled distance. Action order remains executable REDUCE before ADD, LONG before SHORT.

Readiness compares OLD peak/trough before updating. Seed=25; SHORT REDUCE distance=.3*trail_pp, all other distances=trail_pp. After fill REDUCE peak=25+half_width; ADD trough=25−.9*half_width. Only the executed side/direction is consumed. Raw surplus/deficit boundaries are not an execution dead band. Zero distance uses the same comparison and permits equality, as documented.

The prior experimental threshold-activation/None-consumption/unchanged-mark blocker was therefore superseded, not combined with the donor rules. See the historical IMPLEMENTATION_EQUITY_SIGNAL_FIX.md for its separate evidence. Durable schema is now shared-wallet-state-v3; v1/v2 are rejected with migration required, never silently reset.

## Evidence and results

Full final `rebalance/scenarios` suite: **274 passed**, 21.42 seconds. Coverage includes new fixed expected cases for both REDUCE size regimes, both relative-quantity ADD regimes, SHORT reduced trail distance, documented consume pins, inside-band observations, entry flooring/second-target recomputation, old-state rejection, durable serialization, and existing caller integration. The cycle-activation fixture now uses explicit 20% spread and funding +2/−1 to create the required unequal floored entry; its next-open exit expectation is unchanged. This is test input isolation, not a strategy setting.

Exactly one diagnostic ran on the user-designated `C:\Python\Prosperous_Bot\futures_portfolio\data\BTCUSDT_live_365d.feather`, 525600 rows, SHA256 `de0ab2af9e54b58b8a3f9751687b8d91ee6d11d8dd4cf0fcd02268dd87aa471d` checked before/after. No downloads or optimization. Signal=previous close, execution=current open with the existing spread/slippage assumptions. Config unchanged: capital 10000 USDT, fee .0004, step .001 BTC, leverage 7, min notional 5.3 USDT, spread 2 bps, slippage 1 bps, surplus/deficit/trail .7/.7/.09.

Artifacts in reports/strategy_diagnostic_2026-10-18:

- tinvest_adapted_2026-10-19.json: effective settings, exact source/core hashes and metrics.
- tinvest_adapted_2026-10-19.npz: all 525600 Equity/drawdown/share/quantity observations.
- tinvest_adapted_2026-10-19.png: inspected four-panel chart.
- check_corrected_history.py: reused diagnostic generator with explicit exclusive output selection and dynamic chart labels; previous results were retained.

| Metric | Result |
|---|---:|
| Final open-position Equity | 9871.327363645800 USDT |
| Net excluding unavailable funding | −128.672636354200 USDT |
| Fees | 14.656218454200 USDT |
| Rebalances | 429 |
| Maximum close-observed drawdown | 3.636969118899% |
| Worst Equity step | −16.5580 USDT |
| Maximum absolute inventory delta | .018 BTC |
| Final LONG / SHORT | .042 / .046 BTC |

Every row independently reconciles Equity change to old inventory times price change plus signed fill valuation effects minus fees, residual<=1E-23 USDT. The worst step at zero-based row 151128 is exactly .010 BTC times (75693.8−77349.6). This is arithmetic consistency, not trading readiness.

## Deliberate boundaries and unresolved properties

- Source §2 explicitly says independently managed hedged legs do not imply equal quantities or zero delta. The adaptation does not claim strict delta neutrality, remove price jumps or guarantee profitable results. The donor's RUB Net floor is not portable to BTCUSDT; no Binance floor was invented.
- Source C-01/C-02 remain: negative excess can yield zero REDUCE despite readiness; market-versus-mechanical observation activation is not contract-certified by seed/pin parity. The transfer reproduces documented behavior, not proof that the donor's open issues are solved.
- RUB daily-loss sums, 1.2 per-account margin gate, donor equity trailing activation/timeout/cooldown, two-account wallet equalizer, Moscow session and broker rounding are not silently installed as USDT policy. Existing optional portfolio-cycle policy and accounting/execution safeguards remain; the diagnostic did not enable a new cycle policy. Complete supervisor equivalence is not claimed.
- Source Feather lacks timestamps and funding. Calendar coverage and missing-minute continuity are unverified; funding is excluded rather than assumed economically zero. Positions stay open at the end; no synthetic terminal liquidation was included. Spread/slippage do not model depth, liquidation or partial fills.
- Shared-wallet funding is an intentional adaptation rather than per-account buying-power parity. No hot-config changes or v1/v2 state migrations are implemented by this work. Existing historical snapshots must keep their frozen kernels.
