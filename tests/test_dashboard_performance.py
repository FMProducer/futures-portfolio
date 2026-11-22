"""Offline regression checks for dashboard I/O and incremental history."""

import json
import sqlite3
from pathlib import Path

import pytest

from dashboard import log_reader
from dashboard import swarm_curve
from dashboard.swarm_history import _log_samples


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_log_tail_count_append_partial_and_rotation(tmp_path, ending):
    path = tmp_path / "bot.log"
    path.write_bytes(ending.join([b"one", b"two", "three é".encode()]) + ending)
    assert log_reader.read_tail(path, 2) == ["two", "three é"]
    assert log_reader.read_tail(path, 2, 2) == ["one"]
    assert log_reader.read_tail(path, 2, 100) == []
    assert log_reader.line_count(path) == 3
    with path.open("ab") as stream:
        stream.write(b"four")
    assert log_reader.line_count(path) == 4
    with path.open("ab") as stream:
        stream.write(ending + b"five" + ending)
    assert log_reader.line_count(path) == 5
    assert log_reader.read_tail(path, 2) == ["four", "five"]
    path.write_bytes(b"replacement\n")
    assert log_reader.line_count(path) == 1
    replacement = tmp_path / "new.log"
    replacement.write_bytes(b"rotated\nnext\n")
    replacement.replace(path)
    assert log_reader.line_count(path) == 2
    assert log_reader.read_tail(path, 1) == ["next"]


def test_large_log_warm_reads_only_tail_and_appended_bytes(tmp_path, monkeypatch):
    path = tmp_path / "bot.log"
    path.write_bytes(b"old log line\n" * 200000)
    assert log_reader.line_count(path) == 200000
    original = Path.open
    reads = []

    class Tracked:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            body = self.stream.read(size)
            reads.append(len(body))
            return body

    def tracked(path, *args, **kwargs):
        return Tracked(original(path, *args, **kwargs))

    with path.open("ab") as stream:
        stream.write(b"latest\n")
    monkeypatch.setattr(Path, "open", tracked)
    assert log_reader.read_tail(path, 2) == ["old log line", "latest"]
    assert log_reader.line_count(path) == 200001
    assert sum(reads) < 10000


def test_history_samples_parse_only_new_complete_lines(tmp_path):
    path = tmp_path / "bot.log"
    first = b"2026-11-22 10:00:00 INFO Heartbeat: TPV=100.0\n"
    second = b"2026-11-22 10:01:00 INFO Heartbeat: TPV=90.0"
    path.write_bytes(first + second)
    assert [v for _, v in _log_samples(path)] == [100, 90]
    assert log_reader.line_count(path) == 2
    assert [v for _, v in _log_samples(path)] == [100, 90]
    with path.open("ab") as stream:
        stream.write(b"\n2026-11-22 10:02:00 INFO Rebalance #1 complete. TPV: 105\n")
    assert [v for _, v in _log_samples(path)] == [100, 90, 105]
    path.write_bytes(first)
    assert [v for _, v in _log_samples(path)] == [100]


def test_curve_decodes_only_new_rows_and_reloads_edits(tmp_path, monkeypatch):
    path = tmp_path / "reports/paper_swarm_continuous/observations.sqlite"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observations(ts REAL PRIMARY KEY, gap_seconds REAL, summary TEXT)")
        db.executemany("INSERT INTO observations VALUES(?,?,?)", [
            (100, None, '{"reported_cumulative_net":40}'),
            (106, 6, '{"reported_cumulative_net":10}')])
    original = json.loads
    decoded = []

    def loads(body):
        decoded.append(body)
        return original(body)

    monkeypatch.setattr(swarm_curve.json, "loads", loads)
    first = swarm_curve.read_curve(tmp_path)
    assert len(decoded) == 2
    assert swarm_curve.read_curve(tmp_path) == first
    assert len(decoded) == 2
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO observations VALUES(200,94,?)", ('{"reported_cumulative_net":60}',))
    second = swarm_curve.read_curve(tmp_path, (210, 25), initial=10000)
    assert len(decoded) == 3
    assert second["points"][-1] == [210, 10025, 35, False]
    assert second["max_drawdown_usdt"] == 35
    assert second["max_drawdown_pct"] == pytest.approx(35 / 10060 * 100)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE observations SET summary=? WHERE ts=106", ('{"reported_cumulative_net":0}',))
    assert swarm_curve.read_curve(tmp_path)["max_drawdown_usdt"] == 40
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM observations WHERE ts=100")
    assert swarm_curve.read_curve(tmp_path)["points"][0][0] == 106


def test_plot_reduction_preserves_extrema_gaps_and_drawdown():
    points = [[i, float(i % 17), float(i % 11), False] for i in range(10000)]
    points[1234] = [1234, -100, 200, False]
    points[5678] = [5678, 1000, 0, True]
    reduced = swarm_curve._plot_points(points)
    assert len(reduced) <= 2002
    assert reduced[0] == points[0] and reduced[-1] == points[-1]
    assert points[1234] in reduced and points[5678] in reduced and points[5677] in reduced
