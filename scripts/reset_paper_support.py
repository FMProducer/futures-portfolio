"""Quote-safe operations for the PowerShell PAPER reset command."""
import json
import sys
from pathlib import Path


def main():
    if sys.argv[1] == "inventory":
        processes = json.load(sys.stdin)
        print(json.dumps([{
            "name": p["name"],
            "status": p.get("pm2_env", {}).get("status"),
            "pid": p.get("pid", 0),
        } for p in processes]))
    elif sys.argv[1] == "initialize":
        from core.paper_budget import initialize, read_budget
        root = Path(sys.argv[2]).resolve()
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        initialize(root, config["paper_mode_bots"], config["portfolios"][0]["paper_initial_capital"], {})
        budget = read_budget(root)
        if budget["cash"] != budget["initial"] or budget["allocations"]:
            raise ValueError("Expected a clean unallocated PAPER budget")
        print(json.dumps(budget))
    else:
        raise ValueError("Unknown reset operation")


if __name__ == "__main__":
    main()
