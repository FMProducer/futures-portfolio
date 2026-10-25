import sqlite3

from futures_portfolio.scripts.watch_paper_swarm import sample


def test_observer_keeps_offsets_and_records_gap(tmp_path):
    logs = tmp_path / "core/logs"
    logs.mkdir(parents=True)
    path = logs / "paper_X.log"
    path.write_bytes(b"first\n")
    with sqlite3.connect(":memory:") as db:
        sample(db, tmp_path, 100)
        sample(db, tmp_path, 105)
        assert db.execute("SELECT count(*) FROM log_chunks").fetchone()[0] == 1
        with path.open("ab") as stream:
            stream.write(b"second\n")
        sample(db, tmp_path, 125)
        chunks = db.execute("SELECT body FROM log_chunks ORDER BY offset").fetchall()
        assert b"".join(row[0] for row in chunks) == b"first\nsecond\n"
        assert db.execute("SELECT gap_seconds FROM observations WHERE ts=125").fetchone()[0] == 20
        path.write_bytes(b"rotated\n")
        sample(db, tmp_path, 130)
        assert db.execute("SELECT max(generation) FROM log_chunks").fetchone()[0] == 1
