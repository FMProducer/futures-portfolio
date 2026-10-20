from decimal import Decimal as D

import pytest

from rebalance.historical import candles, funding_rows, replay
from rebalance.kernel import Config


def config():
    return Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09')


def bars(values):
    return [dict(time=i*60000, open=D(v), high=D(v), low=D(v), close=D(v))
            for i,v in enumerate(values)]


def test_flat_market_pays_entry_friction_without_phantom_pnl():
    result, trades, _ = replay(bars(['100']*10), config(), '10000', '2', '1', [])
    assert result['rebalances'] == 0 and len(trades) == 2
    assert D(result['net_mtm']) < -D(result['fees'])
    assert D(result['net_mtm']) == D(result['realized'])+D(result['unrealized'])-D(result['fees'])


def test_future_bar_does_not_change_earlier_execution():
    prefix = bars(['100','110','109','108'])
    _, first, _ = replay(prefix, config(), '10000', '2', '1', [])
    _, extended, _ = replay(prefix+[
        dict(time=240000, open=D(1000), high=D(1000), low=D(1000), close=D(1000))],
        config(), '10000', '2', '1', [])
    assert first == [t for t in extended if t['time'] < 240000]


def test_intrabar_high_low_not_used_as_signal():
    original = bars(['100']*5)
    changed = [dict(b, high=D(200), low=D(50)) for b in original]
    assert replay(original, config(), '10000', '0', '0', []) == replay(changed, config(), '10000', '0', '0', [])


def test_funding_affects_wallet_for_asymmetric_quantities():
    rows = bars(['100','110','109','108','107','106'])
    base, _, _ = replay(rows, config(), '10000', '0', '0', [])
    events = [dict(time=359999, rate=D('.01'), mark=D(106))]
    actual, _, _ = replay(rows, config(), '10000', '0', '0', events)
    assert actual['funding_events'] == 1 and D(actual['funding']) != 0
    assert D(actual['equity'])-D(base['equity']) == D(actual['funding'])


def test_rejects_outside_funding():
    with pytest.raises(ValueError, match='outside'):
        replay(bars(['100','100']), config(), '10000', '0', '0',
               [dict(time=999999, rate=D('.001'), mark=D(100))])


@pytest.mark.parametrize('second', [0, 120000])
def test_csv_rejects_duplicate_or_missing_candles(tmp_path, second):
    path = tmp_path/'bars.csv'
    path.write_text(f'open_time,open,high,low,close\n0,100,101,99,100\n{second},100,101,99,100\n')
    with pytest.raises(ValueError, match='contiguous'):
        candles(path)


def test_funding_csv_rejects_duplicate(tmp_path):
    path = tmp_path/'funding.csv'
    path.write_text('timestamp,rate,mark_price\n1,0.001,100\n1,0.001,100\n')
    with pytest.raises(ValueError, match='duplicate'):
        funding_rows(path)
