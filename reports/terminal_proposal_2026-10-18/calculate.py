"""Arithmetic sensitivity of existing evidence only; not a replay or policy."""
import csv
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path


def calculate():
    root = Path(__file__).resolve().parents[2]
    study = root/'reports/research_runner_2026-10-18/fixed_BTCUSDT'
    paths = {name: study/name for name in (
        'candidate_result.json', 'hold_result.json', 'frozen/inputs/config.json',
        'frozen/inputs/data.csv')}
    captured = {name: path.read_bytes() for name, path in paths.items()}
    config = json.loads(captured['frozen/inputs/config.json'])
    rows = csv.DictReader(captured['frozen/inputs/data.csv'].decode('utf-8-sig').splitlines())
    last = None
    for row in rows:
        last = row
    mark = D(last['close'])
    friction = (D(config['spread_bps'])/2+D(config['slippage_bps']))/10000
    bid, ask = mark*(1-friction), mark*(1+friction)
    fee_rate = D(config['strategy']['fee_rate'])
    arms = {}
    for arm in ('candidate', 'hold'):
        result = json.loads(captured[arm+'_result.json'])
        assert result['terminal'] == 'OPEN_MTM'
        assert result['end'] == int(last['open_time'])+59999
        ql, qs = (D(result['quantities'][side]) for side in ('LONG', 'SHORT'))
        adjustment = ql*(bid-mark)+qs*(mark-ask)
        exit_fees = (ql*bid+qs*ask)*fee_rate
        equity = D(result['equity'])+adjustment-exit_fees
        net = equity-D(result['initial_equity'])
        arms[arm] = {key: str(value) for key, value in dict(
            long_quantity=ql, short_quantity=qs, open_net=D(result['net_mtm']),
            adverse_price_adjustment=adjustment, exit_fees=exit_fees,
            estimated_flat_equity=equity, estimated_flat_net=net,
            added_exit_cost=-adjustment+exit_fees).items()}
    return dict(status='ILLUSTRATION_NOT_ADOPTED', replay_performed=False,
        inputs={name: dict(path=str(paths[name]), sha256=hashlib.sha256(data).hexdigest())
                for name, data in captured.items()},
        terminal_time=int(last['open_time'])+59999, close_proxy=str(mark),
        synthetic_sell_price=str(bid), synthetic_buy_price=str(ask),
        settings=config, arms=arms,
        estimated_flat_delta=str(D(arms['candidate']['estimated_flat_net'])-
                                 D(arms['hold']['estimated_flat_net'])),
        admission_ready=False, trading_authorized=False,
        limitations=['candle close proxy; synthetic quotes, full fills, no execution proof',
                     'no intrabar liquidation/depth/latency/partial or extra time funding',
                     'existing fees/funding already in open_net; only incremental exit costs subtracted',
                     'diagnostic illustration on previously used data, no PF/OOS evaluation'])


if __name__ == '__main__':
    value = calculate()
    with Path(__file__).with_name('illustration.json').open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
    print(json.dumps(value['arms'], indent=2))
    print('estimated_flat_delta:', value['estimated_flat_delta'])
