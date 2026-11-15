"""Reconstruct recorded PAPER collateral from execution receipts and heartbeats."""
import json
import logging
import re
import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.main import _paper_guard_metrics

source = ROOT / 'reports/soph_audit'
close = json.loads((source / 'close.json').read_text())
receipts = list(close['paper_state']['share_fill_receipts'].values())[:6]
wallet = {'balance': D(str(close['state']['initial_tpv'])),
          'positions': {'SOPHUSDT_LONG': D(0), 'SOPHUSDT_SHORT': D(0)},
          'long_entry_price': D(0), 'short_entry_price': D(0)}
pending = iter(receipts)
recorded = []
applied = 0
for line in (source / 'paper.log').read_text(encoding='utf-8').splitlines():
    if line[:10] != '2026-11-15' or line[11:19] < '08:53:14':
        continue
    if 'PAPER: ' in line:
        fill = next(pending)
        assert str(int(D(fill['qty']))) in line and fill['side'] in line
        symbol = fill['symbol']
        entry = 'long_entry_price' if fill['type'] == 'LONG' else 'short_entry_price'
        qty, price = D(fill['qty']), D(fill['price'])
        old = wallet['positions'][symbol]
        if fill['reduce_only'] == 'True':
            wallet['positions'][symbol] -= qty
        else:
            wallet[entry] = (old * wallet[entry] + qty * price) / (old + qty)
            wallet['positions'][symbol] += qty
        wallet['balance'] += D(fill['trade_pnl']) - D(fill['commission'])
        applied += 1
    match = re.search(r'Heartbeat: TPV=([\d.]+).*SOPHUSDT=([\d.]+)', line)
    if match:
        price = D(match[2])
        tpv = (wallet['balance'] + wallet['positions']['SOPHUSDT_LONG'] * (price-wallet['long_entry_price'])
               + wallet['positions']['SOPHUSDT_SHORT'] * (wallet['short_entry_price']-price))
        assert abs(tpv-D(match[1])) <= D('.006'), (line, tpv)
        collateral, maintenance, distance = _paper_guard_metrics(wallet, 'SOPHUSDT', price,
            tpv, logging.getLogger('audit'), 5000)
        recorded.append(dict(time=line[:23], tpv=float(tpv), collateral=float(collateral),
            maintenance=float(maintenance), distance=distance,
            no_reserve_distance=float((tpv-maintenance)/tpv*100)))
assert applied == 6
summary = dict(heartbeats=len(recorded), fills=applied,
    min_tpv=min(recorded, key=lambda r: r['tpv']),
    min_margin_distance=min(recorded, key=lambda r: r['distance']),
    min_no_reserve_distance=min(recorded, key=lambda r: r['no_reserve_distance']))
Path(__file__).with_name('margin_replay.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
