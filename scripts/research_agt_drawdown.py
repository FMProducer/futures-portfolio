"""Offline first-stop envelopes; no source/config mutations or exchange access."""
import asyncio
import hashlib
import inspect
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / ".venv/Lib/site-packages"))
import numpy as np
import backtest.backtest_rebalance as bt

OUT = ROOT / "reports/agt_drawdown_research"
OUT.mkdir(exist_ok=True)
records = {"before_orders": [], "after_orders": []}
session = 0
original_event = bt.BacktestState.log_event


def log_event(self, bar, kind, *args, **kwargs):
    global session
    if kind == "cooldown_end":
        session += 1
    return original_event(self, bar, kind, *args, **kwargs)


def observe(stage, loc):
    state, price = loc["state"], loc["mid_price"]
    equity = state.get_tpv(price) + state.siphoning_reserve
    ledger = equity - state.unrealized(price)
    sim = loc["sim"]
    sell, buy = sim.execution_price("SELL", price), sim.execution_price("BUY", price)
    cost = (state.pos_long * (price - sell) + state.pos_short * (buy - price)
            + (state.pos_long * sell + state.pos_short * buy) * sim.commission_pct)
    assert cost >= 0
    records[stage].append((loc["i"], session, float(equity), float(ledger),
                           float(state.initial_capital + state.siphoning_reserve),
                           float(equity - cost), float(price)))


def analyze(stage, rows, funding_by_bar, initial):
    a = np.asarray(rows, dtype=float)
    np.savez_compressed(OUT / f"{stage}.npz", samples=a)
    bars, sessions = a[:, 0].astype(int), a[:, 1].astype(int)
    groups = np.r_[0, np.flatnonzero(np.diff(sessions)) + 1, len(a)]
    sources = {"equity": a[:, 2], "ledger": a[:, 3], "rebased": a[:, 4]}
    later_funding = np.cumsum(funding_by_bar[::-1])[::-1]
    funding_after_stop = later_funding[bars] - funding_by_bar[bars]
    final_closed = a[:, 5] + funding_after_stop
    results = []
    for anchor, values in sources.items():
        for horizon in ("persistent", "session"):
            peak = np.maximum.accumulate(np.maximum(values, initial))
            if horizon == "session":
                for lo, hi in zip(groups[:-1], groups[1:]):
                    peak[lo:hi] = np.maximum.accumulate(np.maximum(values[lo:hi], a[lo, 4]))
            for measure, current in sources.items():
                loss = peak - current
                # Only new record losses can be the first crossing of a static budget.
                record = np.maximum.accumulate(np.maximum(loss, 0))
                indices = np.flatnonzero(np.r_[True, np.diff(record) > 1e-9])
                valid = indices[loss[indices] >= 0]
                if not len(valid):
                    continue
                best = int(valid[np.argmax(final_closed[valid])])

                def point(index):
                    j = int(index)
                    return {"bar": int(bars[j]), "peak": float(peak[j]),
                            "measured": float(current[j]), "equity": float(a[j, 2]),
                            "rebased": float(a[j, 4]), "price": float(a[j, 6]),
                            "closed_equity": float(a[j, 5]),
                            "final_equity": float(final_closed[j]),
                            "profit_usdt": float(final_closed[j] - initial),
                            "loss_usdt": float(loss[j])}

                row = {"stage": stage, "anchor": anchor, "horizon": horizon, "measure": measure,
                       "policy_matches_fixed_equity_stop": measure == "equity",
                       "limits": {}}
                for pct in (100, 700):
                    crossings = np.flatnonzero(loss >= initial * pct / 100)
                    row["limits"][str(pct)] = point(crossings[0]) if len(crossings) else None
                preceding = record[best - 1] if best > 0 else 0
                row["best_static_limit_hindsight"] = {
                    **point(best), "limit_pct_lower_exclusive": float(preceding / initial * 100),
                    "limit_pct_upper_inclusive": float(loss[best] / initial * 100)}
                row["envelope"] = [point(j) for j in valid]
                results.append(row)
    return results


async def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    initial = float(config["portfolios"][0]["initial_capital"])
    assert config["max_drawdown_cooldown_days"] >= 365
    assert config["funding_model"] == "signed_actual"
    assert not config["portfolios"][0].get("siphoning_threshold_pct", 0)
    bt.BacktestState.log_event = log_event
    source = inspect.getsource(bt.run_backtest)
    source = source.replace('            # --- 2. Portfolio Calculation ---',
                            '            _research_observe("before_orders", locals())\n'
                            '            # --- 2. Portfolio Calculation ---')
    source = source.replace('            # Preserve the total-equity peak across unrelated session restarts.',
                            '            _research_observe("after_orders", locals())\n'
                            '            # Preserve the total-equity peak across unrelated session restarts.')
    bt.__dict__["_research_observe"] = observe
    bt.drawdown_threshold = lambda *args: float("-inf")
    exec(compile(source, bt.__file__, "exec"), bt.__dict__)
    logging.getLogger("Backtest").setLevel(logging.ERROR)
    result = await bt.run_backtest(str(ROOT / "config.json"), str(ROOT / "data"),
                                   ticker_override="AGTUSDT", quiet=True,
                                   since_utc="2025-10-20 00:00", until_utc="2026-10-19 23:59")
    assert result is not None and result["accounting_ok"]
    funding = np.zeros(len(result["equity_curve"]))
    for event in result["events"]:
        if event["kind"] == "funding":
            funding[event["bar"]] += event["funding_amount"]
    variants = [row for stage, rows in records.items()
                for row in analyze(stage, rows, funding, initial)]
    current = next(v for v in variants if (v["stage"], v["anchor"], v["horizon"], v["measure"])
                   == ("after_orders", "equity", "persistent", "equity"))
    assert current["limits"]["100"]["bar"] == 243375
    assert current["limits"]["700"]["bar"] == 485910
    assert abs(current["limits"]["700"]["final_equity"] - 23242.21313284558) < 1e-6
    summary = {"config_sha256": hashlib.sha256((ROOT / "config.json").read_bytes()).hexdigest(),
               "source_sha256": hashlib.sha256((ROOT / "backtest/backtest_rebalance.py").read_bytes()).hexdigest(),
               "assumptions": ["First stop only; 365-day cooldown covers remaining period",
                               "Fixed signed funding scenario is unchanged after flat close",
                               "Execution at observed bar close with original commission/slippage",
                               "Static-limit optimum is hindsight, not a deployable recommendation"],
               "baseline": {k: result[k] for k in ("final_equity", "profit_pct", "liquidations", "accounting_ok")},
               "variants": variants}
    (OUT / "research.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for v in variants:
        print(v["stage"], v["anchor"], v["horizon"], v["measure"],
              {p: (round(s["final_equity"], 2), s["bar"]) if s else None for p, s in v["limits"].items()},
              "best", round(v["best_static_limit_hindsight"]["final_equity"], 2), flush=True)
    print("RESEARCH PASS", len(variants), "families", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
