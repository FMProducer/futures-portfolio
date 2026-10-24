"""Real process death around ordinary market submission; file-backed fake exchange only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_market_only_partial_fill_e2e import CONFIG, _seed_files


WORKER = r'''
import asyncio, json, os, sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
root = Path(sys.argv[1]); sys.path[:0] = [str(root), str(root.parent)]
from tests.test_market_only_partial_fill_e2e import _make_connector, _make_notifier
from futures_portfolio.core import main
mode, point, fraction = sys.argv[2], sys.argv[3], float(sys.argv[4])
exchange = json.loads(Path('exchange.json').read_text())
connector = _make_connector(exchange)
original_save = main.save_json
original_sleep = asyncio.sleep

def create(**params):
    assert params['type'] == 'MARKET' and params['newClientOrderId'].startswith('reb-')
    assert not Path('exchange_order.json').exists(), 'duplicate order after restart'
    qty = float(params['quantity']) * fraction
    exchange['ZECUSDT_LONG'] -= qty
    Path('exchange.json').write_text(json.dumps(exchange))
    order = dict(orderId=77, symbol='ZECUSDT', side=params['side'],
                 positionSide=params['positionSide'], clientOrderId=params['newClientOrderId'],
                 status='FILLED' if fraction == 1 else 'PARTIALLY_FILLED',
                 origQty=params['quantity'], executedQty=str(qty), avgPrice='200.05')
    Path('exchange_order.json').write_text(json.dumps(order))
    if point == 'after_send': os._exit(99)
    return order

async def save(path, value):
    await original_save(path, value)
    if mode != 'crash': return
    if point == 'after_result' and value.get('rebalance_inflight', {}).get('results'):
        os._exit(99)
    if point == 'after_book' and value.get('share_fill_receipts'):
        os._exit(99)

async def sleep(seconds, *args, **kwargs):
    if seconds >= 86400: raise SystemExit(0)
    await original_sleep(0)

connector.futures_client.futures_create_order.side_effect = create
with patch.object(main, 'save_json', side_effect=save), \
     patch.object(main, 'self_kill_pm2', new=AsyncMock()), \
     patch.object(main, 'TelegramNotifier', return_value=_make_notifier()), \
     patch('asyncio.sleep', side_effect=sleep):
    asyncio.run(main.rebalance_loop(connector, 'config.json', 'state.json',
                                   'paper_state.json', MagicMock()))
'''


@pytest.mark.parametrize('point', ['after_send', 'after_result', 'after_book'])
@pytest.mark.parametrize('fraction', [1., .5])
def test_market_crash_preserves_intent_and_blocks_duplicate(tmp_path, point, fraction):
    root = Path(__file__).resolve().parents[1]
    seed = _seed_files()
    for name, value in seed.items():
        (tmp_path/name).write_text(json.dumps(value))
    (tmp_path/'config.json').write_text(json.dumps(CONFIG))
    (tmp_path/'exchange.json').write_text(json.dumps({'ZECUSDT_LONG':14., 'ZECUSDT_SHORT':2.}))
    worker = tmp_path/'worker.py'; worker.write_text(WORKER)
    def run(mode):
        return subprocess.run([sys.executable, '-I', str(worker), str(root), mode, point, str(fraction)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=30,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    crashed = run('crash')
    assert crashed.returncode == 99, crashed.stdout + crashed.stderr
    state = json.loads((tmp_path/'state.json').read_text())
    order = json.loads((tmp_path/'exchange_order.json').read_text())
    assert state['startup_blocked']
    intent = state['rebalance_inflight']
    assert intent['actions'][0]['client_order_id'] == order['clientOrderId']
    if point != 'after_send':
        assert intent['results'][0]['execution_id'] == 'binance:ZECUSDT:77'
    book_before = json.loads((tmp_path/'paper_state.json').read_text())
    if point == 'after_book':
        assert book_before['positions']['ZECUSDT_LONG'] == pytest.approx(14-float(order['executedQty']))
        assert book_before['balance'] == 1018.05
        assert len(book_before['share_fill_receipts']) == 1
    else:
        assert book_before == seed['paper_state.json']
    # A growing cumulative fill is ambiguous: preserve its exchange facts, never resubmit.
    if fraction < 1:
        order['executedQty'] = order['origQty']; order['status'] = 'FILLED'
        (tmp_path/'exchange_order.json').write_text(json.dumps(order))
        exchange = json.loads((tmp_path/'exchange.json').read_text())
        exchange['ZECUSDT_LONG'] = 14-float(order['origQty'])
        (tmp_path/'exchange.json').write_text(json.dumps(exchange))
    restarted = run('restart')
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    restored = json.loads((tmp_path/'state.json').read_text())
    assert restored['startup_blocked'] and restored['rebalance_inflight'] == intent
    if point == 'after_book':
        assert restored['share_trail']['BASE_LONG']['peak'] is None
        assert restored['share_trail_last_fill'] == 'binance:ZECUSDT:77'
    assert json.loads((tmp_path/'paper_state.json').read_text()) == book_before
    assert json.loads((tmp_path/'exchange_order.json').read_text()) == order
