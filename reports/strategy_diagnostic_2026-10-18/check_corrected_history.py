"""Price-sequence diagnostic using only the user-designated local history."""
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path

import pandas as pd

from rebalance.kernel import Config, Portfolio


def main(export_plot=False, output_name='corrected_history_check.json'):
    root = Path(__file__).resolve().parents[2]
    source = Path(r'C:\Python\Prosperous_Bot\futures_portfolio\data\BTCUSDT_live_365d.feather')
    data = pd.read_feather(source)
    config_path = root/'rebalance/historical_example.json'
    settings = json.loads(config_path.read_text())
    config = Config(**settings['strategy'])
    p = Portfolio(config, settings['capital'])
    friction = (D(settings['spread_bps'])/2+D(settings['slippage_bps']))/10000
    previous_close = None
    previous_equity = p.wallet
    peak = p.wallet
    drawdown = D(0)
    worst = None
    fills = 0
    max_residual = D(0)
    max_delta = D(0)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    curve = []
    for index, row in enumerate(data.itertuples(index=False)):
        opening, close = D(str(row.open)), D(str(row.close))
        assert min(opening, close, D(str(row.low))) > 0
        assert D(str(row.low)) <= min(opening, close) <= max(opening, close) <= D(str(row.high))
        bid, ask = opening*(1-friction), opening*(1+friction)
        delta = (p.legs['LONG'].units-p.legs['SHORT'].units)*config.step
        price_pnl = delta*(close-(opening if previous_close is None else previous_close))
        actions = (p.plan_entry(opening, bid, ask) if index == 0
                   else [p.next_action(previous_close, bid, ask)])
        fill_pnl = D(0)
        for action in actions:
            if action.kind == 'WAIT':
                assert index != 0, action.reason
                continue
            price = ask if (action.kind == 'ADD') == (action.side == 'LONG') else bid
            quantity = action.units*config.step
            fee = quantity*price*config.fee_rate
            p.fill(f'row-{index}-{action.side}', action, price, fee, opening)
            if index == 0:
                p.legs[action.side].initial_units = action.units
            signed = quantity*(1 if (action.kind == 'ADD') == (action.side == 'LONG') else -1)
            fill_pnl += signed*(close-price)-fee
            fills += 1
        snapshot = p.snapshot(close)
        equity = snapshot['equity']
        residual = equity-previous_equity-price_pnl-fill_pnl
        max_residual = max(max_residual, abs(residual))
        assert abs(residual) < D('1e-19'), (index, residual)
        assert equity > snapshot['margin'], 'collateral exhausted'
        for side, leg in p.legs.items():
            value = leg.units*config.step*leg.avg+snapshot['legs'][side]['upl']-leg.open_fees
            assert snapshot['legs'][side]['decision'] == value
        change = equity-previous_equity
        if worst is None or change < D(worst['equity_change']):
            worst = dict(row_zero_based=index, equity_change=str(change),
                         prior_close=str(previous_close), close=str(close),
                         delta_before_btc=str(delta), price_pnl=str(price_pnl), fills_pnl_less_fees=str(fill_pnl))
        max_delta = max(max_delta, abs((p.legs['LONG'].units-p.legs['SHORT'].units)*config.step))
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak-equity)/peak*100)
        if export_plot:
            curve.append((float(equity), float((equity-peak)/peak*100),
                          float(snapshot['legs']['LONG']['share']), float(snapshot['legs']['SHORT']['share']),
                          float(p.legs['LONG'].units*config.step), float(p.legs['SHORT'].units*config.step)))
        previous_close, previous_equity = close, equity
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    result = dict(status='PRICE_SEQUENCE_DIAGNOSTIC_ONLY', source=str(source), source_sha256=source_hash,
                  rows=len(data), settings=settings, fills=fills, rebalances=fills-2,
                  equity=str(equity), fees=str(p.fees), net_excluding_funding=str(equity-D(settings['capital'])),
                  max_close_drawdown_pct=str(drawdown), worst_equity_step=worst,
                  max_absolute_delta_btc=str(max_delta), max_arithmetic_residual=str(max_residual),
                  quantities={side:str(leg.units*config.step) for side,leg in p.legs.items()},
                  code_sha256={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                               for name in ('rebalance/kernel.py', 'rebalance/durable.py')},
                  limitations=['No timestamps in source: calendar coverage and minute continuity unverified.',
                               'Funding unavailable and excluded, not assumed economically zero.',
                               'Open positions marked to close; no terminal close, cycle policy or optimization.',
                               'Synthetic execution spread/slippage; no depth or liquidation model.'],
                  trading_authorized=False)
    output = Path(__file__).with_name(output_name)
    if export_plot:
        if output.exists():
            assert result == json.loads(output.read_text(encoding='utf-8')), 'replay differs from saved result'
        else:
            with output.open('x',encoding='utf-8') as stream:
                json.dump(result,stream,indent=2)
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        values = np.asarray(curve)
        np.savez_compressed(output.with_suffix('.npz'), values=values,
                            columns=['equity','drawdown_pct','long_share_pct','short_share_pct','long_btc','short_btc'])
        fig, axes = plt.subplots(4, 1, figsize=(15, 12), sharex=True,
                                 gridspec_kw={'height_ratios':[2,1,1,1]}, layout='constrained')
        x = np.arange(len(values))
        axes[0].plot(x, values[:,0], color='#187caa', linewidth=.7)
        axes[0].axhline(float(settings['capital']), color='gray', linestyle='--', linewidth=.8)
        axes[0].scatter([len(x)-1], [values[-1,0]], color='#a42d40', s=28, zorder=4)
        axes[0].set_title(f'BTCUSDT — {output.stem}, {len(data):,} строк\n'
                          f'Equity: {float(equity):,.2f} USDT | Net: {float(equity-D(settings["capital"])):+,.2f} USDT | '
                          f'Комиссии: {float(p.fees):,.2f} USDT')
        axes[0].set_ylabel('Equity, USDT')
        axes[1].fill_between(x, values[:,1], 0, color='#b44d60', alpha=.55, linewidth=0)
        axes[1].set_ylabel('Просадка, %')
        axes[1].set_title(f'Максимальная просадка: {float(drawdown):.3f}%', loc='left', fontsize=10)
        for column, label, color in ((2,'LONG','#187caa'),(3,'SHORT','#dc8b25')):
            axes[2].plot(x, values[:,column], label=label, color=color, linewidth=.7)
        axes[2].axhline(25, color='gray', linestyle='--', linewidth=.8)
        axes[2].set_ylabel('Доля Equity, %')
        axes[2].legend(loc='upper right', ncol=2)
        axes[3].plot(x, values[:,4], label='LONG', color='#187caa', linewidth=.7)
        axes[3].plot(x, values[:,5], label='SHORT', color='#dc8b25', linewidth=.7)
        axes[3].set_ylabel('Количество, BTC')
        axes[3].set_xlabel('Номер строки исходного файла — временные метки отсутствуют')
        axes[3].legend(loc='upper right', ncol=2)
        for ax in axes:
            ax.grid(alpha=.18)
        fig.suptitle(f'Funding не учтён • Позиции на финише открыты • {fills-2} ребалансировок\n'
                     'Проверены формулы долей и поминутный баланс Equity', fontsize=11)
        image_path = output.with_suffix('.png')
        fig.savefig(image_path, dpi=140)
        plt.close(fig)
        print(str(image_path))
        return
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--plot',action='store_true')
    parser.add_argument('--output',default='corrected_history_check.json')
    args = parser.parse_args()
    main(export_plot=args.plot,output_name=args.output)
