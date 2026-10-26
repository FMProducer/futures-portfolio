import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import optimization.optimize_universal as optimizer

from optimization.optimize_universal import (
    PROJECT_DIR, P, aggregate, candidate, discover, fixed_settings, metrics, search_space, prepare_history,
    export_results, open_study, study_contract, load_optimizer_settings, load_base_config,
)


class UniversalOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((PROJECT_DIR / "config.json").read_text(encoding="utf-8"))

    def test_ranges_and_candidate_do_not_mutate_source(self):
        original = copy.deepcopy(self.config)
        space = search_space(self.config)
        self.assertEqual(space["leverage"], ("int", 3, 10))
        for path in (P + "targets.BASE_LONG.leverage", P + "targets.BASE_SHORT.leverage"):
            self.assertNotIn(path, space)
        self.assertEqual(space[P + "liquidation_distance_crit_pct"], ("float", 0, 0))
        value = self.config["rebalance_share_trail_distance"]
        self.assertEqual(space["rebalance_share_trail_distance"], ("float", value * .5, value * 1.5))
        cfg = candidate(self.config, {P + "fuse_enabled": True, "leverage": 4})
        self.assertEqual(cfg["portfolios"][0]["targets"]["BASE_LONG"]["leverage"], 4)
        self.assertEqual(cfg["portfolios"][0]["targets"]["BASE_SHORT"]["leverage"], 4)
        self.assertEqual(self.config, original)
        self.assertTrue(cfg["portfolios"][0]["fuse_enabled"])
        self.assertEqual(cfg["portfolios"][0]["ticker_thresholds"], {})
        self.assertEqual([cfg["portfolios"][0]["targets"][leg]["share"] for leg in
                          ("BASE_LONG", "BASE_SHORT", "VIRTUAL")], [.25, .25, .5])
        fixed = fixed_settings(cfg, space)
        self.assertEqual(fixed["min_notional_usdt"], self.config["min_notional_usdt"])
        self.assertNotIn(P + "fuse_enabled", fixed)

    def test_discovery_is_local_unique_and_uses_largest_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in (("BTCUSDT_live.feather", b"1"), ("BTCUSDT_snapshot.feather", b"123"),
                                  ("ETHUSDT_live.feather", b"12"), ("notes.feather", b"1234")):
                (root / name).write_bytes(content)
            result = discover(root)
            self.assertEqual(set(result), {"BTCUSDT", "ETHUSDT"})
            self.assertEqual(result["BTCUSDT"].name, "BTCUSDT_snapshot.feather")

    def test_recursive_discovery_stays_in_selected_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "selected"
            (selected / "BTCUSDT").mkdir(parents=True)
            (selected / "ETHUSDT").mkdir()
            (selected / "BTCUSDT/BTCUSDT_live.feather").write_bytes(b"123")
            (selected / "BTCUSDT_snapshot.feather").write_bytes(b"1")
            (selected / "ETHUSDT/ETHUSDT_live.feather").write_bytes(b"12")
            (root / "OUTSIDEUSDT_live.feather").write_bytes(b"12345")
            self.assertEqual(set(discover(selected)), {"BTCUSDT"})
            files = discover(selected, recursive=True)
            self.assertEqual(set(files), {"BTCUSDT", "ETHUSDT"})
            self.assertEqual(files["BTCUSDT"].parent.name, "BTCUSDT")
            with self.assertRaisesRegex(ValueError, "No USDT"):
                discover(root / "missing", recursive=True)

    def test_objectives_and_hard_constraints(self):
        rows = [{"net_pct": n, "max_dd_pct": d, "liquidations": 0} for n, d in
                ((-10, 20), (5, 50), (1000, 30))]
        self.assertEqual(aggregate(rows), (5, 50))
        rows[0]["max_dd_pct"] = 50.01
        with self.assertRaises(ValueError):
            aggregate(rows)
        rows[0]["max_dd_pct"] = 20
        rows[0]["liquidations"] = 1
        with self.assertRaises(ValueError):
            aggregate(rows)
        with self.assertRaises(ValueError):
            aggregate([])

    def test_net_uses_final_equity_and_requires_accounting(self):
        result = {"initial_capital": 1000, "final_equity": 1100, "max_dd_pct": 12,
                  "liquidations": 0, "cycles": 5, "data_window": {}, "accounting_ok": True}
        self.assertAlmostEqual(metrics(result)["net_pct"], 10)
        result["accounting_ok"] = False
        with self.assertRaises(ValueError):
            metrics(result)
        result["accounting_ok"] = True
        result["final_equity"] = float("nan")
        with self.assertRaises(ValueError):
            metrics(result)

    def test_portfolio_default_is_an_infeasible_result(self):
        with self.assertRaisesRegex(ValueError, "Infeasible candidate: portfolio default"):
            metrics({"status": "portfolio_default", "accounting_ok": True})

    def test_history_without_time_is_supported_without_inventing_dates(self):
        import pandas as pd

        frame = pd.DataFrame({key: [1.0] * 3 for key in ("open", "high", "low", "close", "volume")})
        tail, metadata = prepare_history(frame, 2 / 1440, Path("sample.feather"))
        self.assertEqual(len(tail), 2)
        self.assertNotIn("time", tail.columns)
        self.assertEqual(metadata["time_source"], "mtime")
        self.assertIsNone(metadata["start"])
        self.assertFalse(metadata["minute_continuity_verified"])
        excluded, reason = prepare_history(frame, 4 / 1440, Path("sample.feather"))
        self.assertIsNone(excluded)
        self.assertEqual(reason, {"reason": "insufficient_history", "available_bars": 3,
                                  "required_bars": 4, "file": "sample.feather"})
        full, _ = prepare_history(frame, 3 / 1440, Path("sample.feather"))
        self.assertEqual(len(full), 3)
        with self.assertRaisesRegex(ValueError, "Missing OHLCV"):
            prepare_history(frame.drop(columns="close"), 2 / 1440, Path("sample.feather"))

    def test_history_with_time_still_requires_contiguous_minutes(self):
        import pandas as pd

        frame = pd.DataFrame({key: [1.0] * 3 for key in ("open", "high", "low", "close", "volume")})
        frame["time"] = pd.date_range("2026-01-01", periods=3, freq="min", tz="UTC")
        _, metadata = prepare_history(frame, 3 / 1440, Path("sample.feather"))
        self.assertTrue(metadata["minute_continuity_verified"])
        frame.loc[2, "time"] += pd.Timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "Non-contiguous"):
            prepare_history(frame, 3 / 1440, Path("sample.feather"))

    def test_shared_leverage_rejects_out_of_range_values(self):
        for value in (2, 11, True, 3.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                candidate(self.config, {"leverage": value})

    def test_base_snapshot_is_initialized_once_and_survives_missing_project_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "config.json"
            snapshot = Path(tmp) / "optimizer_base_config.json"
            original = json.dumps(self.config).encode()
            project.write_bytes(original)
            cfg, raw = load_base_config(snapshot, project)
            self.assertEqual(cfg, self.config)
            self.assertEqual(raw, original)
            self.assertEqual(snapshot.read_bytes(), original)
            project.write_text("invalid JSON from an external writer")
            self.assertEqual(load_base_config(snapshot, project), (cfg, raw))
            project.unlink()
            self.assertEqual(load_base_config(snapshot, project), (cfg, raw))
            with self.assertRaisesRegex(ValueError, "separate"):
                load_base_config(snapshot, snapshot)
            snapshot.write_text("invalid snapshot")
            with self.assertRaises(json.JSONDecodeError):
                load_base_config(snapshot, project)

    def test_optimizer_config_switches_ranges_and_fixed_values(self):
        settings = json.loads((PROJECT_DIR / "optimizer_config.json").read_text())
        enabled = {"leverage", "equity_trailing_stop_activation_pct", "rebalance_share_trail_distance",
                   "max_drawdown_limit", "toxic_cooldown_days", P + "safety_guards.net_move_window_sec",
                   P + "rebalance_threshold_surplus", P + "safety_guards.trend_lookback_bars"}
        for name, spec in settings["parameters"].items():
            spec["enabled"] = name in enabled
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "optimizer.json"
            path.write_text(json.dumps(settings))
            space, fixed, steps, _ = load_optimizer_settings(self.config, path)
            self.assertEqual(len(space), 8)
            self.assertIn("leverage", space)
            self.assertIn("equity_trailing_stop_pct", fixed)
            settings["parameters"]["leverage"].update(enabled=False, value=4)
            settings["parameters"]["equity_trailing_stop_pct"].update(enabled=True, low=1.0, high=2.0, step=.25)
            path.write_text(json.dumps(settings))
            space, fixed, steps, _ = load_optimizer_settings(self.config, path)
            self.assertNotIn("leverage", space)
            self.assertEqual(space["equity_trailing_stop_pct"], ("float", 1.0, 2.0))
            self.assertEqual(steps["equity_trailing_stop_pct"], .25)
            cfg = candidate(self.config, fixed)
            self.assertEqual(cfg["portfolios"][0]["targets"]["BASE_SHORT"]["leverage"], 4)
            settings["parameters"]["equity_trailing_stop_pct"]["step"] = .3
            path.write_text(json.dumps(settings))
            with self.assertRaisesRegex(ValueError, "divisible"):
                load_optimizer_settings(self.config, path)
            settings["parameters"]["equity_trailing_stop_pct"]["step"] = .25
            settings["parameters"]["leverage"]["high"] = 11
            path.write_text(json.dumps(settings))
            with self.assertRaisesRegex(ValueError, "Invalid range"):
                load_optimizer_settings(self.config, path)

    def test_sqlite_resume_and_contract_mismatch(self):
        import optuna

        with tempfile.TemporaryDirectory() as tmp:
            url = "sqlite:///" + (Path(tmp) / "study.db").as_posix()
            study = open_study(url, "test", "contract_a")
            trial = study.ask()
            trial.suggest_int("leverage", 3, 10)
            study.tell(trial, [1.0, 10.0])
            resumed = open_study(url, "test", "contract_a")
            self.assertEqual(len(resumed.trials), 1)
            self.assertEqual(resumed.trials[0].values, [1.0, 10.0])
            resumed.tell(resumed.ask(), [2.0, 9.0])
            third = open_study(url, "test", "contract_a")
            self.assertEqual(len(third.trials), 2)
            with self.assertRaisesRegex(ValueError, "inputs differ"):
                open_study(url, "test", "contract_b")
            self.assertEqual(study.directions, [optuna.study.StudyDirection.MAXIMIZE,
                                                optuna.study.StudyDirection.MINIMIZE])
            for item in (study, resumed, third):
                item._storage._backend.remove_session()
                item._storage._backend.engine.dispose()

    def test_contract_changes_when_inputs_change(self):
        report = {key: {} for key in ("config_sha256", "days", "search_space", "objectives", "constraints", "data", "code_sha256")}
        original = study_contract(report)
        for key in report:
            changed = copy.deepcopy(report)
            changed[key] = "different"
            self.assertNotEqual(original, study_contract(changed))

    def test_exports_config_summary_and_all_html_plots(self):
        import optuna
        from optuna.distributions import FloatDistribution, IntDistribution

        study = optuna.create_study(directions=["maximize", "minimize"])
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "optimization"
            output_dir.mkdir()
            report = {"created_utc": "test", "status": "completed", "study_name": "test", "days": 1,
                      "data": {"TESTUSDT": {}}, "excluded_tickers": {}, "limitations": ["In-sample only"],
                      "search_space": {"leverage": ("int", 3, 10), "equity_trailing_stop_pct": ("float", 0.1, 5.0)}}
            for i in range(12):
                params = {"leverage": 3 + i % 8, "equity_trailing_stop_pct": 0.1 + i * 0.3}
                cfg = candidate(self.config, params)
                rows = {"TESTUSDT": {"net_pct": i, "max_dd_pct": i + 5, "liquidations": 0}}
                study.add_trial(optuna.trial.create_trial(params=params,
                    distributions={"leverage": IntDistribution(3, 10), "equity_trailing_stop_pct": FloatDistribution(.1, 5)},
                    values=[i, i + 5], user_attrs={"candidate_config": cfg, "per_ticker": rows}))
            original = copy.deepcopy(self.config)
            export_results(report, study, output_dir / "report.json")
            best = json.loads((Path(tmp) / "best_netdd_config_test.json").read_text())
            self.assertEqual(best, candidate(self.config, study.trials[-1].params))
            self.assertEqual(report["recommendation"]["trial"], 11)
            self.assertEqual(report["visualization_errors"], [])
            html = list((output_dir / "optuna_visuals_test").glob("*.html"))
            self.assertEqual(len(html), 9)
            self.assertTrue((output_dir / "report.md").exists())
            self.assertEqual(original, self.config)
            report["created_utc"] = "broken_plot"
            with patch("optuna.visualization.plot_param_importances", side_effect=RuntimeError("plot unavailable")):
                export_results(report, study, output_dir / "broken.json")
            self.assertEqual(len(report["visualization_errors"]), 2)
            self.assertTrue((Path(tmp) / "best_netdd_config_broken_plot.json").exists())
            self.assertTrue((output_dir / "broken.md").exists())

    def test_no_feasible_trials_does_not_export_config(self):
        import optuna

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "optimization"
            output_dir.mkdir()
            report = {"created_utc": "empty", "status": "completed", "study_name": "empty", "days": 1,
                      "data": {}, "excluded_tickers": {}, "limitations": []}
            study = optuna.create_study(directions=["maximize", "minimize"])
            export_results(report, study, output_dir / "report.json")
            self.assertIsNone(report["recommendation"])
            self.assertFalse((Path(tmp) / "best_netdd_config_empty.json").exists())

    def test_main_resumes_without_mutating_config_or_data(self):
        import pandas as pd

        async def fake_backtest(config_path, data_dir, **kwargs):
            cfg = json.loads(Path(config_path).read_text())
            targets = cfg["portfolios"][0]["targets"]
            self.assertEqual(targets["BASE_LONG"]["leverage"], targets["BASE_SHORT"]["leverage"])
            self.assertEqual(len(pd.read_feather(Path(data_dir) / "TESTUSDT_live.feather")), 3)
            if mutate_project[0]:
                source.write_text("externally changed project config")
            return {"initial_capital": 1000, "final_equity": 1010, "max_dd_pct": 5,
                    "liquidations": 0, "cycles": 1, "data_window": {}, "accounting_ok": True}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ("optimization", "backtest", "core", "data"):
                (root / directory).mkdir()
            script = root / "optimization" / "optimize_universal.py"
            script.write_text("test code", encoding="utf-8")
            (root / "backtest/backtest_rebalance.py").write_text("test backtest", encoding="utf-8")
            source = root / "config.json"
            source.write_text(json.dumps(self.config), encoding="utf-8")
            (root / "optimizer_config.json").write_bytes((PROJECT_DIR / "optimizer_config.json").read_bytes())
            frame = pd.DataFrame({key: [1.0] * 3 for key in ("open", "high", "low", "close", "volume")})
            selected = root / "reports/backtest_gate/dataset"
            (selected / "TESTUSDT").mkdir(parents=True)
            (selected / "SHORTUSDT").mkdir()
            history = selected / "TESTUSDT/TESTUSDT_live.feather"
            frame.to_feather(history)
            frame.iloc[:1].to_feather(selected / "SHORTUSDT/SHORTUSDT_live.feather")
            frame.to_feather(root / "data/DECOYUSDT_live.feather")
            source_bytes = source.read_bytes()
            data_bytes = history.read_bytes()
            argv = ["optimizer", "--data-dir", str(selected), "--days", str(3 / 1440), "--n-trials"]
            mutate_project = [False]
            with patch.object(optimizer, "PROJECT_DIR", root), patch.object(optimizer, "__file__", str(script)), \
                    patch("backtest.backtest_rebalance.run_backtest", side_effect=fake_backtest):
                for count in (2, 1):
                    with patch("sys.argv", [*argv, str(count)]):
                        optimizer.main()
                self.assertEqual(source.read_bytes(), source_bytes)
                mutate_project[0] = True
                with patch("sys.argv", [*argv, "1"]):
                    optimizer.main()
                source.unlink()
                with patch("sys.argv", [*argv, "1"]):
                    mutate_project[0] = False
                    optimizer.main()
            reports = sorted((root / "optimization").glob("universal_report_*.json"))
            self.assertEqual(len(reports), 4)
            latest = json.loads(reports[-1].read_text())
            self.assertEqual(latest["status"], "completed")
            self.assertEqual(latest["resumed_trials"], 4)
            self.assertEqual(len(latest["trials"]), 5)
            self.assertIn("SHORTUSDT", latest["excluded_tickers"])
            self.assertEqual(list(latest["data"]), ["TESTUSDT"])
            self.assertFalse(source.exists())
            self.assertEqual((root / "optimizer_base_config.json").read_bytes(), source_bytes)
            self.assertEqual(history.read_bytes(), data_bytes)
            self.assertEqual(latest["data_dir"], str(selected.resolve()))
            self.assertTrue(Path(latest["artifacts"]["recommended_config"]).exists())


if __name__ == "__main__":
    unittest.main()
