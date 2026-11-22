from contextlib import closing
import json
import math
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from threading import RLock

_LOCK = RLock()


@lru_cache(maxsize=4)
def _cache(path):
    return {}


def _observations(path):
    with _LOCK, closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        stat = path.stat()
        wal = path.with_name(path.name + "-wal")
        try:
            wal_stat = wal.stat()
            version = stat.st_mtime_ns, stat.st_size, wal_stat.st_mtime_ns, wal_stat.st_size
        except FileNotFoundError:
            version = stat.st_mtime_ns, stat.st_size, None, None
        identity = stat.st_dev, stat.st_ino
        state = _cache(path.resolve())
        db.execute("BEGIN")
        count = db.execute("SELECT count(*) FROM observations").fetchone()[0]
        last = db.execute("SELECT coalesce(max(rowid),0) FROM observations").fetchone()[0]
        reset = (state.get("identity") != identity or count < state.get("count", 0)
                 or last < state.get("last", 0)
                 or (last == state.get("last") and version != state.get("version")))
        cursor = 0 if reset else state.get("last", 0)
        rows = db.execute("SELECT rowid,ts,gap_seconds,summary FROM observations WHERE rowid>? ORDER BY ts",
                          (cursor,)).fetchall()
        old = [] if reset else state.get("rows", [])
        if (len(old) + len(rows) != count or (old and rows and rows[0][1] <= old[-1][0])):
            old = []
            rows = db.execute("SELECT rowid,ts,gap_seconds,summary FROM observations ORDER BY ts").fetchall()
        # shortcut: observations are append-only; historical edits combined with inserts require a full reload.
        decoded = [(ts, gap, json.loads(body).get("reported_cumulative_net")) for _, ts, gap, body in rows]
        old.extend(decoded)
        if reset or old is not state.get("rows"):
            state.pop("curve", None)
        state.update(identity=identity, count=count, last=last, version=version, rows=old)
        return old


def _plot_points(points):
    if len(points) <= 2000:
        return points
    selected = set()
    step = math.ceil(len(points) / 400)
    for start in range(0, len(points), step):
        stop = min(start + step, len(points))
        selected.update((start, stop - 1))
        valid = [i for i in range(start, stop) if points[i][1] is not None]
        if valid:
            selected.update((min(valid, key=lambda i: points[i][1]),
                             max(valid, key=lambda i: points[i][1]),
                             max(valid, key=lambda i: points[i][2])))
        for i in range(start, stop):
            if points[i][3] or points[i][1] is None:
                selected.update((max(0, i - 1), i))
    return [points[i] for i in sorted(selected)]


def _base_curve(path, initial):
    rows = _observations(path)
    state = _cache(path.resolve())
    curve = state.get("curve")
    if curve is None or curve["initial"] != initial:
        curve = {"initial": initial, "points": [], "peak": None, "maximum": 0.0,
                 "high": float(initial) if initial else None, "pct": 0.0, "plot": []}
    points = curve["points"]
    changed = len(points) != len(rows)
    for ts, gap, net in rows[len(points):]:
        if not isinstance(net, (int, float)) or not math.isfinite(net):
            points.append([ts, None, None, True])
            continue
        curve["peak"] = net if curve["peak"] is None else max(curve["peak"], net)
        drawdown = curve["peak"] - net
        curve["maximum"] = max(curve["maximum"], drawdown)
        value = net
        if initial:
            value += initial
            curve["high"] = max(curve["high"], value)
            curve["pct"] = max(curve["pct"], (curve["high"] - value) / curve["high"] * 100)
        points.append([ts, value, drawdown, bool(gap and gap > 20)])
    if changed:
        curve["plot"] = _plot_points(points)
    state["curve"] = curve
    return curve


def read_curve(root, current=None, initial=None):
    path = root / "reports/paper_swarm_continuous/observations.sqlite"
    result = {"points": [], "markers": [], "error": None, "max_drawdown_usdt": None,
              "initial": initial, "max_drawdown_pct": None}
    for folder in (root / "reports/paper_lifecycle_repair").glob("deploy_*"):
        try:
            ts = datetime.strptime(folder.name, "deploy_%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            result["markers"].append({"ts": ts, "label": "PAPER restart / backup"})
        except ValueError:
            continue
    if not path.exists():
        result["error"] = "Observation database is unavailable"
        return result
    try:
        with _LOCK:
            curve = _base_curve(path, initial)
            result["points"] = [point[:] for point in curve["plot"]]
            maximum, dd_pct = curve["maximum"], curve["pct"]
            if current is not None:
                ts, net = current
                peak = net if curve["peak"] is None else max(curve["peak"], net)
                drawdown = peak - net
                maximum = max(maximum, drawdown)
                previous = curve["points"][-1][0] if curve["points"] else ts
                result["points"].append([ts, net + initial if initial else net, drawdown, ts - previous > 20])
                if initial:
                    equity = initial + net
                    high = max(curve["high"], equity)
                    dd_pct = max(dd_pct, (high - equity) / high * 100)
            if result["points"]:
                result["max_drawdown_usdt"] = maximum
                if initial:
                    result["max_drawdown_pct"] = dd_pct
    except (sqlite3.Error, ValueError, OSError) as exc:
        result["error"] = str(exc)
    return result
