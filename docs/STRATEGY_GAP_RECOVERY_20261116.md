# PAPER minute-gap recovery, 2026-11-16

## Accepted policy

The user selected preservation of existing PAPER positions, with no retroactive trades.
The current runtime has `paper_mode=true` and `funding_model=disabled`.

`core/backtest_rules.py` now validates the last observed wallet/equity and execution
checkpoint before allowing an open PAPER session to evaluate the latest closed minute.
It records the skipped interval in `strategy_gap_reconciliation`. It does not advance
`strategy_bar_ms` itself: the existing successful-cycle checkpoint remains authoritative.
Balances, quantities, cost bases, receipts, capital, ATH, drawdown and trailing state are
not reset by reconciliation. The normal strategy and current safety guards run afterwards.
Invalid/stale market history, unresolved execution, inconsistent wallets, pending funding,
REAL gaps and non-disabled funding remain blocked. Historical decisions and unobserved
risk extrema during the outage are not reconstructed or represented as executed facts.

## Provenance and deployment

- Workspace baseline: `97c198ec4069546f4899c12624005063840e4b53`, clean before source edits.
- Runtime baseline: `61e93230d0d2469a0a5ec25d7a7106cc9bcf1b3c`; existing dirty config was preserved.
- Original runtime module SHA256: `98f0cab08ac062e349abcf99d797aa7ab8545be1d51d5e4871ff2cc5df06614a`.
- Deployed module SHA256: `bc2a830c52084f7b3833254637ef23f9f5abc2820ad0cd0bdf81b7fc45e640b2`.
- Only `C:/Python/Prosperous_Bot/futures_portfolio/core/backtest_rules.py` was installed.
- `audit_paper_gap.py` reconciled positions against confirmed fill receipts, balances,
  saved equity and budget allocations for all eight sessions. No financial snapshots
  were edited. `preflight/` and `stopped_snapshot_*/` retain originals and SQLite backups.
- A UAC-elevated process was required for the existing administrator PM2 pipe. It stopped
  the supervisor and exactly the eight existing PAPER workers, re-audited frozen state,
  installed the module and restarted the same PM2 entries. The supervisor was then resumed.
- No push, REAL actions, wallet resets or parameter optimization were performed.

## Validation

Native interpreter: `C:/Python/Prosperous_Bot/futures_portfolio/.venv/Scripts/python.exe`.

1. `-B -m pytest -q -o addopts= tests/test_backtest_rules.py tests/test_strategy_gap.py tests/test_runtime_backtest_parity.py`: **72 passed**, exit 0.
2. `-B -m pytest -q -o addopts= tests/test_paper_gap_audit.py`: **3 passed**, exit 0.
3. `acceptance.json`: **8/8 sessions advanced at least two closed-minute decisions**,
   no errors in their logs since recovery, identical session identities and initial
   capital, and unchanged PAPER budget account/allocations/events. Deployment hashes match.

Rejected attempts: the sandbox Python 3.12 could not load the runtime's Python 3.11 binary
dependencies (57 setup errors); an intermediate native run had 54 passes and three error
message compatibility failures. Neither is counted as a passing run. The final native
run above includes the corrected error-prefix handling.

Acceptance covers the observed resumed minutes, not long-duration monitoring, actual
Binance trading, or recovery with enabled funding. See `recovery_status.json` for exact
worker identities, timestamps and the recorded intervals.
