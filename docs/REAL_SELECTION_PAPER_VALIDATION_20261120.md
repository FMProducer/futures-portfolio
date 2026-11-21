# REAL selection policy: approved design and PAPER validation

Recorded 2026-11-20. User approved the proposed design, then instructed: test every proposal against accumulated PAPER trading data. Numerical REAL risk limits remain unapproved pending evidence.

Approved design: deduplicate PAPER sessions; accumulate cycles and net across session boundaries; include open losses and executable exit costs; separate incompatible strategy configurations; retain the 15-cycle admission minimum; consider all historical candidates; require a fresh 48/24 gate, accounting and execution reconciliation and sufficient depth. Rank comparable capital returns by R_net / max(cycles, 15) * log(1 + cycles). Retain REAL owners until a confirmed natural flat close, except separately defined risk exits; return capital exactly once; observe the existing pause; replace at most one owner per supervisor cycle. Two slots require joint account validation and can remain empty.

Implementation gate: test recorded PAPER first. Do not activate REAL, lower the minimum to manufacture trades, treat historical closed winners as current candidates, or invent risk thresholds. Sensitivity cutoffs derived from observed PAPER risk are research only. Existing ON/HOT 48/36 research is a separate experiment and cannot validate this full-pool 48/24 policy.

Scope: a read-only snapshot and causal eligibility study, focused tests, and a reproducible report. Production selection, PAPER economics, processes, allocation ledgers and configuration remain untouched until the study supports a concrete policy. Actual counterfactual returns require market and execution data beyond observed PAPER outcomes; any absent inputs must be reported explicitly.

## Completed PAPER evidence study

Final snapshot: 2026-11-20 18:57:11.145208 UTC. Sources are read-only runtime SQLite and sequential copies of PAPER archives, current states and logs. The SQLite backup is consistent; the complete filesystem snapshot is not globally atomic. Source hashes are preserved.

Final artifacts: `reports/real_selection_paper_20261120_final/{manifest.json,results.json,verification.json,tests.log,snapshot/}`. Reproduction: bundled Python 3.12, `-B scripts/study_real_selection_paper.py --runtime <runtime-root> --output <new-empty-output>`. Offline analysis reads only the frozen snapshot after capture. No PM2 command, account API call, trade, restart or runtime state mutation is used.

The final snapshot contains 18 confirmed returned sessions, across the current 8,000-USDT capital pool. All 18 closed returns are profitable: aggregate realized ledger Net **399.05448377 USDT**. Open state-marked Net is **-317.23874350344124 USDT**. This is not executable exit Net, and adding it is an indicative marked result rather than verified liquidation equity. The eighteenth closed session is ONUSDT, +25.50825779 USDT. Earlier reports of 17 were accurate for their earlier snapshots.

| Proposal | Actual test and outcome | Evidence limit |
|---|---|---|
| Current-session vs cumulative cycles | 51,542 recorded heartbeat/rebalance/return events, processed in time order without terminal-cycle lookahead; no qualifying ticker in either variant | Missing log intervals are not reconstructed; this is observed-event eligibility, not a full native-engine counterfactual |
| Preserve 15 cycles and positive cumulative Net | No current candidate passes. BTW has 17 cycles but negative cumulative reported Net; BULLA has 10 cumulative cycles and positive Net | Actual BID/ASK exit fees would still be required; insufficient inputs block full admission |
| Full historical pool | All 17 distinct tickers in this pool evaluated, including inactive historical owners | Contemporary scanner eligibility and fresh gate results were not archived for every decision |
| Comparable-capital score | Uses the sum of each session Net / its allocated capital, then the approved cycle factor; focused test confirms scaling invariance | Fixed reference capital interpretation, not compounded reinvestment; no claim of a calibrated optimal score |
| Drawdown limits | Evaluated every observed session-DD breakpoint; detailed sensitivity in results.json | Descriptive research cutoffs, not approved or optimized policy; DD is a lower bound from sampled marks |
| Directional exposure / margin limits | Not certified | Historical order books and joint REAL collateral/margin trajectory are absent; PAPER margins cannot substitute for exchange account margin |
| Natural-close rotation / one replacement / pause | No observed eligible admission, so subsequent REAL transitions are unidentifiable on this pool under the retained threshold | Zero observations cannot establish superiority, holding-period PnL, safe liquidation, or two-slot capacity |
| Fresh 48/24 gate and parity | Remains a prerequisite | The separate BTW study demonstrated final-candle versus observed-price divergence; no full-pool parity correction is claimed |

Observed closed-session maximum sampled DD: 8.1225% (BULLA). Open BTW sampled DD: 34.143%; logs contain gaps, including approximately 9.48 hours in some earlier sessions. Selecting limits from only profitable closed sessions would exclude the open-loss tail and introduce selection bias. No numerical REAL risk limit is justified by this short record. BULLA and AKE remain research shortlist names, not admitted REAL owners.

Six focused tests PASS on the final script, including duplicate rejection, session-boundary accumulation, loss rejection, capital normalization, receipt/capital checks, post-close log exclusion and absence of terminal-cycle lookahead. Command: `python -B tests/test_real_selection_study.py`; exit 0. Independent Decimal ledger reconstruction confirms 18 returns / 399.05448377 Net; all snapshot SHA-256 hashes match. Research run exit 0. Native production REAL integration and two-owner shared-margin execution have not been tested or deployed by this study.

Conclusion: apply the approved rules in research, keep the 15-cycle minimum, record all historical candidates, and leave REAL slots empty until there is an actual qualified candidate. Continue accumulation and preserve decision-time scan/gate inputs, executable quote/depth evidence, full strategy configuration provenance and account-risk inputs before a meaningful paired REAL replay. The production supervisor and live configuration have not been modified. The study cannot honestly produce counterfactual REAL profits or defensible numeric limits from the missing inputs.
