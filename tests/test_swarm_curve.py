import json
import sqlite3

from futures_portfolio.dashboard.swarm_curve import read_curve


def test_curve_preserves_net_gap_and_observed_drawdown(tmp_path):
    folder = tmp_path / "reports/paper_swarm_continuous"
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / "observations.sqlite") as db:
        db.execute("CREATE TABLE observations(ts REAL, gap_seconds REAL, summary TEXT)")
        for ts, net, gap in [(100, 40, None), (106, 10, 6), (200, 60, 94)]:
            db.execute("INSERT INTO observations VALUES(?,?,?)", (ts, gap, json.dumps(
                {"reported_cumulative_net": net, "fees_recorded": 9})))
    result = read_curve(tmp_path)
    assert result["points"] == [[100, 40, 0, False], [106, 10, 30, False], [200, 60, 0, True]]
    assert result["max_drawdown_usdt"] == 30
    assert result["error"] is None
    current = read_curve(tmp_path, (210, 25))
    assert current["points"][-1] == [210, 25, 35, False]
    assert current["max_drawdown_usdt"] == 35
    assert read_curve(tmp_path / "missing")["points"] == []
