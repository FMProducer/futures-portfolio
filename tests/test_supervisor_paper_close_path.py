"""Check the closing child import path without importing the trading application."""
import ast
import builtins
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


class PaperClosePathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.core = Path(self.temporary.name) / "core"
        self.core.mkdir()
        (self.core / "paper_budget.sqlite").touch()
        self.events = []
        self.release = Mock(side_effect=lambda *args, **kwargs: self.events.append("release"))
        self.close_code = 0
        probe = ("from importlib.machinery import PathFinder\nimport sys\nfrom pathlib import Path\n"
                 "spec = PathFinder.find_spec('core', sys.path)\n"
                 f"assert spec and Path(spec.origin).resolve() == Path({str(self.root / 'core/__init__.py')!r}).resolve()\n")
        (self.core / "main.py").write_text(probe, encoding="utf-8")

        async def wait_stop():
            self.events.append("stopped")
            return 0

        async def wait_close():
            self.events.append("close_exit")
            return self.close_code

        async def spawn(*args, **kwargs):
            self.assertEqual(self.events, ["stopped"])
            self.assertEqual(kwargs["env"]["PYTHONPATH"], str(self.root))
            self.assertEqual(kwargs["cwd"], str(self.root))
            self.assertEqual(args[args.index("--ticker") + 1], "AGTUSDT")
            self.assertIn("--stop", args)
            self.assertIn("--paper", args)
            if self.close_code == 0:
                result = subprocess.run(args, cwd=kwargs["cwd"], env=kwargs["env"],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            return SimpleNamespace(wait=wait_close)

        def import_stub(name, *args, **kwargs):
            if name == "core.paper_budget":
                return SimpleNamespace(release=self.release)
            raise AssertionError(f"Unexpected application import: {name}")

        namespace = {
            "__builtins__": dict(vars(builtins), __import__=import_stub),
            "BASE_PATH": self.root, "CORE_PATH": self.core,
            "CONFIG_PATH": Path(self.temporary.name) / "unused_config.json",
            "os": SimpleNamespace(environ={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}),
            "sys": sys, "logger": Mock(), "safe_load_json": AsyncMock(return_value={}),
            "asyncio": SimpleNamespace(
                subprocess=SimpleNamespace(DEVNULL=subprocess.DEVNULL),
                create_subprocess_shell=AsyncMock(return_value=SimpleNamespace(wait=wait_stop)),
                create_subprocess_exec=AsyncMock(side_effect=spawn)),
        }
        source = self.root / "supervisor/swarm_manager.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "stop_bot")
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        self.stop = namespace["stop_bot"]

    async def test_paper_child_sees_core_before_successful_release(self):
        await self.stop("AGTUSDT", is_paper=True)
        self.assertEqual(self.events, ["stopped", "close_exit", "release"])
        self.release.assert_called_once()

    async def test_failed_close_keeps_capital_reserved(self):
        self.close_code = 1
        with self.assertRaisesRegex(RuntimeError, "PAPER close failed; capital remains reserved: AGTUSDT"):
            await self.stop("AGTUSDT", is_paper=True)
        self.release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
