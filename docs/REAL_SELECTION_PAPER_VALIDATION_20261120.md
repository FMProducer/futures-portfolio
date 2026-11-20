# REAL selection policy: approved design and PAPER validation

Recorded 2026-11-20. User approved the proposed design, then instructed: test every proposal against accumulated PAPER trading data. Numerical REAL risk limits remain unapproved pending evidence.

Approved design: deduplicate PAPER sessions; accumulate cycles and net across session boundaries; include open losses and executable exit costs; separate incompatible strategy configurations; retain the 15-cycle admission minimum; consider all historical candidates; require a fresh 48/24 gate, accounting and execution reconciliation and sufficient depth. Rank comparable capital returns by R_net / max(cycles, 15) * log(1 + cycles). Retain REAL owners until a confirmed natural flat close, except separately defined risk exits; return capital exactly once; observe the existing pause; replace at most one owner per supervisor cycle. Two slots require joint account validation and can remain empty.

Implementation gate: test recorded PAPER first. Do not activate REAL, lower the minimum to manufacture trades, treat historical closed winners as current candidates, or invent risk thresholds. Sensitivity cutoffs derived from observed PAPER risk are research only. Existing ON/HOT 48/36 research is a separate experiment and cannot validate this full-pool 48/24 policy.

Scope: a read-only snapshot and causal eligibility study, focused tests, and a reproducible report. Production selection, PAPER economics, processes, allocation ledgers and configuration remain untouched until the study supports a concrete policy. Actual counterfactual returns require market and execution data beyond observed PAPER outcomes; any absent inputs must be reported explicitly.
