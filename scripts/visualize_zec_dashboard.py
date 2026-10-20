"""ZECUSDT backtest dashboard — uses visualize_365d_dashboard.py template.
Usage: python scripts/visualize_zec_dashboard.py [prefix]
   default prefix = zec_12m -> reports/zec_12m_backtest_result.json etc."""
import json, os, html, sys

PREFIX = sys.argv[1] if len(sys.argv) > 1 else "zec_12m"
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
DATA = os.path.join(ROOT, "data")
TICKER = "ZECUSDT"

result = json.load(open(os.path.join(REPORTS, f"{PREFIX}_backtest_result.json"), encoding="utf-8"))
equity = np.array(json.load(open(os.path.join(REPORTS, f"{PREFIX}_equity.json"), encoding="utf-8")))

dw = result.get("data_window", {})
feather = os.path.join(DATA, dw.get("feather", f"{TICKER}_12m.feather"))
# Fallback: result doesn't always have data_window when run manually
if not os.path.exists(feather):
    for cand in (f"{TICKER}_12m.feather", f"{TICKER}_live.feather"):
        p = os.path.join(DATA, cand)
        if os.path.exists(p):
            feather = p
            break
offset = int(dw.get("offset", 0))
full_closes = np.array(pd.read_feather(feather)["close"], dtype=np.float64)
n = min(len(equity), len(full_closes) - offset)
closes, equity = full_closes[offset:offset + n], equity[:n]

end_ts = pd.Timestamp.fromtimestamp(os.path.getmtime(feather), tz="UTC").floor("min")
dates = pd.date_range(end=end_ts, periods=n, freq="min")

fills = result.get("fills", [])
events = result.get("events", [])
initial = float(equity[0])
final_tpv = float(equity[-1])

DS = 10
dates_ds = [str(d) for d in dates[::DS]]
closes_ds = [round(float(v), 2) for v in closes[::DS]]
equity_ds = [round(float(v), 2) for v in equity[::DS]]

bar_realized = np.zeros(n)
bar_commission = np.zeros(n)
for f in fills:
    b = min(f["bar"], n - 1)
    bar_realized[b] += f["realized_pnl"]
    bar_commission[b] += f["commission"]
cum_realized = np.cumsum(bar_realized)
cum_commission = np.cumsum(bar_commission)
total_realized = float(cum_realized[-1])
total_commission = float(cum_commission[-1])
siphoning = result.get("siphoning_reserve", 0.0)
net_pnl = final_tpv - initial
funding_residual = initial + total_realized - total_commission - siphoning - final_tpv

runmax = np.maximum.accumulate(equity)
dd_pct = (runmax - equity) / (runmax + 1e-9) * 100.0

def fmt_money(v):
    return f"{v:,.0f}" if abs(v) >= 1e6 else f"{v:,.2f}"

profit_str = result.get('profit_pct', 0)
if abs(profit_str) > 1e15:
    profit_display = f"{profit_str:.2e}%"
else:
    profit_display = f"{profit_str:+.3f}%"

tiles = [
    ("Net PnL", f"{net_pnl:+,.0f} USDT", "pos" if net_pnl >= 0 else "neg"),
    ("Net PnL %", profit_display, "pos" if result.get('profit_pct', 0) >= 0 else "neg"),
    ("Max DD %", f"{result.get('max_dd_pct', 0):.2f}%", "neg"),
    ("Sortino", f"{result.get('sortino_ratio', 0):.4f}", ""),
    ("Rebalance cycles", f"{result.get('cycles', 0):,}", ""),
    ("Fills", f"{len(fills):,}", ""),
    ("Commissions", f"{total_commission:,.0f}", "neg"),
    ("Siphoned reserve", f"{siphoning:,.0f}", ""),
    ("Liquidations", f"{result.get('liquidations', 0)}", "neg" if result.get('liquidations') else ""),
    ("Trailing stops", f"{result.get('trailing_stops', 0)}", "warn" if result.get('trailing_stops') else ""),
    ("ZEC asset change", f"{result.get('asset_chg_pct', 0):+.2f}%", "pos" if result.get('asset_chg_pct', 0) >= 0 else "neg"),
    ("Trend-guard blocks", f"{result.get('trend_guard_blocks', 0):,}", "warn" if result.get('trend_guard_blocks') else ""),
]
tile_html = "".join(
    f'<div class="tile"><div class="tl">{html.escape(k)}</div><div class="tv {cls}">{html.escape(v)}</div></div>'
    for k, v, cls in tiles
)

attr_rows = [
    ("Initial capital", initial),
    ("Realized PnL (fills)", total_realized),
    ("Commissions", -total_commission),
    ("Funding/other residual", funding_residual),
    ("Siphoning reserve", -siphoning),
]
attr_html = "".join(f"<tr><td>{html.escape(name)}</td><td class='num'>{v:+,.2f}</td></tr>" for name, v in attr_rows) + \
    f"<tr class='tot'><td>Final TPV</td><td class='num'>{final_tpv:,.2f}</td></tr>"

