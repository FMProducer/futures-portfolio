"""Exercise session rollover with temporary wallets and no trading imports."""

import ast
import asyncio
import gc
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from core.paper_budget import initialize, read_budget
from core.storage import safe_load_json, safe_save_json


class SessionCapitalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.temp.name)
        self.core = self.root / "core"
        self.core.mkdir()
        self.config = {"portfolios": [{"paper_initial_capital": 1000, "initial_capital": 500}]}
        self.launch = AsyncMock(return_value=SimpleNamespace(wait=AsyncMock(return_value=0)))
        self.connector = SimpleNamespace(verify_connection=AsyncMock(), get_positions=AsyncMock(return_value={}))
        self.ns = dict(BASE_PATH=self.root, CORE_PATH=self.core, math=math, os=os,
                       sys=sys, time=time, shutil=shutil, logger=Mock(),
                       safe_load_json=safe_load_json, safe_save_json=safe_save_json,
                       BinanceConnector=Mock(return_value=self.connector),
                       asyncio=SimpleNamespace(create_subprocess_shell=self.launch,
                                               subprocess=asyncio.subprocess))
        source = Path(__file__).resolve().parents[1] / "supervisor/swarm_manager.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                     and n.name in ("reset_bot_state_files", "start_bot")]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), self.ns)

    def seed(self, equity=1147.6612, paper=True, closed=True):
        prefix = "paper" if paper else "real"
        shadow = "paper_shadow" if paper else "shadow_state"
        self.state_path = self.core / f"{prefix}_state_QUSDT.json"
        self.wallet_path = self.core / f"{shadow}_QUSDT.json"
        state = dict(initial_tpv=1000, started_at=123, last_tpv=equity,
                     last_profit=equity - 1000, tpv_ath=1200, virt_debt=575,
                     rebalance_cycles=8, trailing_stop_triggered=closed,
                     paper_close_complete=closed, trailing_stop_violation_start=12)
        wallet = dict(balance=equity, positions={"QUSDT_LONG": 0, "QUSDT_SHORT": 0})
        self.state_path.write_text(json.dumps(state))
        self.wallet_path.write_text(json.dumps(wallet))
        return state, wallet

    def assert_fresh(self, capital):
        state = json.loads(self.state_path.read_text())
        wallet = json.loads(self.wallet_path.read_text())
        for key in ("initial_tpv", "reference_tpv", "tpv_ath", "last_tpv", "balance"):
            self.assertEqual(state[key], capital)
        for key in ("last_profit", "total_pnl_pct", "rebalance_cycles", "virt_debt",
                    "trailing_stop_violation_start", "trailing_stop_triggered"):
            self.assertEqual(state[key], 0)
        self.assertNotEqual(state["started_at"], 123)
        self.assertEqual(wallet["balance"], capital)
        self.assertEqual(wallet["positions"], {"QUSDT_LONG": 0, "QUSDT_SHORT": 0})

    async def test_paper_profit_returns_to_pool_before_capped_new_session(self):
        state, _ = self.seed()
        initialize(self.root, 1, 1000, {"QUSDT": state})
        await self.ns["start_bot"]("QUSDT", True, self.config)
        self.assert_fresh(1000)
        budget = read_budget(self.root)
        self.assertAlmostEqual(budget["cash"], 147.6612)
        self.assertEqual(budget["allocations"], {"QUSDT": 1000})
        self.assertEqual(len(list((self.root / "history").glob("*.json"))), 2)
        # A process restart must not return or allocate the same capital twice.
        before = self.state_path.read_bytes(), self.wallet_path.read_bytes(), budget
        await self.ns["start_bot"]("QUSDT", True, self.config)
        self.assertEqual(before, (self.state_path.read_bytes(), self.wallet_path.read_bytes(), read_budget(self.root)))

    async def test_paper_loss_is_not_refilled(self):
        state, _ = self.seed(equity=900)
        initialize(self.root, 1, 1000, {"QUSDT": state})
        await self.ns["start_bot"]("QUSDT", True, self.config)
        self.assert_fresh(900)
        self.assertEqual(read_budget(self.root)["cash"], 0)

    async def test_paper_unverified_close_cannot_return_capital(self):
        state, wallet = self.seed()
        wallet["positions"]["QUSDT_LONG"] = 1
        self.wallet_path.write_text(json.dumps(wallet))
        initialize(self.root, 1, 1000, {"QUSDT": state})
        with self.assertRaisesRegex(ValueError, "terminal close is unverified"):
            await self.ns["start_bot"]("QUSDT", True, self.config)
        self.assertEqual(read_budget(self.root)["cash"], 0)
        self.launch.assert_not_awaited()

    async def test_paper_close_without_pool_is_preserved(self):
        self.seed()
        before = self.state_path.read_bytes(), self.wallet_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "capital pool reconciliation"):
            await self.ns["start_bot"]("QUSDT", True, self.config)
        self.assertEqual(before, (self.state_path.read_bytes(), self.wallet_path.read_bytes()))
        self.launch.assert_not_awaited()

    async def test_real_profit_has_capped_fresh_baseline_after_exchange_check(self):
        self.seed(paper=False)
        await self.ns["start_bot"]("QUSDT", False, self.config)
        self.assert_fresh(500)
        self.connector.verify_connection.assert_awaited_once()
        self.connector.get_positions.assert_awaited_once()
        archived = list((self.root / "history").glob("*real_state*"))
        self.assertEqual(json.loads(archived[0].read_text())["last_tpv"], 1147.6612)

    async def test_real_open_exchange_position_blocks_reset(self):
        self.seed(paper=False)
        self.connector.get_positions.return_value = {"QUSDT_SHORT": {"qty": -1}}
        before = self.state_path.read_bytes(), self.wallet_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "terminal close is unverified"):
            await self.ns["start_bot"]("QUSDT", False, self.config)
        self.assertEqual(before, (self.state_path.read_bytes(), self.wallet_path.read_bytes()))
        self.launch.assert_not_awaited()

    async def test_real_exchange_failure_blocks_reset(self):
        self.seed(paper=False)
        self.connector.get_positions.side_effect = RuntimeError("unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await self.ns["start_bot"]("QUSDT", False, self.config)
        self.assertEqual(json.loads(self.state_path.read_text())["started_at"], 123)
        self.launch.assert_not_awaited()

    async def test_reset_caps_override_and_rejects_invalid_capital(self):
        self.seed()
        await self.ns["reset_bot_state_files"]("QUSDT", True, self.config, capital_override=2000)
        self.assert_fresh(1000)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Invalid starting capital"):
                await self.ns["reset_bot_state_files"]("QUSDT", True, self.config, capital_override=value)

    async def test_smaller_allocation_keeps_actual_baseline_and_ath_in_bot(self):
        source = Path(__file__).resolve().parents[1] / "core/main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {"state": {"initial_tpv": 900, "tpv_ath": 1200},
                 "paper_state": {"balance": 900, "positions": {"QUSDT_LONG": 0, "QUSDT_SHORT": 0}},
                 "base_ticker": "QUSDT", "target_initial_cap": 1000, "target_initial": 1000,
                 "initial_tpv": 900, "tpv_total": 900, "tpv_ath": 0,
                 "paper_mode": False, "_funding_halt_active": False,
                 "connector": self.connector, "logger": Mock(), "share_trail": Mock(),
                 "save_json": AsyncMock(), "state_file_path": "unused", "paper_state_file_path": "unused"}
        from decimal import Decimal
        names.update(Decimal=Decimal, logging=logging)
        clean = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_apply_clean_slate")
        exec(compile(ast.Module(body=[clean], type_ignores=[]), str(source), "exec"), names)
        predicates = ("not paper_mode and", "not state.get('trailing_stop_triggered', False)",
                      "initial_tpv > target_initial", "tpv_ath == 0 or tpv_total > tpv_ath")
        for predicate in predicates:
            node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                        and ast.unparse(n.test).startswith(predicate)
                        and (predicate != "not paper_mode and" or "_funding_halt_active" in ast.unparse(n.test)))
            module = ast.parse("async def fragment():\n    pass\n")
            module.body[0].body = [ast.Global(names=["initial_tpv", "tpv_ath"]), node]
            exec(compile(ast.fix_missing_locations(module), str(source), "exec"), names)
            await names["fragment"]()
        self.assertEqual(names["state"]["initial_tpv"], 900)
        self.assertEqual(names["state"]["tpv_ath"], 900)
        self.assertEqual(names["paper_state"]["balance"], 900)
        self.assertEqual(names["initial_tpv"], 900)


if __name__ == "__main__":
    unittest.main()
