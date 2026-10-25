import copy
import json
import tempfile
import unittest
from pathlib import Path

from optimization.optimize_universal import (
    PROJECT_DIR, P, aggregate, candidate, discover, fixed_settings, metrics, search_space, prepare_history,
)


class UniversalOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((PROJECT_DIR / "config.json").read_text(encoding="utf-8"))

    def test_ranges_and_candidate_do_not_mutate_source(self):
        original = copy.deepcopy(self.config)
        space = search_space(self.config)
        for path in (P + "targets.BASE_LONG.leverage", P + "targets.BASE_SHORT.leverage"):
            self.assertEqual(space[path], ("int", 3, 10))
        self.assertEqual(space[P + "liquidation_distance_crit_pct"], ("float", 0, 0))
        value = self.config["rebalance_share_trail_distance"]
        self.assertEqual(space["rebalance_share_trail_distance"], ("float", value * .5, value * 1.5))
        cfg = candidate(self.config, {P + "fuse_enabled": True})
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


if __name__ == "__main__":
    unittest.main()
