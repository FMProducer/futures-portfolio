"""Measure native funding persistence; a bounded prefix is not an annual PASS."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys
import sqlite3
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import funding, storage, funding_store


def measure(config_path, events_path, directory, count=1000):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    config.pop('funding_reserve_receipts', None)
    config.pop('funding_reserve_totals', None)
    config.pop('funding_reserve_checkpoint', None)
    config['portfolios'][0]['cash_reserve_usdt'] = 5000
    path, sp, wp = (directory/name for name in ('config.json', 'strategy.json', 'wallet.json'))
    path.write_text(json.dumps(config), encoding='utf-8')
    events = json.loads(Path(events_path).read_text(encoding='utf-8'))
    if not 0 < count <= len(events):
        raise ValueError('Prefix count must be positive and within the supplied stream')
    state = dict(base_ticker='AGTUSDT', virt_debt=500, funding_model='signed_actual',
                 live_funding=dict(symbol='AGTUSDT', snapshot_ms=1))
    wallet = dict(base_ticker='AGTUSDT', balance=1000, positions={})
    journal, halt = funding.load_funding_state(state, str(path))
    original, written = storage._atomic_save_json, [0]

    def save(target, data, **kwargs):
        original(target, data, **kwargs)
        written[0] += Path(target).stat().st_size

    start = time.perf_counter()
    with funding_store.connection_session(), patch('core.storage._atomic_save_json', new=save):
        for index, row in enumerate(events[:count]):
            event = funding.FundingEvent.from_dict({**row, 'status': 'received', 'applied_at': None})
            assert journal.receive(event) == 'new'
            funding.save_funding_boundary(str(sp), str(wp), state, wallet, journal, halt)
            receipt = funding.settle_funding_reserve(str(path), 'real', 'AGTUSDT', event)
            funding.apply_funding_paper(event.signed_amount, wallet, state, receipt)
            journal.mark_applied(event.event_id)
            funding.save_funding_boundary(str(sp), str(wp), state, wallet, journal, halt)
            if (index+1) % 100 == 0:
                print(json.dumps(dict(processed=index+1, json_bytes_written=written[0],
                                      elapsed_seconds=time.perf_counter()-start)), flush=True)
    with closing(sqlite3.connect(str(path)+'.funding.sqlite')) as db:
        archived = db.execute('select count(*) from applied_events').fetchone()[0]
    result = dict(status='PERSISTENCE_COMPLETED_NOT_STRATEGY_PASS', events=count, annual_events=len(events),
                  annual_stream_completed=count==len(events), archived_events=archived,
                  elapsed_seconds=time.perf_counter()-start, native_json_bytes_written=written[0],
                  final_state_bytes=sp.stat().st_size, journal_events=len(journal),
                  funding_paid=state['funding_paid'], reserve=json.loads(path.read_text())['portfolios'][0]['cash_reserve_usdt'],
                  estimated_annual_json_bytes_linear=written[0]*(len(events)/count),
                  limits=['Persistence-only recorded funding prefix; no orders, market replay or worker lifecycle',
                          'Annual JSON volume is a linear prefix estimate, not measured unless the full stream completed',
                          'JSON byte count excludes SQLite and filesystem metadata writes'])
    (directory/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config'); parser.add_argument('events'); parser.add_argument('directory')
    parser.add_argument('--count', type=int, default=1000)
    args=parser.parse_args()
    print(json.dumps(measure(args.config,args.events,args.directory,args.count),indent=2))
