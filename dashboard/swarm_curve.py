import json
import math
import sqlite3
from datetime import datetime, timezone


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
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            peak = None
            maximum = 0.0
            for ts, gap, body in db.execute("SELECT ts,gap_seconds,summary FROM observations ORDER BY ts"):
                summary = json.loads(body)
                net = summary.get("reported_cumulative_net")
                if not isinstance(net, (int, float)) or not math.isfinite(net):
                    result["points"].append([ts, None, None, True])
                    continue
                peak = net if peak is None else max(peak, net)
                drawdown = peak - net
                maximum = max(maximum, drawdown)
                result["points"].append([ts, net, drawdown, bool(gap and gap > 20)])
            if current is not None:
                ts, net = current
                peak = net if peak is None else max(peak, net)
                drawdown = peak - net
                maximum = max(maximum, drawdown)
                previous = result["points"][-1][0] if result["points"] else ts
                result["points"].append([ts, net, drawdown, ts - previous > 20])
            if result["points"]:
                result["max_drawdown_usdt"] = maximum
                if initial:
                    high = float(initial)
                    dd_pct = 0.0
                    for _, net, _, _ in result["points"]:
                        if net is None:
                            continue
                        equity = initial + net
                        high = max(high, equity)
                        dd_pct = max(dd_pct, (high-equity)/high*100)
                    result["max_drawdown_pct"] = dd_pct
                    result["points"] = [[t, None if n is None else n+initial, d, g] for t,n,d,g in result["points"]]
    except (sqlite3.Error, ValueError, OSError) as exc:
        result["error"] = str(exc)
    return result
