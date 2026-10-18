"""Render the completed B00 evidence without recalculating the strategy."""
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
import math
from pathlib import Path
import urllib.request

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np


def main():
    base=Path(__file__).resolve().parent
    study=base/'runtime/studies/B00'
    output=base/'baseline_visual'
    output.mkdir(exist_ok=True)
    load=lambda name:json.loads((study/(name+'.json')).read_text(encoding='utf-8-sig'))
    summary=load('comparison')
    result=summary['candidate']
    capital=D(result['initial_equity'])
    curves={arm:load(arm+'_equity') for arm in ('candidate','hold')}
    times=np.array([p['time'] for p in curves['candidate']],dtype=np.int64)
    assert all([p['time'] for p in curves[arm]]==times.tolist() for arm in ('candidate','hold'))
    values={arm:np.array([float(p['equity']) for p in rows]) for arm,rows in curves.items()}
    dd={arm:(v/np.maximum.accumulate(np.r_[float(capital),v])[1:]-1)*100 for arm,v in values.items()}
    assert math.isclose(-min(dd['candidate']),float(result['max_close_drawdown_pct']),abs_tol=1e-9)
    selected={0,len(times)-1}
    # Preserve each six-hour bucket's extrema and worst drawdown for readable annual charts.
    for start in range(0,len(times),360):
        stop=min(start+360,len(times))
        selected.update((start,stop-1))
        for arm in values:
            selected.update(start+int(f(values[arm][start:stop])) for f in (np.argmin,np.argmax))
            selected.add(start+int(np.argmin(dd[arm][start:stop])))
    idx=sorted(selected)
    dates=[datetime.fromtimestamp(int(times[i])/1000,timezone.utc) for i in idx]
    terminal=summary['terminal_closeout']
    parts=[D(result['realized']),D(result['unrealized']),D(result['funding']),-D(result['fees']),
           D(terminal['candidate']['price_adjustment']),-D(terminal['candidate']['exit_fees'])]
    labels=['Realized','Open PnL','Funding','Trading fees','Exit spread/slip','Exit fees']
    net=D(terminal['candidate']['net_flat'])
    assert abs(sum(parts)-net)<D('1e-20')
    traces=[]
    for arm,color,label in (('candidate','#52d4c8','Strategy'),('hold','#f0ba68','Hold')):
        traces.append(dict(name=label,x=[d.isoformat() for d in dates],
            y=[values[arm][i]-float(capital) for i in idx],type='scatter',mode='lines',line=dict(color=color,width=1.5)))
        traces.append(dict(name=label+' closeout',x=[dates[-1].isoformat()],
            y=[float(terminal[arm]['net_flat'])],type='scatter',mode='markers',
            marker=dict(color=color,size=11,symbol='diamond'),showlegend=True))
    colors={'candidate':'#52d4c8','hold':'#f0ba68'}
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'figure.facecolor':'#101722',
        'axes.facecolor':'#101722','axes.edgecolor':'#344052','text.color':'#e4ebf5',
        'axes.labelcolor':'#b8c5d6','xtick.color':'#a2b0c2','ytick.color':'#a2b0c2','grid.color':'#293344'})
    fig,axes=plt.subplots(3,1,figsize=(15,12),gridspec_kw=dict(height_ratios=[2.2,1.2,1.5]))
    fig.suptitle('BTCUSDT / ANNUAL BASELINE B00\n01 Oct 2025 - 30 Sep 2026 / UTC',fontsize=19,fontweight='bold',y=.98)
    fig.text(.07,.906,f'Net after closeout  {net:,.2f} USDT     |     Return  {net/capital*100:.3f}%     |     Max DD  {D(result["max_close_drawdown_pct"]):.3f}%     |     {result["rebalances"]} rebalances',fontsize=12)
    for arm in ('candidate','hold'):
        axes[0].plot(dates,[values[arm][i]-float(capital) for i in idx],label=arm.title(),color=colors[arm],lw=1.3)
        axes[0].scatter(dates[-1],float(terminal[arm]['net_flat']),marker='D',s=40,color=colors[arm],zorder=5)
        axes[1].plot(dates,[dd[arm][i] for i in idx],color=colors[arm],lw=1.2,label=arm.title())
    axes[0].axhline(0,color='#64748b',lw=.8,ls='--')
    axes[0].set_title('Portfolio Net: open MTM curve; diamond = synthetic closeout',loc='left',pad=12)
    axes[0].set_ylabel('USDT'); axes[0].legend(loc='lower left',facecolor='#101722',labelcolor='#e4ebf5')
    axes[1].set_title('Drawdown from prior Equity peak',loc='left',pad=10); axes[1].set_ylabel('%')
    for ax in axes[:2]:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2)); ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax.grid(alpha=.45); ax.spines[['top','right']].set_visible(False)
    bottoms=[]; cursor=D(0)
    for part in parts: bottoms.append(float(min(cursor,cursor+part))); cursor+=part
    axes[2].bar(range(len(parts)),[abs(float(v)) for v in parts],bottom=bottoms,
                color=['#52d4c8' if v>=0 else '#ef7a81' for v in parts],width=.65)
    axes[2].bar(len(parts),float(net),color='#f0ba68',width=.65)
    for i,(name,part) in enumerate(zip(labels,parts)):
        axes[2].annotate(f'{part:+,.2f}',(i,bottoms[i]+abs(float(part))),xytext=(0,5),textcoords='offset points',ha='center',fontsize=9)
    axes[2].annotate(f'{net:,.2f}',(len(parts),0),xytext=(0,8),textcoords='offset points',ha='center',fontweight='bold')
    axes[2].set_xticks(range(len(parts)+1),labels+['Net closeout'])
    axes[2].axhline(0,color='#64748b',lw=.8); axes[2].set_ylabel('USDT')
    axes[2].set_title('PnL bridge: realized gains do not equal portfolio profit',loc='left',pad=16)
    axes[2].grid(axis='y',alpha=.4); axes[2].spines[['top','right']].set_visible(False)
    fig.text(.07,.025,'Research only | Initial capital 10,000 USDT | Actual funding history | Synthetic full fills / no intrabar liquidation\nCurve: extrema-preserving reduction of 525,600 minute observations. Metrics use complete evidence. No trading admission.',fontsize=10,color='#a2b0c2')
    fig.subplots_adjust(top=.86,bottom=.10,hspace=.40,left=.075,right=.97)
    fig.savefig(output/'baseline.png',dpi=145)
    plt.close(fig)
    dd_traces=[dict(name=arm.title(),type='scatter',mode='lines',x=[d.isoformat() for d in dates],
        y=[dd[arm][i] for i in idx],line=dict(color=colors[arm])) for arm in values]
    trades=load('candidate_trades')
    step=D(load('execution_manifest')['settings']['strategy']['step'])
    quantities={'LONG':D(0),'SHORT':D(0)}
    positions={s:dict(name=s,x=[],y=[],type='scatter',mode='lines',line=dict(shape='hv')) for s in quantities}
    for trade in trades+terminal['candidate']['fills']:
        quantities[trade['side']]+=trade['units']*step*(1 if trade['kind']=='ADD' else -1)
        for side in quantities:
            positions[side]['x'].append(datetime.fromtimestamp(trade['time']/1000,timezone.utc).isoformat())
            positions[side]['y'].append(float(quantities[side]))
    assert not any(quantities.values())
    payload=dict(net=traces,dd=dd_traces,positions=list(positions.values()),
        bridge=[dict(type='waterfall',x=labels+['Net closeout'],y=[float(p) for p in parts]+[0],
                     measure=['relative']*len(parts)+['total'],connector=dict(line=dict(color='#64748b')))])
    js_url='https://cdn.plot.ly/plotly-4.1.1.min.js'
    if (output/'plotly.min.js').exists(): bundle=(output/'plotly.min.js').read_bytes()
    else:
        with urllib.request.urlopen(js_url,timeout=30) as response: bundle=response.read()
    (output/'plotly.min.js').write_bytes(bundle)
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTCUSDT annual baseline B00</title><script src="plotly.min.js"></script><style>
body{margin:0;background:#101722;color:#e4ebf5;font:16px system-ui}main{max-width:1200px;margin:auto;padding:30px}h1{margin-bottom:8px}.muted{color:#a2b0c2}.cards{display:flex;flex-wrap:wrap;gap:15px;margin:25px 0}.card{background:#1b2636;border:1px solid #344052;padding:18px;min-width:175px;border-radius:8px}.card strong{display:block;font-size:26px;margin-top:7px}.chart{height:330px;margin-bottom:20px}#net{height:440px}a{color:#52d4c8}footer{padding:15px 0;font-size:14px;line-height:1.7}</style><main>
<p class="muted">OFFLINE RESEARCH / LEGACY CONTROL B00</p><h1>BTCUSDT · Annual portfolio baseline</h1><p class="muted">01 Oct 2025 — 30 Sep 2026 · All timestamps UTC · Initial capital 10,000 USDT</p>
<div class="cards">CARDS</div><p>Drag to zoom; double-click to reset. Click a legend item to hide or show a series.</p>
<div id="net" class="chart"></div><div id="dd" class="chart"></div><div id="bridge" class="chart"></div><div id="positions" class="chart"></div>
<footer>Diamond = synthetic terminal closeout. Curve keeps six-hour bucket extrema; exact metrics use all 525,600 minute observations.<br>Realized gains include partially closed trades; remaining inventory must also be valued. Fees and funding are already included in Net.<br>Research data, synthetic spread/slippage and full fills; no depth, latency or intrabar liquidation proof. This is not trading admission.<br><a href="baseline.png">Open static PNG</a></footer></main><script>
const data=PAYLOAD;const common={paper_bgcolor:'#101722',plot_bgcolor:'#101722',font:{color:'#dce7f4'},margin:{l:65,r:35,t:65,b:75},hovermode:'x unified',legend:{orientation:'h',y:-0.2,x:0},xaxis:{gridcolor:'#293344'},yaxis:{gridcolor:'#293344'}};
for(const [id,title,unit] of [['net','Portfolio Net · MTM curve and terminal closeout','USDT'],['dd','Drawdown from Equity peak','%'],['bridge','PnL bridge · all costs counted once','USDT'],['positions','Recorded LONG / SHORT quantities','BTC']]){const layout=structuredClone(common);layout.title={text:title,x:0.05};layout.yaxis.title={text:unit};Plotly.newPlot(id,data[id],layout,{responsive:true,displaylogo:false,scrollZoom:true});}
</script></html>'''
    cards=[('Net after closeout',f'{net:,.2f} USDT'),('Hold closeout',f'{D(terminal["hold"]["net_flat"]):,.2f} USDT'),
           ('Max drawdown',f'{D(result["max_close_drawdown_pct"]):.3f}%'),('Rebalances',str(result['rebalances']))]
    html=html.replace('CARDS',''.join(f'<div class="card">{k}<strong>{v}</strong></div>' for k,v in cards))
    html=html.replace('PAYLOAD',json.dumps(payload,separators=(',',':')).replace('</','<\\/'))
    (output/'index.html').write_text(html,encoding='utf-8')
    audit=dict(source_study='B00',source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in study.glob('*.json')},
        minute_rows=len(times),rendered_curve_points=len(idx),net_closeout=str(net),bridge_residual=str(sum(parts)-net),
        max_drawdown_full_curve_pct=str(float(-min(dd['candidate']))),positions_end_flat=True,
        plotly_url=js_url,plotly_sha256=hashlib.sha256(bundle).hexdigest(),backtest_rerun=False)
    (output/'visualization_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in audit.items() if k!='source_sha256'},indent=2))


if __name__=='__main__': main()
