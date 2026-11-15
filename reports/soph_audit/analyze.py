import json
import re
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p = Path(__file__).parent
rows = []
for line in (p / 'paper.log').read_text(encoding='utf-8').splitlines():
    if line[:10] != '2026-11-15' or line[11:19] < '08:53:14':
        continue
    match = re.search(r'Heartbeat: TPV=([\d.]+).*SOPHUSDT=([\d.]+)', line)
    if match:
        rows.append(dict(time=line[:23], tpv=float(match[1]), price=float(match[2])))
frame = pd.DataFrame(rows)
frame['time'] = pd.to_datetime(frame.time, format='%Y-%m-%d %H:%M:%S,%f')
frame.to_csv(p / 'heartbeat.csv', index=False)
fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
axes[0].plot(frame.time, frame.price)
axes[0].set_ylabel('SOPHUSDT price')
axes[1].plot(frame.time, frame.tpv)
axes[1].axhline(1044.18722839, color='gray', linestyle='--')
axes[1].set_ylabel('Portfolio TPV, USDT')
for clock, label in [('10:31:03','Short reduction'), ('12:14:06','Short reduction'),
                     ('12:17:10','Long addition'), ('13:23:04','Long addition')]:
    moment = pd.Timestamp('2026-11-15 ' + clock)
    for ax in axes:
        ax.axvline(moment, color='red', alpha=.4)
    axes[0].annotate(label, (moment, frame.loc[(frame.time-moment).abs().idxmin(), 'price']),
                     rotation=65, fontsize=8)
axes[1].set_xlabel('2026-11-15, runtime local time (UTC+02:00)')
fig.tight_layout()
fig.savefig(p / 'session.png', dpi=140)
close = json.loads((p / 'close.json').read_text())
fills = list(close['paper_state']['share_fill_receipts'].values())
(p / 'reconciliation.json').write_text(json.dumps(dict(
    realized_pnl=sum(float(f['trade_pnl']) for f in fills),
    commission=sum(float(f['commission']) for f in fills),
    initial=close['state']['initial_tpv'], final=close['state']['last_tpv']), indent=2))
