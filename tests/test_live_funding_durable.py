"""Durable live submission tests using only a file-backed fake exchange."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('point', ['before_send', 'after_send', 'partial_after_send'])
def test_restart_never_resubmits_uncertain_live_order(tmp_path, point):
    worker = r'''
import asyncio, json, os, sys
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, sys.argv[1])
from core import funding
from core.main import _run_live_funding_closes
mode, point = sys.argv[2:4]
if mode == 'crash':
    halt = funding.commit_funding_halt_with_intents(funding.FundingHalt(), 'e1', D('1'), D('1'), D('1'))
    state = {'funding_halt': halt.to_dict()}
    wallet = {'balance': 100., 'positions': {'BTCUSDT_LONG':1., 'BTCUSDT_SHORT':1.}, 'last_price':100., 'long_entry_price':100., 'short_entry_price':100.}
else:
    funding.recover_funding_boundary('state.json', 'wallet.json')
    state = json.loads(Path('state.json').read_text())
    wallet = json.loads(Path('wallet.json').read_text())
    halt = funding.FundingHalt.from_dict(state['funding_halt'])
journal = funding.FundingJournal()

class Exchange:
    async def get_positions(self):
        return {key: {'qty':qty} for key,qty in wallet['positions'].items()}
    async def execute_market_order(self, **kwargs):
        assert kwargs['reduce_only'] and kwargs['client_order_id']
        if mode == 'crash' and point == 'before_send':
            os._exit(73)
        orders = json.loads(Path('exchange.json').read_text()) if Path('exchange.json').exists() else {}
        assert kwargs['client_order_id'] not in orders, 'Duplicate exchange submission'
        amount = float(kwargs['qty']) * (.5 if point == 'partial_after_send' and mode == 'crash' else 1)
        order = dict(orderId=len(orders)+1, clientOrderId=kwargs['client_order_id'],
            symbol=kwargs['symbol'], side=kwargs['side'], positionSide=kwargs['position_side'],
            executedQty=str(amount), origQty=str(kwargs['qty']), avgPrice='100',
            status='CANCELED' if amount < float(kwargs['qty']) else 'FILLED')
        orders[kwargs['client_order_id']] = order
        Path('exchange.json').write_text(json.dumps(orders))
        if mode == 'crash':
            os._exit(73)
    async def get_order_by_client_id(self, symbol, client):
        orders = json.loads(Path('exchange.json').read_text()) if Path('exchange.json').exists() else {}
        if client not in orders:
            raise TimeoutError('No authoritative order result available')
        return orders[client]
    async def get_order_status(self, symbol, oid):
        return next(o for o in json.loads(Path('exchange.json').read_text()).values() if o['orderId'] == oid)
    async def get_order_trades(self, symbol, oid):
        order = await self.get_order_status(symbol, oid)
        return [dict(id=oid, orderId=oid, symbol=symbol, side=order['side'],
            positionSide=order['positionSide'], qty=order['executedQty'], price='100',
            commission='.01', commissionAsset='USDT', realizedPnl='0')]

exchange = Exchange()
asyncio.run(_run_live_funding_closes(exchange, exchange, halt, state, wallet,
    'BTCUSDT', 'state.json', 'wallet.json', journal, {'BTCUSDT':.001}, {},
    MagicMock(), fresh=mode == 'crash'))
'''
    root = str(Path(__file__).resolve().parents[1])
    for mode, code in (('crash', 73), ('restart', 0)):
        result = subprocess.run([sys.executable, '-I', '-c', worker, root, mode, point],
            cwd=tmp_path, capture_output=True, text=True, timeout=30)
        assert result.returncode == code, result.stdout + result.stderr
    state = json.loads((tmp_path / 'state.json').read_text())
    wallet = json.loads((tmp_path / 'wallet.json').read_text())
    orders = json.loads((tmp_path / 'exchange.json').read_text())
    legs = state['funding_halt']['close_state']
    assert state['funding_halt']['active']
    assert len(orders) == (1 if point == 'before_send' else 2)
    assert wallet['balance'] == pytest.approx(99.99 if point == 'before_send' else 99.98)
    assert wallet['positions']['BTCUSDT_SHORT'] == 0
    if point == 'before_send':
        assert wallet['positions']['BTCUSDT_LONG'] == 1
        assert legs['BASE_LONG']['status'] == 'quarantined'
    elif point == 'partial_after_send':
        assert wallet['positions']['BTCUSDT_LONG'] == .5
        assert legs['BASE_LONG']['status'] == 'quarantined'
        assert legs['BASE_LONG']['qty_closed'] == .5
    else:
        assert wallet['positions']['BTCUSDT_LONG'] == 0
        assert legs['BASE_LONG']['status'] == 'filled'
