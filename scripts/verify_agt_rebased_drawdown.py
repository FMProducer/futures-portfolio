"""Full strategy replay for a selected research profile, without editing source."""
import argparse
import asyncio
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / ".venv/Lib/site-packages"))
import backtest.backtest_rebalance as bt


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor", choices=("equity", "rebased", "ledger"), required=True)
    parser.add_argument("--measure", choices=("equity", "rebased", "ledger"), default="equity")
    parser.add_argument("--horizon", choices=("persistent", "session"), default="persistent")
    parser.add_argument("--stage", choices=("before_orders", "after_orders"), default="after_orders")
    parser.add_argument("--limit", type=float, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", default=str(ROOT / "docs/research/agt_rebased_ledger_2_08/config.json"))
    args = parser.parse_args()
    out = ROOT / "reports/agt_drawdown_research"
    out.mkdir(exist_ok=True)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    config["max_drawdown_limit"] = args.limit
    config_path = out / f"{args.name}_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    source = inspect.getsource(bt.run_backtest)
    assert source.count('            dd_equity = state.get_tpv_fast(float(mid_price)) + float(state.siphoning_reserve)') == 1, "Drawdown source changed; review the research patch"
    assert source.count('            max_dd_ath = max(max_dd_ath, dd_equity)') == 1, "Peak source changed; review the research patch"
    expressions = {"equity": "state.get_tpv_fast(float(mid_price)) + float(state.siphoning_reserve)",
                   "ledger": "float(state.get_tpv(mid_price) + state.siphoning_reserve - state.unrealized(mid_price))",
                   "rebased": "float(state.initial_capital + state.siphoning_reserve)"}
    source = source.replace('            dd_equity = ' + expressions["equity"],
                            '            dd_equity = ' + expressions[args.measure])
    peak_expression = f'max(max_dd_ath, {expressions[args.anchor]})'
    if args.horizon == "session":
        if args.anchor == "rebased":
            peak_expression = expressions["rebased"]
        else:
            source = source.replace('                state.initial_capital = active_restart_capital',
                                    '                max_dd_ath = float(active_restart_capital)\n'
                                    '                state.initial_capital = active_restart_capital')
    source = source.replace('            max_dd_ath = max(max_dd_ath, dd_equity)',
                            '            max_dd_ath = ' + peak_expression)
    if args.stage == "before_orders":
        start = source.index('            # Preserve the total-equity peak')
        end = source.index('            # --- 2.5 Unified Predictive', start)
        block = source[start:end]
        source = source[:start] + source[end:]
        at = source.index('            # --- 2. Portfolio Calculation ---')
        source = source[:at] + block + source[at:]
    exec(compile(source, bt.__file__, "exec"), bt.__dict__)
    bt.logger.disabled = True
    result = await bt.run_backtest(str(config_path), str(ROOT / "data"), ticker_override="AGTUSDT",
                                   quiet=True, since_utc="2025-10-20 00:00", until_utc="2026-10-19 23:59")
    assert result and result["accounting_ok"]
    result.pop("equity_curve")
    (out / f"{args.name}_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    stops = [e for e in result["events"] if e["kind"] == "max_dd_stop"]
    print(json.dumps({"name": args.name, "profile": vars(args), "final_equity": result["final_equity"],
                      "profit_pct": result["profit_pct"], "liquidations": result["liquidations"],
                      "max_dd_stop": stops, "accounting_ok": result["accounting_ok"]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
