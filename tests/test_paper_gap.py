"""Gap coverage for rebalance/paper.py Runner guards (tmp databases only)."""
import json
import sqlite3
from decimal import Decimal as D

import pytest

from rebalance.durable import Store
from rebalance.kernel import Config, Portfolio
from rebalance.paper import Runner

SETTINGS = dict(capital="10000", spread_bps="2", slippage_bps="1",
                strategy=dict(step="0.001", min_notional="5.3", leverage="7", fee_rate="0.0004",
                              surplus_pp="0.7", deficit_pp="0.7", trail_pp="0.09"))


def make_rows(opens):
    rows = []
    for i, o in enumerate(opens):
        o = D(o)
        prev = D(opens[i - 1]) if i else o
        rows.append(dict(time=i * 60000, open=o, high=max(o, prev) + D("0.5"),
                         low=min(o, prev) - D("0.5"), close=o))
    return rows


def test_runner_rejects_invalid_capital_or_costs(tmp_path):
    rows = make_rows(["100", "100"])
    with pytest.raises(ValueError, match="invalid paper capital or costs"):
        Runner(tmp_path / "p.sqlite", rows, dict(SETTINGS, capital="0"))
    with pytest.raises(ValueError, match="invalid paper capital or costs"):
        Runner(tmp_path / "p.sqlite", rows, dict(SETTINGS, spread_bps="-1"))


@pytest.mark.parametrize("funding,mode", [
    ([dict(time=60000, rate="0.001", mark="100")], "ASSUMED_ZERO"),
    ([], "bogus"),
])
def test_runner_rejects_an_invalid_funding_mode(tmp_path, funding, mode):
    rows = make_rows(["100", "100"])
    with pytest.raises(ValueError, match="invalid funding source mode"):
        Runner(tmp_path / "p.sqlite", rows, SETTINGS, funding=funding, funding_mode=mode)


def test_runner_requires_at_least_two_observations(tmp_path):
    with pytest.raises(ValueError, match="at least two observations"):
        Runner(tmp_path / "p.sqlite", make_rows(["100"]), SETTINGS)


def test_runner_rejects_a_bad_funding_sequence(tmp_path):
    rows = make_rows(["100", "100"])
    for funding in ([dict(time=60000, rate="0.001", mark="0")],
                    [dict(time=-1, rate="0.001", mark="100")],
                    [dict(time=120000, rate="0.001", mark="100"), dict(time=120000, rate="0.001", mark="100")]):
        with pytest.raises(ValueError, match="invalid paper funding sequence"):
            Runner(tmp_path / "p.sqlite", rows, SETTINGS, funding=funding, funding_mode="CSV_RATES")


def test_existing_portfolio_without_a_binding_requires_reconciliation(tmp_path):
    db = tmp_path / "p.sqlite"
    with Runner(db, make_rows(["100", "100"]), SETTINGS):
        pass
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER paper_binding_no_delete")
        conn.execute("DELETE FROM paper_binding")
    with pytest.raises(ValueError, match="paper state requires reconciliation"):
        Runner(db, make_rows(["100", "100"]), SETTINGS)


def test_step_rejects_an_invalid_index(tmp_path):
    runner = Runner(tmp_path / "p.sqlite", make_rows(["100", "100"]), SETTINGS)
    try:
        with pytest.raises(ValueError, match="invalid paper observation index"):
            runner.step(5)
        with pytest.raises(ValueError, match="invalid paper observation index"):
            runner.step("0")
    finally:
        runner.close()


def test_step_detects_a_diverged_cursor_price(tmp_path):
    db = tmp_path / "p.sqlite"
    runner = Runner(db, make_rows(["100", "101"]), SETTINGS)
    runner.step(0)
    runner.close()
    with sqlite3.connect(db) as conn:
        body = json.loads(conn.execute("SELECT body FROM paper_cursor WHERE id=1").fetchone()[0])
        body["previous_close"] = "1"
        conn.execute("UPDATE paper_cursor SET body=? WHERE id=1", (json.dumps(body),))
    with Runner(db, make_rows(["100", "101"]), SETTINGS) as reopened:
        with pytest.raises(ValueError, match="cursor/price mismatch"):
            reopened.step(1)


def test_step_rejects_an_external_execution_intent(tmp_path):
    db = tmp_path / "p.sqlite"
    runner = Runner(db, make_rows(["100", "101"]), SETTINGS)
    runner.step(0)
    with runner.store.connection() as conn:
        conn.execute("INSERT INTO orders VALUES('external', ?)", (json.dumps({}),))
    try:
        with pytest.raises(ValueError, match="external execution intent in paper database"):
            runner.step(1)
    finally:
        runner.close()


def test_step_reports_collateral_exhaustion(tmp_path):
    db = tmp_path / "p.sqlite"
    rows = make_rows(["100", "100", "100"])
    funding = [dict(time=120000, rate="100", mark="100")]
    runner = Runner(db, rows, SETTINGS, funding=funding, funding_mode="CSV_RATES")
    runner.step(0)
    runner.close()
    with sqlite3.connect(db) as conn:
        body = json.loads(conn.execute("SELECT body FROM portfolio WHERE id=1").fetchone()[0])
        body["portfolio"]["legs"]["SHORT"] = dict(units=0, initial_units=0, avg="0", margin="0", open_fees="0")
        conn.execute("UPDATE portfolio SET body=? WHERE id=1", (json.dumps(body),))
    with Runner(db, rows, SETTINGS, funding=funding, funding_mode="CSV_RATES") as reopened:
        with pytest.raises(ValueError, match="paper collateral exhausted"):
            reopened.run()


@pytest.mark.parametrize("max_bars", [0.5, -1])
def test_run_rejects_an_invalid_max_bars(tmp_path, max_bars):
    runner = Runner(tmp_path / "p.sqlite", make_rows(["100", "101"]), SETTINGS)
    try:
        with pytest.raises(ValueError, match="nonnegative integer"):
            runner.run(max_bars)
    finally:
        runner.close()
