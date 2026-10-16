"""Check legacy equity plotter on an empty fixture, without notifications."""
import asyncio
import json
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT / "_sandbox" / "futures_portfolio"
fixture = OUT / "_sandbox" / "monitoring_fixture"
fixture.mkdir(exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(fixture / "mplconfig")
os.environ["MPLBACKEND"] = "Agg"
sys.path.insert(0, str(ROOT))
os.chdir(fixture)
(fixture / "config.json").write_bytes((ROOT / "config.json").read_bytes())
from monitoring import equity_visualizer as plotter

total, safe = asyncio.run(plotter.get_total_equity())
plotter.save_history(total, safe)
plotter.save_history(total, safe)
result = dict(empty_fixture_total_equity=total, safe=safe,
              configured_strategy_capital=14, state_files=0, plot_rendered=plotter.create_plot())
(OUT / "monitoring_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(result)
