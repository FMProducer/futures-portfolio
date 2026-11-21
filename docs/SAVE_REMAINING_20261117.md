# Saved local work: integration provenance

Integration branch: `chore/save-remaining-20261117`, based on `origin/master` at
`fb59b5b617ee2e16ce60fba1e41c7588240e3502`. The user explicitly authorized commit,
push and merge of the remaining local work.

## Preserved commits

- `43f6b9b`: the previously committed admission transport mock fix.
- `227444c`: original `FMProducer/futures_portfolio` worktree's four-fix candidate
  and accompanying tests, including `tests/test_bugfix_four_verification.py`.
- `62715c0`: production config snapshot; only the current ticker list changed.
- `dc2531e`: OpenCode worktree's matching mock fix. Integrating this commit changed
  history only: the integration tree hash was identical before and after its merge.

No reset/restore/clean/stash was used to remove the original work. The source
workspace diff, status, HEAD and untracked verification test were backed up under
its local `reports/save_remaining_20261117/`. Production config was copied to
its local report directory before being committed. Runtime selection may update
the config again after this snapshot; that is not loss of the committed version.

## Validation

Tests were run in the isolated integration checkout with the `futures_portfolio`
namespace pinned to its root, not the neighboring dirty/older workspace.

Research/fix suite, existing Python 3.13.15:
```text
python.exe -B -
pytest.main([
  tests/test_bugfix_four_verification.py,
  tests/test_backtest_scope_gap.py,
  tests/test_optimization_scope_gap.py,
  tests/test_paper_budget_gap.py,
  tests/test_runtime_surface_gap.py,
  tests/test_session_capital.py,
  tests/test_paper_budget.py,
  tests/test_backtest_gate.py,
  tests/test_dashboard_attribution.py, -q
])
```
Result: **135 passed, 1 warning in 10.74s**, exit 0. The warning comes from an
existing Optuna FixedTrial fixture outside its configured leverage range.

Runtime/network suite, production `.venv/Scripts/python.exe` (Python 3.11.9):
```text
pytest.main([
  tests/test_direct_network.py,
  tests/test_binance_runtime_network.py,
  tests/test_connector.py,
  tests/test_backtest_gate.py,
  tests/test_backtest_rebalance.py,
  tests/test_rank_tickers.py,
  tests/test_daily_universe.py,
  rebalance/scenarios/test_binance_readonly.py,
  rebalance/scenarios/test_public_session.py,
  rebalance/scenarios/test_user_stream.py, -q
])
```
Result: **218 passed in 9.68s**, exit 0, no skipped/deselected cases. The two
suites overlap at the gate tests; their counts are not summed as unique tests.

The earlier research suite in the trading venv was rejected: 121 passed,
4 failed, 2 skipped and 5 errors. That environment lacks matplotlib, and its
market-data fake lacked the direct client's session. The fake was corrected
without changing production transport; installed research dependencies were used
instead of altering the trading venv. Both final commands ran the final code.

`git diff --check` passed. No worker restart or live API operation was performed.
The optimizer's pre-existing lookback no-op is documented by a characterization
test; no trading-policy decision or real parameter optimization was made here.
