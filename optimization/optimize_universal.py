"""Offline Pareto search. Run: python -m optimization.optimize_universal --days 30.

Writes a new JSON report; never writes the source config. No live downloads.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import re
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
P = "portfolios.0."


def get_path(config, path):
    value = config
    for key in path.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def set_path(config, path, value):
    keys = path.split(".")
    parent = get_path(config, ".".join(keys[:-1])) if len(keys) > 1 else config
    key = int(keys[-1]) if isinstance(parent, list) else keys[-1]
    parent[key] = value


def search_space(config):
    # Existing optimizers disagree; use their combined bounds, without new policy constraints.
    space = {
        "max_drawdown_limit": ("int", 5, 50),
        "equity_trailing_stop_pct": ("float", 0.0001, 25.0),
        "equity_trailing_stop_activation_pct": ("float", 0.5, 20.0),
        P + "rebalance_threshold_surplus": ("float", 0.008, 0.01),
        P + "rebalance_threshold_deficit": ("float", 0.01, 0.12),
        P + "safety_guards.trend_min_move_pct": ("float", 0.49, 0.91),
        P + "safety_guards.trend_eff_threshold": ("float", 0.32, 0.59),
        P + "safety_guards.net_move_block_pct": ("float", 1.54, 2.86),
        P + "safety_guards.net_move_window_sec": ("int", 42, 78),
        P + "safety_guards.trend_lookback_bars": ("int", 10, 60),
        P + "targets.BASE_LONG.leverage": ("int", 3, 10),
        P + "targets.BASE_SHORT.leverage": ("int", 3, 10),
        P + "fuse_enabled": ("categorical", False, True),
    }
    for path in (
        "rebalance_share_trail_distance", "equity_trailing_stop_timeout_sec",
        "toxic_cooldown_days", "max_drawdown_cooldown_days",
        P + "trend_add_block.pct", P + "trend_add_block.window_bars",
        P + "safety_guards.max_price_velocity_pct", P + "safety_guards.velocity_window_sec",
        P + "liquidation_distance_crit_pct", P + "siphoning_threshold_pct",
    ):
        value = get_path(config, path)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid numeric setting: {path}")
        kind = "int" if isinstance(value, int) else "float"
        low, high = value * 0.5, value * 1.5
        space[path] = (kind, math.ceil(low), math.floor(high)) if kind == "int" else (kind, low, high)
    return space


def candidate(config, params):
    result = copy.deepcopy(config)
    for path, value in params.items():
        set_path(result, path, value)
    targets = result["portfolios"][0]["targets"]
    for leg, share in (("BASE_LONG", 0.25), ("BASE_SHORT", 0.25), ("VIRTUAL", 0.5)):
        targets[leg]["share"] = share
    targets["VIRTUAL"]["leverage"] = 1
    result["portfolios"][0]["ticker_thresholds"] = {}
    return result


def discover(data_dir):
    selected = {}
    for path in sorted(data_dir.glob("*.feather")):
        match = re.fullmatch(r"([A-Z0-9]+USDT)(?:_.*)?\.feather", path.name)
        if match:
            ticker = match[1]
            if ticker not in selected or (path.stat().st_size, path.name) > (selected[ticker].stat().st_size, selected[ticker].name):
                selected[ticker] = path
    if not selected:
        raise ValueError(f"No USDT ticker Feather files in {data_dir}")
    return selected


def metrics(result):
    if not result or result.get("accounting_ok") is not True or result.get("cash_invariant_violation"):
        raise ValueError("Backtest failed or accounting validation failed")
    capital = result["initial_capital"]
    net = (result["final_equity"] / capital - 1) * 100
    dd = result["max_dd_pct"]
    liqs = result["liquidations"]
    if capital <= 0 or not all(math.isfinite(x) for x in (net, dd, liqs)) or dd < 0 or liqs < 0:
        raise ValueError("Invalid backtest metrics")
    return {"net_pct": net, "max_dd_pct": dd, "liquidations": liqs,
            "cycles": result["cycles"], "data_window": result["data_window"]}


def aggregate(rows):
    if not rows:
        raise ValueError("No ticker results")
    if any(row["max_dd_pct"] > 50 or row["liquidations"] != 0 for row in rows):
        raise ValueError("Infeasible candidate: MaxDD > 50% or modeled liquidation")
    return statistics.median(row["net_pct"] for row in rows), max(row["max_dd_pct"] for row in rows)


def fixed_settings(config, space, prefix=""):
    rows = {}
    items = enumerate(config) if isinstance(config, list) else config.items()
    for key, value in items:
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, (dict, list)) and value:
            rows.update(fixed_settings(value, space, path))
        elif path not in space:
            rows[path] = value
    return rows


def prepare_history(frame, days, path):
    import pandas as pd

    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing OHLCV columns {sorted(missing)}: {path}")
    bars = int(days * 1440)
    if len(frame) < bars:
        return None, {"reason": "insufficient_history", "available_bars": len(frame),
                      "required_bars": bars, "file": str(path)}
    tail = frame.iloc[-bars:]
    metadata = {"bars": len(tail), "start": None, "end": None,
                "time_source": "mtime", "minute_continuity_verified": False}
    if "time" in frame.columns:
        times = pd.to_datetime(frame["time"], utc=True)
        if times.isna().any() or not times.is_monotonic_increasing or times.duplicated().any():
            raise ValueError(f"Invalid time axis: {path}")
        if not times.iloc[-bars:].diff().dropna().eq(pd.Timedelta(minutes=1)).all():
            raise ValueError(f"Non-contiguous minute history: {path}")
        metadata.update(start=str(times.iloc[-bars]), end=str(times.iloc[-1]),
                        time_source="feather_time", minute_continuity_verified=True)
    values = tail[["open", "high", "low", "close", "volume"]]
    if not all(math.isfinite(float(x)) for x in values.to_numpy().flat) or (values[["open", "high", "low", "close"]] <= 0).any().any() or (values["volume"] < 0).any():
        raise ValueError(f"Invalid OHLCV values: {path}")
    return tail, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, required=True)
    parser.add_argument("--n-trials", type=int, default=150)
    args = parser.parse_args()
    if not math.isfinite(args.days) or args.days * 1440 < 2 or args.n_trials < 1:
        parser.error("--days must cover at least two minutes; --n-trials must be positive")

    import optuna
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest

    source = PROJECT_DIR / "config.json"
    source_bytes = source.read_bytes()
    config = json.loads(source_bytes)
    space = search_space(config)
    files = discover(PROJECT_DIR / "data")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = PROJECT_DIR / "optimization" / f"universal_report_{stamp}.json"
    report = {
        "created_utc": stamp, "config_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_config": config, "days": args.days, "search_space": space,
        "fixed_settings": fixed_settings(candidate(config, {}), space),
        "objectives": ["maximize median net_pct", "minimize worst ticker max_dd_pct"],
        "constraints": {"max_dd_pct_each_ticker": 50, "modeled_liquidations": 0},
        "fixed_policy": {"shares": [0.25, 0.25, 0.5], "ticker_thresholds": {},
                         "other_fields": "Unchanged from source_config; not all are modeled by run_backtest"},
        "limitations": ["In-sample search; no holdout validation", "Independent ticker replays, not a swarm simulation",
                        "Minute OHLCV, simulated market fills and funding; no historical bid/ask",
                        "Largest file per ticker, separate trailing windows; see per-ticker data_window",
                        "Supervisor, rotation, limit execution, spread and live margin ratio settings are not modeled"],
        "data": {}, "excluded_tickers": {}, "trials": [], "pareto": [],
    }

    def save():
        if source.read_bytes() != source_bytes:
            raise RuntimeError("Source config changed externally during optimization")
        staging = output.with_suffix(".tmp")
        staging.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        staging.replace(output)

    with tempfile.TemporaryDirectory(prefix="universal_", dir=PROJECT_DIR / "optimization") as tmp:
        tmp = Path(tmp)
        data = tmp / "data"
        data.mkdir()
        for ticker, path in files.items():
            frame = pd.read_feather(path)
            tail, metadata = prepare_history(frame, args.days, path)
            if tail is None:
                report["excluded_tickers"][ticker] = metadata
                print(f"Skipping {ticker}: {metadata['available_bars']} bars, need {metadata['required_bars']}")
                continue
            destination = data / f"{ticker}_live.feather"
            tail.to_feather(destination)
            source_stat = path.stat()
            os.utime(destination, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            report["data"][ticker] = {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "source_mtime_ns": source_stat.st_mtime_ns, **metadata}
        save()
        if not report["data"]:
            raise ValueError(f"No tickers have enough history for {args.days} days. Exclusions: {output}")
        print(f"Eligible tickers: {len(report['data'])}; excluded: {len(report['excluded_tickers'])}; report: {output}")
        study = optuna.create_study(directions=["maximize", "minimize"], sampler=optuna.samplers.NSGAIISampler(seed=42))

        def objective(trial):
            params = {}
            for path, (kind, low, high) in space.items():
                params[path] = trial.suggest_categorical(path, [low, high]) if kind == "categorical" else getattr(trial, f"suggest_{kind}")(path, low, high)
            cfg = candidate(config, params)
            trial.set_user_attr("candidate_config", cfg)
            cfg_path = tmp / "candidate.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            rows = {}
            for ticker in report["data"]:
                result = asyncio.run(run_backtest(str(cfg_path), str(data), live_mode=False,
                                                 ticker_override=ticker, days=args.days, quiet=True))
                try:
                    rows[ticker] = metrics(result)
                except (ValueError, KeyError, ZeroDivisionError) as exc:
                    trial.set_user_attr("rejected", f"{ticker}: {exc}")
                    trial.set_user_attr("per_ticker", rows)
                    raise optuna.TrialPruned(str(exc)) from exc
            trial.set_user_attr("per_ticker", rows)
            try:
                return aggregate(list(rows.values()))
            except ValueError as exc:
                trial.set_user_attr("rejected", str(exc))
                raise optuna.TrialPruned(str(exc)) from exc

        def checkpoint(study, trial):
            report["trials"].append({"number": trial.number, "state": trial.state.name,
                                     "values": trial.values, "params": trial.params, **trial.user_attrs})
            report["pareto"] = [t.number for t in study.best_trials]
            save()

        try:
            study.optimize(objective, n_trials=args.n_trials, n_jobs=1, callbacks=[checkpoint])
        finally:
            save()
    print(f"Report: {output}; Pareto trials: {report['pareto']}")


if __name__ == "__main__":
    main()
