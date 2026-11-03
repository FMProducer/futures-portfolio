from decimal import Decimal as D
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'zec_yearly'
r = json.loads((OUT / 'result.json').read_text())
records = {(row['step'], action['key']): action for row in r['rebalances'] for action in row['actions_detail']}
pos = {leg: [D(0), D(0)] for leg in ['BASE_LONG', 'BASE_SHORT']}
checks = []
for f in r['fills']:
    leg = f['leg']
    if leg not in pos:
        continue
    q, entry = pos[leg]
    size, price, mid = [D(str(f[k])) for k in ['qty', 'price', 'mid']]
    sign = D(1) if leg == 'BASE_LONG' else D(-1)
    add = f['side'] == ('BUY' if leg == 'BASE_LONG' else 'SELL')
    new_q = q + size if add else max(D(0), q - size)
    new_entry = (q * entry + size * price) / new_q if add else entry
    nav = new_q * (new_entry / 3 + sign * (mid - new_entry))
    tpv = D(str(f['tpv']))
    if f['trigger'] == 'rebalance' and not f.get('initial_build') and tpv > 0:
        action = records[(f['bar'], leg)]
        capped = D(str(action['approved_usdt'])) < D(str(action['requested_usdt'])) - D('1e-7') or action.get('reason') == 'capped_by_free_cash'
        cost = abs(price - mid) + price * D('.0004')
        unit = price / 3 + sign * (mid - price) + D('.25') * cost if add else entry / 3 + sign * (mid - entry) - D('.25') * cost
        tolerance = abs(unit) * D('.001') + D('1e-7')
        error = nav - D('.25') * tpv
        ok = error <= tolerance if add and capped else abs(error) <= tolerance
        checks.append({'bar': f['bar'], 'leg': leg, 'add': add, 'cash_capped': capped,
                       'target_error_usdt': float(error), 'tolerance_usdt': float(tolerance), 'ok': ok})
    pos[leg] = [new_q, new_entry if new_q else D(0)]
assert checks and all(row['ok'] for row in checks), [row for row in checks if not row['ok']][:8]
(OUT / 'target_checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
summary = {'checked_fills': len(checks), 'reductions': sum(not row['add'] for row in checks),
           'additions': sum(row['add'] for row in checks), 'cash_capped_additions': sum(row['add'] and row['cash_capped'] for row in checks),
           'all_ok': True}
(OUT / 'verification.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