ev_bars = [e["bar"] for e in events if e["bar"] < n]
ev_kind = [e.get("kind") for e in events if e["bar"] < n]
tr_bars = [b for b, k in zip(ev_bars, ev_kind) if k == "trailing_stop"]
liq_bars = [b for b, k in zip(ev_bars, ev_kind) if k == "liquidation"]

# --- fills table
MAX_ROWS = 5000
shown = fills[:MAX_ROWS]
trows = []
for idx, f in enumerate(shown):
    ts = str(dates[f["bar"]]) if f["bar"] < n else "?"
    trows.append(
        f"<tr><td>{html.escape(ts)}</td><td>{html.escape(f['side'])}</td><td>{html.escape(f['leg'])}</td>"
        f"<td class='num'>{f['qty']:.4f}</td><td class='num'>{f['price']:,.2f}</td>"
        f"<td class='num'>{f['commission']:,.6f}</td><td class='num'>{f['realized_pnl']:,.6f}</td>"
        f"<td class='num'>{f.get('tpv', f.get('tpv_after', 0)):,.2f}</td></tr>"
    )
table_body = "".join(trows)
row_note = f"showing {len(shown):,} of {len(fills):,} fills" if len(fills) > MAX_ROWS else f"{len(fills):,} fills"

# --- data quality flags
qflags = []
if result.get("liquidations"):
    qflags.append(f"{result['liquidations']} liquidation events")
if result.get("skipped_expansions"):
    qflags.append(f"{result['skipped_expansions']} skipped expansions")
quality_html = "".join(f"<li>{html.escape(x)}</li>" for x in qflags) or "<li>no flags</li>"

# --- figure 1: price + event markers
fig1 = go.Figure()
fig1.add_trace(go.Scatter(x=dates_ds, y=closes_ds, name="ZECUSDT close", line=dict(color="#ffd43b", width=1.2)))
if tr_bars:
    ts_slice = tr_bars[:2000]
    fig1.add_trace(go.Scatter(x=[str(dates[b]) for b in ts_slice], y=[float(v) for v in closes[ts_slice]],
                  name="trailing stop", mode="markers", marker=dict(size=6, color="#fa5252", symbol="triangle-down")))
if liq_bars:
    lq_slice = liq_bars[:2000]
    fig1.add_trace(go.Scatter(x=[str(dates[b]) for b in lq_slice], y=[float(v) for v in closes[lq_slice]],
                  name="liquidation", mode="markers", marker=dict(size=8, color="#ff6b6b", symbol="x")))
fig1.update_layout(template="plotly_dark", height=380, title="Asset Price + Event Markers",
                   margin=dict(l=50, r=20, t=40, b=30), yaxis=dict(autorange=True, side="right"))

# --- figure 2: equity + drawdown
fig2 = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.05)
fig2.add_trace(go.Scatter(x=dates_ds, y=equity_ds, name="TPV", line=dict(color="#4dabf7", width=1.4)), row=1, col=1)
dd_ds = (dd_pct[::DS]).tolist()
fig2.add_trace(go.Scatter(x=dates_ds, y=[round(v, 3) for v in dd_ds], name="Drawdown %",
                          fill="tozeroy", line=dict(color="#fa5252", width=0.8)), row=2, col=1)
fig2.update_layout(template="plotly_dark", height=480, title="Equity (TPV) and Drawdown",
                   margin=dict(l=50, r=20, t=40, b=30))

# --- figure 3: PnL attribution
fig3 = go.Figure()
fig3.add_trace(go.Scatter(x=dates_ds, y=[round(v, 2) for v in (cum_realized[::DS])], name="Cumulative realized PnL",
                          line=dict(color="#51cf66", width=1.4)))
fig3.add_trace(go.Scatter(x=dates_ds, y=[round(-v, 2) for v in (cum_commission[::DS])], name="Cumulative commissions",
                          line=dict(color="#fa5252", width=1.2)))
fig3.add_trace(go.Scatter(x=dates_ds, y=[round(v, 2) for v in ((equity[::DS] - initial))], name="Net equity PnL",
                          line=dict(color="#4dabf7", width=1.4)))
fig3.update_layout(template="plotly_dark", height=420, title="PnL Attribution (cumulative)",
                   margin=dict(l=50, r=20, t=40, b=30))

# window label
if dw.get("since_utc") or dw.get("until_utc"):
    _s = dw.get("since_utc") or dates[0].strftime("%Y-%m-%d %H:%M")
    _u = dw.get("until_utc") or end_ts.strftime("%Y-%m-%d %H:%M")
    window_label = f"{_s} → {_u} UTC (offset {offset:,})"
