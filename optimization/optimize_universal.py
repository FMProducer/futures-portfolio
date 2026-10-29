"""Offline Pareto search. Run: python -m optimization.optimize_universal --days 1.

Resumes compatible SQLite studies and writes reports, HTML plots and a recommended
config without changing the source config. n-trials adds trials on each invocation.
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
from collections import Counter
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
        "leverage": ("int", 3, 10),
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
        if path != "leverage":
            set_path(result, path, value)
    targets = result["portfolios"][0]["targets"]
    leverage = params.get("leverage", targets["BASE_LONG"]["leverage"])
    if isinstance(leverage, bool) or not isinstance(leverage, int) or not 3 <= leverage <= 10:
        raise ValueError("Shared leverage must be an integer from 3 to 10")
    for leg in ("BASE_LONG", "BASE_SHORT"):
        targets[leg]["leverage"] = leverage
    for leg, share in (("BASE_LONG", 0.25), ("BASE_SHORT", 0.25), ("VIRTUAL", 0.5)):
        targets[leg]["share"] = share
    targets["VIRTUAL"]["leverage"] = 1
    result["portfolios"][0]["ticker_thresholds"] = {}
    return result


def load_optimizer_settings(config, path):
    settings = json.loads(path.read_text(encoding="utf-8"))
    defaults = search_space(config)
    parameters = settings["parameters"]
    if set(parameters) != set(defaults):
        raise ValueError(f"Optimizer parameter keys differ: missing={set(defaults) - set(parameters)}, unknown={set(parameters) - set(defaults)}")
    space, fixed, steps = {}, {}, {}
    for name, spec in parameters.items():
        kind = defaults[name][0]
        if type(spec["enabled"]) is not bool or spec["type"] != kind:
            raise ValueError(f"Invalid enabled/type for {name}")
        value = spec["value"]
        if kind == "categorical":
            choices = spec["choices"]
            if not choices or any(type(x) is not bool for x in choices) or type(value) is not bool:
                raise ValueError(f"Boolean choices/value required for {name}")
            if len(set(choices)) != len(choices):
                raise ValueError(f"Duplicate choices for {name}")
            bounds = (kind, choices[0], choices[-1])
        else:
            low, high = spec["low"], spec["high"]
            numbers = (low, high, value)
            if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in numbers):
                raise ValueError(f"Finite nonnegative numbers required for {name}")
            if kind == "int" and any(int(x) != x for x in numbers):
                raise ValueError(f"Integers required for {name}")
            if kind == "int":
                low, high, value = int(low), int(high), int(value)
            if low > high or (name == "leverage" and not (3 <= low <= high <= 10 and 3 <= value <= 10)):
                raise ValueError(f"Invalid range/value for {name}")
            step = spec.get("step")
            if step is not None:
                if type(step) not in (int, float) or not math.isfinite(step) or step <= 0 or (kind == "int" and type(step) is not int):
                    raise ValueError(f"Invalid step for {name}")
                if not math.isclose((high - low) / step, round((high - low) / step), abs_tol=1e-8):
                    raise ValueError(f"Range must be divisible by step for {name}")
                steps[name] = step
            bounds = (kind, low, high)
        if spec["enabled"]:
            space[name] = bounds
        else:
            fixed[name] = value
    if not space:
        raise ValueError("Enable at least one optimizer parameter")
    return space, fixed, steps, settings


def discover(data_dir, recursive=False):
    selected = {}
    paths = data_dir.rglob("*.feather") if recursive else data_dir.glob("*.feather")
    for path in sorted(paths):
        if not path.is_file():
            continue
        match = re.fullmatch(r"([A-Z0-9]+USDT)(?:_.*)?\.feather", path.name)
        if match:
            ticker = match[1]
            if ticker not in selected or (path.stat().st_size, str(path)) > (selected[ticker].stat().st_size, str(selected[ticker])):
                selected[ticker] = path
    if not selected:
        raise ValueError(f"No USDT ticker Feather files in {data_dir}")
    return selected


def metrics(result):
    if result and result.get("status") == "portfolio_default":
        raise ValueError("Infeasible candidate: portfolio default (TPV <= 0)")
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
    if any(row["max_dd_pct"] > 100 or row["liquidations"] != 0 for row in rows):
        raise ValueError("Infeasible candidate: MaxDD > 100% or modeled liquidation")
    return statistics.median(row["net_pct"] for row in rows), max(row["max_dd_pct"] for row in rows)


def fixed_settings(config, space, prefix=""):
    rows = {}
    items = enumerate(config) if isinstance(config, list) else config.items()
    for key, value in items:
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, (dict, list)) and value:
            rows.update(fixed_settings(value, space, path))
        elif path not in space and path not in (P + "targets.BASE_LONG.leverage", P + "targets.BASE_SHORT.leverage"):
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


def write_json(path, value):
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    staging.replace(path)


def load_base_config(path, project_config):
    if path.resolve() == project_config.resolve():
        raise ValueError("Optimizer base config must be separate from the project config.json")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = project_config.read_bytes()
        json.loads(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            raw = path.read_bytes()
    return json.loads(raw), raw


def study_contract(report):
    contract = {key: report[key] for key in (
        "config_sha256", "days", "search_space", "objectives", "constraints", "data", "code_sha256",
    )}
    contract["optimizer_settings"] = report.get("optimizer_settings")
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def open_study(storage, name, fingerprint):
    import optuna

    backend = optuna.storages.RDBStorage(storage)
    study = optuna.create_study(storage=backend, study_name=name, load_if_exists=True,
                                directions=["maximize", "minimize"],
                                sampler=optuna.samplers.NSGAIISampler(seed=42))
    recorded = study.user_attrs.get("contract_sha256")
    if recorded != fingerprint:
        if recorded is not None or study.trials:
            backend.remove_session()
            backend.engine.dispose()
            raise ValueError("Study inputs differ: use a new study name or restore the original inputs")
        study.set_user_attr("contract_sha256", fingerprint)
    if study.trials:
        # Reusing seed 42 on every process restart repeats the startup candidates.
        study.sampler.reseed_rng()
    return study


def update_trials(report, study):
    report["trials"] = [{"number": t.number, "state": t.state.name, "values": t.values,
                         "params": t.params, **t.user_attrs} for t in study.trials]
    report["pareto"] = [t.number for t in study.best_trials]


def export_results(report, study, output):
    update_trials(report, study)
    complete = [t for t in report["trials"] if t["state"] == "COMPLETE"]
    # Net is the primary user objective; lower drawdown breaks equal-Net ties.
    best = min(complete, key=lambda t: (-t["values"][0], t["values"][1], t["number"])) if complete else None
    stamp = report["created_utc"]
    report["artifacts"] = {"json_report": str(output)}
    report["recommendation"] = None
    if best:
        rows = best["per_ticker"]
        report["recommendation"] = {
            "trial": best["number"], "median_net_pct": best["values"][0],
            "worst_max_dd_pct": best["values"][1],
            "positive_tickers": sum(v["net_pct"] > 0 for v in rows.values()),
            "negative_tickers": sum(v["net_pct"] < 0 for v in rows.values()),
            "worst_net_pct": min(v["net_pct"] for v in rows.values()),
            "modeled_liquidations": sum(v["liquidations"] for v in rows.values()),
            "selection": "Highest median Net, then lowest worst MaxDD, then earliest trial",
        }
        config_path = output.parents[1] / f"best_netdd_config_{stamp}.json"
        cfg = best["candidate_config"]
        targets = cfg["portfolios"][0]["targets"]
        if targets["BASE_LONG"]["leverage"] != targets["BASE_SHORT"]["leverage"]:
            raise ValueError("Refusing to export unequal LONG/SHORT leverage")
        write_json(config_path, cfg)
        report["artifacts"]["recommended_config"] = str(config_path)

    visuals = output.parent / f"optuna_visuals_{stamp}"
    report["visualization_errors"] = []
    if complete:
        try:
            import optuna.visualization as vis
            from optuna.importance import PedAnovaImportanceEvaluator
            import plotly.graph_objects as go

            visuals.mkdir(exist_ok=True)
            varying = [name for name, (_, low, high) in report["search_space"].items() if low != high]
            contour_params = [name for name in ("leverage", "equity_trailing_stop_pct", *varying) if name in varying]
            contour_params = list(dict.fromkeys(contour_params))[:2]
            for index, label in ((0, "median_net_pct"), (1, "worst_max_dd_pct")):
                target = lambda t, i=index: t.values[i]
                # PED-ANOVA assumes smaller targets are better, including for custom targets.
                importance_target = lambda t, i=index: -t.values[0] if i == 0 else t.values[1]
                plots = {
                    f"optimization_history_{label}": lambda: vis.plot_optimization_history(study, target=target, target_name=label),
                    f"param_importances_{label}": lambda: vis.plot_param_importances(
                        study, target=importance_target, target_name=label,
                        evaluator=PedAnovaImportanceEvaluator(evaluate_on_local=False)),
                    f"parallel_{label}": lambda: vis.plot_parallel_coordinate(study, params=varying, target=target, target_name=label),
                    f"contour_{label}": lambda: vis.plot_contour(study, params=contour_params, target=target, target_name=label),
                }
                for name, build in plots.items():
                    try:
                        fig = build()
                        fig.write_html(str(visuals / f"{name}.html"), include_plotlyjs=True)
                    except Exception as exc:
                        report["visualization_errors"].append(f"{name}: {exc}")
            fig = go.Figure()
            fig.add_scatter(x=[t["values"][1] for t in complete], y=[t["values"][0] for t in complete],
                            text=[f"Trial {t['number']}" for t in complete], mode="markers", name="Feasible trials")
            front = sorted(study.best_trials, key=lambda t: t.values[1])
            fig.add_scatter(x=[t.values[1] for t in front], y=[t.values[0] for t in front],
                            mode="lines+markers", name="Pareto front")
            if best:
                fig.add_scatter(x=[best["values"][1]], y=[best["values"][0]], mode="markers",
                                marker={"size": 15, "symbol": "star"}, name=f"Recommended: trial {best['number']}")
            fig.update_layout(title="Median Net vs worst ticker MaxDD", xaxis_title="Worst MaxDD (%)", yaxis_title="Median Net (%)")
            fig.write_html(str(visuals / "pareto.html"), include_plotlyjs=True)
            report["artifacts"]["visualizations"] = str(visuals)
        except Exception as exc:
            report["visualization_errors"].append(str(exc))

    lines = ["# Universal configuration search", "", f"Status: {report['status']}",
             f"Study: {report['study_name']}", f"Days: {report['days']} ({int(report['days'] * 1440)} bars per ticker)",
             f"Eligible tickers: {len(report['data'])}; excluded: {len(report['excluded_tickers'])}",
             f"Trials: {len(report['trials'])}; states: {dict(Counter(t['state'] for t in report['trials']))}", ""]
    if best:
        rec = report["recommendation"]
        lines += [f"Recommended trial: {best['number']}", f"Median Net: {rec['median_net_pct']:+.4f}%",
                  f"Worst ticker MaxDD: {rec['worst_max_dd_pct']:.4f}%",
                  f"Positive/negative tickers: {rec['positive_tickers']}/{rec['negative_tickers']}",
                  f"Worst ticker Net: {rec['worst_net_pct']:+.4f}%; modeled liquidations: {rec['modeled_liquidations']}",
                  f"Config: {config_path}", "Selection: highest median Net; lower worst MaxDD breaks ties.", ""]
        if rec["median_net_pct"] <= 0:
            lines += ["WARNING: best median Net is not positive.", ""]
    else:
        lines += ["No feasible completed trials; no recommended config was generated.", ""]
    lines += ["## Pareto candidates", "", "| Trial | Median Net (%) | Worst MaxDD (%) |", "|---:|---:|---:|"]
    seen = set()
    for t in sorted(study.best_trials, key=lambda t: -t.values[0]):
        values = tuple(t.values)
        if values not in seen:
            lines.append(f"| {t.number} | {values[0]:+.4f} | {values[1]:.4f} |")
            seen.add(values)
    lines += ["", "## Excluded tickers", ""]
    lines += [f"- {ticker}: {reason}" for ticker, reason in report["excluded_tickers"].items()] or ["None."]
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["limitations"]]
    lines += ["", "## Artifacts", ""] + [f"- {name}: {path}" for name, path in report["artifacts"].items()]
    lines += ["", "## Visualization errors", ""] + (report["visualization_errors"] or ["None."])
    summary = output.with_suffix(".md")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["artifacts"]["summary"] = str(summary)
    write_json(output, report)
    print("\n" + "\n".join(lines[:lines.index("## Pareto candidates")]))
    print(f"Summary: {summary}\nReport: {output}\nVisualizations: {visuals if complete else 'none'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, required=True)
    parser.add_argument("--n-trials", type=int, default=150)
    parser.add_argument("--data-dir", type=Path,
                        help="Use only this directory and its nested Feather files; default: project data/ (top-level)")
    parser.add_argument("--storage", type=Path, default=PROJECT_DIR / "optimization" / "universal_optuna.db",
                        help="SQLite file used to resume compatible trials")
    parser.add_argument("--study-name", help="Defaults to an input fingerprint; n-trials adds new trials")
    parser.add_argument("--optimizer-config", type=Path, default=PROJECT_DIR / "optimizer_config.json",
                        help="Parameter switches, fixed values, ranges and optional steps")
    parser.add_argument("--base-config", type=Path, default=PROJECT_DIR / "optimizer_base_config.json",
                        help="Independent trading-config snapshot; initialized from config.json only if missing")
    args = parser.parse_args()
    if not math.isfinite(args.days) or args.days * 1440 < 2 or args.n_trials < 1:
        parser.error("--days must cover at least two minutes; --n-trials must be positive")
    data_dir = (args.data_dir if args.data_dir is not None else PROJECT_DIR / "data").resolve()
    if not data_dir.is_dir():
        parser.error(f"Data directory does not exist: {data_dir}")

    import optuna
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest

    source = args.base_config
    config, source_bytes = load_base_config(source, PROJECT_DIR / "config.json")
    print(f"Optimizer base config: {source.resolve()}")
    space, fixed_params, steps, settings = load_optimizer_settings(config, args.optimizer_config)
    files = discover(data_dir, recursive=args.data_dir is not None)
    print(f"Historical data directory: {data_dir}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = PROJECT_DIR / "optimization" / f"universal_report_{stamp}.json"
    report = {
        "created_utc": stamp, "config_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_config": config, "days": args.days, "search_space": space,
        "base_config_path": str(source.resolve()),
        "data_dir": str(data_dir),
        "status": "preparing",
        "code_sha256": {str(path.relative_to(PROJECT_DIR)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in [Path(__file__).resolve(), PROJECT_DIR / "backtest/backtest_rebalance.py",
                                     *sorted((PROJECT_DIR / "core").glob("*.py"))]},
        "optimizer_settings": settings,
        "fixed_parameter_values": fixed_params,
        "optimizer_config_path": str(args.optimizer_config.resolve()),
        "fixed_settings": fixed_settings(candidate(config, fixed_params), space),
        "importance_method": "PED-ANOVA on completed feasible trials; Net target negated because lower is better",
        "objectives": ["maximize median net_pct", "minimize worst ticker max_dd_pct"],
        "constraints": {"max_dd_pct_each_ticker": 100, "modeled_liquidations": 0},
        "fixed_policy": {"shares": [0.25, 0.25, 0.5], "ticker_thresholds": {},
                         "other_fields": "Unchanged from source_config; not all are modeled by run_backtest"},
        "limitations": ["In-sample search; no holdout validation", "Independent ticker replays, not a swarm simulation",
                        "Minute OHLCV, simulated market fills and funding; no historical bid/ask",
                        "Largest file per ticker, separate trailing windows; see per-ticker data_window",
                        "Missing time axes do not verify calendar alignment or minute continuity",
                        "Parameter importance is statistical association, not a causal effect",
                        "Resuming restores trials, not the exact random generator state",
                        "Supervisor, rotation, limit execution, spread and live margin ratio settings are not modeled"],
        "data": {}, "excluded_tickers": {}, "trials": [], "pareto": [],
    }

    def save():
        write_json(output, report)

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
        fingerprint = study_contract(report)
        report["study_name"] = args.study_name or f"universal_equal_leverage_{fingerprint[:24]}"
        storage_path = args.storage.resolve()
        if storage_path in (source.resolve(), (PROJECT_DIR / "config.json").resolve()) or storage_path.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
            parser.error("--storage must be a separate SQLite file (.db, .sqlite, .sqlite3)")
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        report["storage"] = str(storage_path)
        study = open_study(f"sqlite:///{storage_path.as_posix()}", report["study_name"], fingerprint)
        report["resumed_trials"] = len(study.trials)
        report["requested_additional_trials"] = args.n_trials
        report["status"] = "running"
        update_trials(report, study)
        save()

        def objective(trial):
            params = {}
            for path, (kind, low, high) in space.items():
                params[path] = trial.suggest_categorical(path, list(dict.fromkeys([low, high]))) if kind == "categorical" else getattr(trial, f"suggest_{kind}")(path, low, high, **({"step": steps[path]} if path in steps else {}))
            cfg = candidate(config, {**fixed_params, **params})
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
            update_trials(report, study)
            save()

        try:
            study.optimize(objective, n_trials=args.n_trials, n_jobs=1, callbacks=[checkpoint])
            report["status"] = "completed"
        except KeyboardInterrupt:
            report["status"] = "interrupted"
        except Exception:
            report["status"] = "failed"
            raise
        finally:
            try:
                save()
                export_results(report, study, output)
            finally:
                study._storage._backend.remove_session()
                study._storage._backend.engine.dispose()


if __name__ == "__main__":
    main()
