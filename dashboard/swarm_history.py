"""Read-only PAPER session reconstruction; reported balances are not audited Net."""

import json
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


def reconstruct(root, running_names=()):
    root = Path(root)
    groups, errors, combined = {}, [], []

    def read(path):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}")
            return {}

    def add(state, wallet, source, current=False, observed=None):
        if not isinstance(state, dict):
            return
        ticker, start = state.get("base_ticker"), state.get("started_at")
        if not ticker or not isinstance(start, (int, float)) or not math.isfinite(start):
            return
        # Reject old schemas without a proven opening identity instead of inventing one.
        key = ticker, start
        groups.setdefault(key, []).append((state, wallet or {}, source, current, observed))

    for path in sorted((root / "history").glob("*.json")):
        data = read(path)
        if not isinstance(data, dict):
            continue
        if "_paper_state_" in path.name:
            shadow = path.with_name(path.name.replace("_paper_state_", "_paper_shadow_"))
            add(data, read(shadow) if shadow.exists() else {}, str(path))
        elif isinstance(data.get("paper_state"), dict) and isinstance(data.get("state"), dict):
            combined.append((data, path))

    for path in sorted((root / "reports/paper_swarm_monitor_12h").glob("20*.json")):
        data = read(path)
        if not isinstance(data, dict):
            continue
        for state in data.get("states", []):
            if not isinstance(state, dict):
                continue
            state = dict(state, base_ticker=state.get("ticker"))
            add(state, state.get("wallet", {}), str(path))

    for path in sorted((root / "core").glob("paper_state_*.json")):
        state = read(path)
        ticker = path.stem.removeprefix("paper_state_")
        wallet_path = path.with_name(f"paper_shadow_{ticker}.json")
        add(state, read(wallet_path) if wallet_path.exists() else {}, str(path), True)

    for data, path in combined:
        state = data["state"]
        # REAL emergency archives also contain paper_state; require a PAPER identity.
        if (state.get("base_ticker"), state.get("started_at")) in groups:
            add(state, data["paper_state"], str(path), observed=data.get("timestamp"))

    rows = []
    for (ticker, start), records in sorted(groups.items()):
        records.sort(key=lambda r: float(r[0].get("last_update") or r[4] or start))
        opening = next((r[0].get("initial_tpv") for r in records
                        if isinstance(r[0].get("initial_tpv"), (int, float))), None)
        state, wallet, _, current, _ = records[-1]
        live = any(r[3] for r in records)
        is_running = live and f"paper-{ticker.removesuffix('USDT').lower()}" in running_names
        end = state.get("last_tpv")
        snapshots = [(float(r[0].get("last_update") or r[4] or start), r[0].get("last_tpv")) for r in records]
        snapshots = [(t, v) for t, v in snapshots if isinstance(v, (int, float)) and math.isfinite(v)]
        if not isinstance(opening, (int, float)) or not isinstance(end, (int, float)):
            errors.append(f"{ticker}/{start}: missing capital or endpoint")
            continue
        flat_wallets = [r[1] for r in records if r[1].get("positions")
                        and all(float(q) == 0 for q in r[1]["positions"].values())
                        and isinstance(r[1].get("balance"), (int, float))]
        terminal = flat_wallets[-1]["balance"] if flat_wallets and not is_running else None
        endpoint = terminal if terminal is not None else end
        receipt_sets = [r[1].get("share_fill_receipts", {}) for r in records]
        receipts = {}
        for receipt_set in receipt_sets:
            receipts.update(receipt_set)
        fees = sum(float(v.get("commission", 0)) for v in receipts.values())
        realized = sum(float(v.get("trade_pnl", 0)) for v in receipts.values())
        rows.append({"ticker": ticker, "started_at": start, "initial": opening,
                     "endpoint": endpoint, "reported_net": endpoint - opening,
                     "status": "open" if is_running else ("closed_reported" if terminal is not None else "interrupted"),
                     "cycles": max(int(r[0].get("rebalance_cycles") or 0) for r in records),
                     "fees_recorded": fees if receipts else None,
                     "fills_recorded": len(receipts), "realized_recorded": realized if receipts else None,
                     "wallet_receipt_residual": terminal - (opening + realized - fees) if terminal is not None and receipts else None,
                     "verified_net": None, "sources": sorted({r[2] for r in records}),
                     "last_update": state.get("last_update"), "observed_maxdd_pct": None,
                     "samples": snapshots})

    for ticker in {r["ticker"] for r in rows}:
        sessions = sorted((r for r in rows if r["ticker"] == ticker), key=lambda r: r["started_at"])
        log = root / "core/logs" / f"paper_{ticker}.log"
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        index = 0
        for line in lines:
            match = re.search(r"(?:Heartbeat: TPV=|Rebalance #\d+ complete\. TPV: )([\d.-]+)", line)
            if not match or not sessions:
                continue
            try:
                stamp = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone(timedelta(hours=2))).timestamp()
            except ValueError:
                continue
            while index + 1 < len(sessions) and stamp >= int(sessions[index + 1]["started_at"]):
                index += 1
            if stamp >= int(sessions[index]["started_at"]):
                sessions[index]["samples"].append((stamp, float(match[1])))
        for row in sessions:
            # This is a sampled drawdown lower bound, not a continuous account curve.
            peak, dd = row["initial"], 0.0
            for _, value in sorted(row.pop("samples")):
                peak = max(peak, value)
                if peak > 0:
                    dd = max(dd, (peak - value) / peak * 100)
            row["observed_maxdd_pct"] = dd

    closed = [r for r in rows if r["status"] == "closed_reported"]
    active = [r for r in rows if r["status"] == "open"]
    interrupted = [r for r in rows if r["status"] == "interrupted"]
    return {"sessions": rows, "errors": errors,
            "reported_closed_net": sum(r["reported_net"] for r in closed),
            "open_net": sum(r["reported_net"] for r in active),
            "interrupted_net": sum(r["reported_net"] for r in interrupted),
            "reported_cumulative_net": sum(r["reported_net"] for r in rows),
            "verified_net": None, "session_count": len(rows),
            "capital_allocated": sum(r["initial"] for r in rows),
            "fees_recorded": sum(r["fees_recorded"] or 0 for r in rows),
            "warning": "Оценка по архивам и state; не проверенный Net. Закрывающие fills/funding могут отсутствовать. Независимые wallets; сбросы не обнуляют историю. MaxDD — нижняя оценка по наблюдениям, не общий риск счёта."}