else:
    window_label = f"last {n:,} bars"

page = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZECUSDT Backtest Dashboard — {html.escape(window_label)}</title>
<style>
 body {{ font-family: 'Segoe UI', system-ui, sans-serif; background:#0d1117; color:#e6edf3; margin:0; padding:24px; }}
 h1 {{ font-size:20px; font-weight:600; }} h2 {{ font-size:15px; font-weight:600; margin:24px 0 8px; color:#8b949e; text-transform:uppercase; letter-spacing:.05em; }}
 .tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:10px; }}
 .tile {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px; }}
 .tl {{ font-size:11px; color:#8b949e; margin-bottom:6px; }}
 .tv {{ font-size:18px; font-weight:600; }} .tv.pos {{ color:#51cf66; }} .tv.neg {{ color:#ff6b6b; }} .tv.warn {{ color:#ffd43b; }}
 table {{ border-collapse:collapse; width:100%; font-size:12px; }}
 th, td {{ padding:5px 9px; text-align:left; border-bottom:1px solid #21262d; }}
 th {{ color:#8b949e; position:sticky; top:0; background:#161b22; cursor:pointer; user-select:none; }}
 td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
 .attr td {{ padding:7px 10px; }} .tot td {{ font-weight:600; border-top:2px solid #30363d; }}
 .scroll {{ max-height:420px; overflow:auto; border:1px solid #30363d; border-radius:8px; background:#161b22; }}
 .count {{ color:#8b949e; font-size:12px; margin:6px 0; }}
 .card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px; margin-bottom:14px; }}
</style>
<script src="plotly-2.27.0.min.js" charset="utf-8"></script>
<script>if(!window.Plotly){{document.write('<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"><\\/script>')}}</script>
</head><body>
<h1>ZECUSDT Backtest &mdash; Cross-Margin Rebalance Engine</h1>
<p class="count">window: {html.escape(window_label)} | {n:,} 1m bars ending {html.escape(str(end_ts))} UTC | initial capital {initial:,.0f} USDT | comm 0.04% + slip 0.05%</p>
<h2>Overview</h2>
<div class="tiles">{tile_html}</div>
<h2>Price &amp; Events</h2><div id="c1"></div>
<h2>Equity &amp; Drawdown</h2><div id="c2"></div>
<h2>PnL Attribution</h2>
<div class="card"><table class="attr">{attr_html}</table></div>
<div id="c3"></div>
<h2>Rebalancing &amp; Fills</h2>
<p class="count">{row_note}</p>
<div class="scroll"><table id="fills-table">
<thead><tr><th data-k="0">Timestamp</th><th data-k="1">Side</th><th data-k="2">Leg</th>
<th data-k="3" class="num">Qty</th><th data-k="4" class="num">Price</th><th data-k="5" class="num">Commission</th>
<th data-k="6" class="num">Realized PnL</th><th data-k="7" class="num">TPV after</th></tr></thead>
<tbody>{table_body}</tbody></table></div>
<h2>Data Quality</h2><div class="card"><ul>{quality_html}</ul></div>
<script>
Plotly.newPlot('c1', {json.dumps(fig1.to_plotly_json())}, {{displayModeBar:false}});
Plotly.newPlot('c2', {json.dumps(fig2.to_plotly_json())}, {{displayModeBar:false}});
Plotly.newPlot('c3', {json.dumps(fig3.to_plotly_json())}, {{displayModeBar:false}});
var rows = Array.from(document.querySelectorAll('#fills-table tbody tr'));
document.querySelectorAll('#fills-table th').forEach(function(th) {{
  th.addEventListener('click', function() {{
    var k = +th.dataset.k, asc = th.dataset.asc !== '1'; th.dataset.asc = asc ? '1' : '0';
    rows.sort(function(a, b) {{
      var x = a.cells[k].textContent, y = b.cells[k].textContent;
      var nx = parseFloat(x.replace(/[+,]/g, '')), ny = parseFloat(y.replace(/[+,]/g, ''));
      var c = isNaN(nx) || isNaN(ny) ? x.localeCompare(y) : (nx - ny);
      return asc ? c : -c;
    }});
    var tb = document.querySelector('#fills-table tbody');
    rows.forEach(function(r) {{ tb.appendChild(r); }});
  }});
}});
</script>
</body></html>"""

out = os.path.join(REPORTS, f"{PREFIX}_dashboard.html")
with open(out, "w", encoding="utf-8") as fh:
    fh.write(page)
print(f"saved {out} ({os.path.getsize(out) // 1024} KB)")
print(f"fills: {len(shown):,} / {len(fills):,} | events: {len(events)} | net PnL: {net_pnl:+,.0f} USDT")