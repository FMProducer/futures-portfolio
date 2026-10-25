"""Local read-only observer with durable log offsets and explicit sampling gaps."""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request


def sample(db, root, now=None):
    root = Path(root)
    now = time.time() if now is None else now
    db.executescript("""
        CREATE TABLE IF NOT EXISTS observations(ts REAL PRIMARY KEY, gap_seconds REAL, summary TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(path TEXT, digest TEXT, ts REAL, body BLOB,
                                            PRIMARY KEY(path,digest));
        CREATE TABLE IF NOT EXISTS cursors(path TEXT PRIMARY KEY, generation INTEGER, offset INTEGER, prefix BLOB);
        CREATE TABLE IF NOT EXISTS log_chunks(path TEXT, generation INTEGER, offset INTEGER, ts REAL, body BLOB,
                                             PRIMARY KEY(path,generation,offset));
    """)
    previous = db.execute("SELECT max(ts) FROM observations").fetchone()[0]
    errors = []
    with db:
        for path in list((root / "core").glob("paper*.json")) + list((root / "history").glob("*.json")):
            try:
                before = path.stat()
                body = path.read_bytes()
                data = json.loads(body)
                after = path.stat()
                if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
                    raise ValueError("File changed during sample")
                digest = hashlib.sha256(body).hexdigest()
                db.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)",
                           (str(path.relative_to(root)), digest, now, body))
            except (OSError, ValueError) as exc:
                errors.append(f"{path.name}: {type(exc).__name__}")
        for path in list((root / "core/logs").glob("paper_*.log")) + [root / "logs/supervisor.log"]:
            if not path.exists():
                continue
            name = str(path.relative_to(root))
            try:
                with path.open("rb") as stream:
                    prefix = stream.read(32)
                    cursor = db.execute("SELECT generation,offset,prefix FROM cursors WHERE path=?", (name,)).fetchone()
                    generation, offset = (cursor[0], cursor[1]) if cursor else (0, 0)
                    if cursor and (path.stat().st_size < offset or not prefix.startswith(cursor[2])):
                        generation, offset = generation + 1, 0
                    stream.seek(offset)
                    body = stream.read()
                if body:
                    db.execute("INSERT OR IGNORE INTO log_chunks VALUES(?,?,?,?,?)", (name, generation, offset, now, body))
                db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?,?,?)", (name, generation, offset + len(body), prefix))
            except OSError as exc:
                errors.append(f"{path.name}: {type(exc).__name__}")
        from dashboard.swarm_history import reconstruct
        current = {f"paper-{p.stem.removeprefix('paper_state_').removesuffix('USDT').lower()}"
                   for p in (root / "core").glob("paper_state_*.json")}
        history = reconstruct(root, current)
        summary = {k: history[k] for k in ("reported_closed_net", "open_net", "interrupted_net",
                                           "reported_cumulative_net", "verified_net", "fees_recorded", "session_count")}
        summary["errors"] = errors + history["errors"]
        summary["identity_basis"] = "state presence; process liveness checked separately"
        db.execute("INSERT INTO observations VALUES(?,?,?)", (now, max(0, now - previous) if previous else None,
                                                             json.dumps(summary, ensure_ascii=False)))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    folder = root / "reports/paper_swarm_continuous"
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / "observations.sqlite") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        while True:
            summary = sample(db, root)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open("http://127.0.0.1:8080/health", timeout=5) as response:
                    summary["dashboard"] = json.load(response)
            except Exception as exc:
                summary["dashboard_error"] = str(exc)
            print(json.dumps({"ts": time.time(), **summary}, ensure_ascii=True), flush=True)
            if args.once:
                return
            time.sleep(5)


if __name__ == "__main__":
    main()
