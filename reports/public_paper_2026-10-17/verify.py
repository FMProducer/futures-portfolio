"""Reproduce recorded public observations offline and verify regression evidence."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

from rebalance.public_paper import Paper

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    binding = read(HERE/'verified_probe/binding.json')
    for name,digest in binding['code'].items():
        assert hashlib.sha256((ROOT/'rebalance'/name).read_bytes()).hexdigest() == digest
    baseline = ROOT/'reports/historical_2026-10-17/BTCUSDT'
    for name in ('result','trades','equity'):
        assert read(baseline/(name+'.json')) == read(HERE/'verified_BTCUSDT'/(name+'.json'))
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory)/'paper.sqlite'
        for index in range(2):
            runner = Paper(db,binding['symbol'],binding['settings'],binding['filters'])
            sample = read(HERE/f'verified_probe/sample_{index}.json')
            expected = read(HERE/f'verified_probe/result_{index}.json')
            assert runner.step(sample) == expected
            revision = runner.store.load()[1]
            assert runner.step(sample) == expected
            assert runner.store.load()[1] == revision
        assert len(runner.store.load()[0].receipts) == 2
        assert runner.store.orders() == []
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('SELECT count(*) FROM events').fetchone()[0] == 2
    suite = ET.parse(HERE/'verified_tests.xml').getroot()[0]
    assert suite.attrib['tests'] == '176'
    assert suite.attrib['failures'] == suite.attrib['errors'] == '0'
    output = dict(status='PASS',tests=176,public_observations=2,simulated_entry_fills=2,
        duplicate_additional_fills=0,real_orders=0,regression='result/trades/equity exact match',
        current_code_matches_capture=True,trading_authorized=False)
    (HERE/'verification.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(output,indent=2))


if __name__ == '__main__':
    main()
