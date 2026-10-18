"""One fixed synthetic replay suite; no data download, optimization or live orders."""
import json
from decimal import Decimal as D
from pathlib import Path

from .kernel import Config, Portfolio


def run():
    paths = {
        'constant': [D(100)]*1000,
        'trend': [D(100)+D(i)/100 for i in range(1000)],
        'reversals': [D(100)+D(i)/10 for i in range(100)]
            + [D(110)-D(i)/10 for i in range(200)]
            + [D(90)+D(i)/10 for i in range(100)],
    }
    results = {}
    for name, marks in paths.items():
        p = Portfolio(Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09'), '10000')
        actions = p.plan_entry(marks[0], marks[0], marks[0])
        for action in actions:
            p.fill('entry-'+action.side, action, marks[0],
                   action.units*p.config.step*marks[0]*p.config.fee_rate, marks[0])
            p.legs[action.side].initial_units = action.units
        count = 0
        # One decision per observation; no fabricated intra-candle path.
        for index, mark in enumerate(marks):
            action = p.next_action(mark, mark, mark)
            if action.kind != 'WAIT':
                p.fill(str(index), action, mark,
                       action.units*p.config.step*mark*p.config.fee_rate, mark)
                count += 1
        s = p.snapshot(marks[-1])
        results[name] = dict(observations=len(marks), equity=str(s['equity']),
            net_mtm=str(s['equity']-D(10000)), fees=str(p.fees),
            rebalances=count, terminal='OPEN_MTM', funding='0', spread='0', slippage='0')
        assert s['equity'] == p.wallet+sum((r['upl'] for r in s['legs'].values()), D(0))
        if name == 'constant':
            assert s['equity'] == D(10000)-p.fees and count == 0
    return results


if __name__ == '__main__':
    import sys
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(run(), indent=2), encoding='utf-8')
