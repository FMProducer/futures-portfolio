"""Audit harness: original test collection, localhost-only sockets, no PM2 mutations."""
import os
import socket
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT / "_sandbox" / "futures_portfolio"
os.chdir(ROOT)
sys.path[:0] = [str(ROOT.parent), str(ROOT)]
for name in list(os.environ):
    if any(word in name.upper() for word in ("BINANCE", "TELEGRAM")):
        os.environ.pop(name)
real_connect = socket.socket.connect


def guarded_connect(self, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise OSError("Audit: external network disabled")
    return real_connect(self, address)


socket.socket.connect = guarded_connect
real_popen = subprocess.Popen


class GuardedPopen(real_popen):
    def __init__(self, args, *a, **kw):
        text = str(args).lower()
        if "pm2" in text and not ("jlist" in text or "--version" in text):
            raise OSError("Audit: PM2 mutations forbidden")
        super().__init__(args, *a, **kw)


subprocess.Popen = GuardedPopen
import pytest

if "--stub-pm2-save" in sys.argv:
    import asyncio
    from types import SimpleNamespace
    sys.argv.remove("--stub-pm2-save")
    original_shell = asyncio.create_subprocess_shell
    async def isolated_shell(cmd, *a, **kw):
        if cmd.strip() == "pm2 save":
            async def wait():
                return 0
            return SimpleNamespace(wait=wait, returncode=0)
        return await original_shell(cmd, *a, **kw)
    asyncio.create_subprocess_shell = isolated_shell
    raise SystemExit(pytest.main(["tests", "--junitxml=" + str(OUT / "unit_results_isolated.xml"),
                                 "-k", "isolated_blacklists_signal_processing or manage_swarm_rotation_hysteresis_and_cushion"] + sys.argv[1:]))

raise SystemExit(pytest.main(["tests", "accounting/scenarios", "--junitxml=" + str(OUT / "unit_results.xml"),
    "--cov=.", "--cov-report=json:" + str(OUT / "coverage.json"),
    "--cov-report=term", "-o", "cache_dir=" + str(OUT / "_sandbox" / ".pytest_cache")] + sys.argv[1:]))
