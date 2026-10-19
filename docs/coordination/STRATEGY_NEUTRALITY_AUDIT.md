# Strategy neutrality audit — 2026-10-18

Subsequent clarification: the user requires balancing USDT equity shares, not notional or equal quantities. Inventory mismatch remains the measured cause of the jumps, but does not alone prove a violation of that equity-share policy. The forced equal-entry correction was withdrawn. See IMPLEMENTATION_EQUITY_SIGNAL_FIX.md for confirmed signal defects and corrected local-history diagnostics.

The baseline does not enforce strict delta neutrality. Calling it strictly delta-neutral before checking actual inventory was incorrect. No strategy changes, parameter optimization, or new strategy runs were performed for this audit.

## Saved annual report reconciliation

This section was completed before the user's restriction to historical inputs in `C:\Python\Prosperous_Bot\futures_portfolio\data`. It describes the existing B00 evidence, not a new run on that directory.

At 2026-01-31 18:43 UTC, price fell from 77349.60 to 75693.80. Actual inventory before the minute was LONG 0.032 BTC and SHORT 0.023 BTC. The residual delta was +0.009 BTC. There were no fills or funding events in this minute:

`0.009 * (75693.80 - 77349.60) = -14.90220 USDT`

This exactly equals the saved Equity decrease. The following minute's +18.204577664496 USDT change reconciles to +16.42500 from the previous delta and +1.779577664496 from a SHORT reduction including its fee. That reduction increased net LONG exposure to 0.010 BTC.

Independent Decimal reconciliation of all 525600 saved minute changes used previous inventory times price change, signed fill quantity times close-minus-fill-price, fees, and funding in original event order. It consumed 679 fills and 1095 funding events. Maximum absolute arithmetic residual was 1E-23 USDT. Terminal quantities, total fees and funding matched the saved result. This is arithmetic consistency with recorded inputs, not verification of execution realism or market data provenance.

Residual delta was nonzero for 490962 recorded minute closes. Maximum absolute delta after all fills at a timestamp was 0.015 BTC (LONG 0.040, SHORT 0.025).

The kernel balances `qty * average_entry + unrealized_pnl - open_fees` against a target. Equal values of this measure do not imply equal LONG and SHORT quantities. Independent one-leg actions lack an invariant requiring equal quantities. Therefore directional Equity moves are a property of the implemented inventory policy. The audited jumps do not require an Equity arithmetic error to explain them.

Paper and B00 kernel SHA256 were identical: `34aae718a6d4f96a3ab3cbdcee3256ffb5279a03af9c722aef5e4b8377c614eb`; strategy settings also matched. The paper process was stopped and its monitor paused following user cancellation. Its historical RUNNING text is not completion evidence.

Evidence: `reports/strategy_diagnostic_2026-10-18/minute_reconciliation.json` and its generator `analyze.py`.

## User-designated historical data

After the restriction, only local historical files in the designated directory were examined as price inputs. `BTCUSDT_live.feather` and `BTCUSDT_live_365d.feather` are byte-identical; SHA256 `de0ab2af9e54b58b8a3f9751687b8d91ee6d11d8dd4cf0fcd02268dd87aa471d`.

The dataset contains 525600 OHLCV rows, no nonfinite cells and no invalid OHLC rows. Its index is RangeIndex; timestamps and funding are absent. The existing `scripts/download_history.py` drops the time column before saving. Thus row count alone cannot establish dates, minute continuity or the requested calendar-year coverage.

The largest negative adjacent close change is at zero-based row 151128: 77349.60 to 75693.80. This confirms the same price pair exists in the designated data. It does not independently establish its UTC timestamp or inventory/funding alignment. The largest positive adjacent price change is row 438932: 67581.30 to 70307.10; largest price change and largest Equity change need not coincide because exposure varies.

Evidence: `reports/strategy_diagnostic_2026-10-18/user_data_validation.json`.

Full calendar-aligned strategy/PnL verification using exclusively this directory remains incomplete without original timestamps and funding evidence. No dates, zero funding, hedge band, or replacement trading rules were invented. No trading admission follows from this audit.
