import json, os, sys
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
DATA = os.path.join(ROOT, "data")

result = json.load(open(os.path.join(REPORTS, "btc_365d_backtest_result.json"), encoding="utf-8"))
equity = json.load(open(os.path.join(REPORTS, "btc_365d_equity.json"), encoding="utf-8"))

df = pd.read_feather(os.path.join(DATA, "BTCUSDT_live_365d.feather"))
n = min(len(equity), len(df))
prices = df["close"].astype(float).values[:n]

# equity curve is per-bar for the last max_bars window
window = df.tail(n).reset_index(drop=True)
dates = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("min"), periods=n, freq="min")

fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                    row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.05)

eq = pd.Series(equity[-n:])
running_max = eq.cummax().clip(lower=result.get("initial_capital", eq.iloc[0]))
dd = ((running_max - eq) / running_max * 100)

fig.add_trace(go.Scatter(x=dates, y=eq, name="TPV (equity)", line=dict(color="#4dabf7", width=1.2)), row=1, col=1)
fig.add_trace(go.Scatter(x=dates, y=prices, name="BTCUSDT price", line=dict(color="#ffd43b", width=1)), row=1, col=1)
fig.add_trace(go.Scatter(x=dates, y=dd, name="Drawdown %", fill="tozeroy", line=dict(color="#fa5252", width=0.8)), row=2, col=1)
fig.add_trace(go.Scatter(x=dates, y=(prices - prices[0]) / prices[0] * 100, name="BTC %", line=dict(color="#868e96", width=1)), row=3, col=1)

fig.update_layout(template="plotly_dark", height=900,
                  title=dict(text=f"BTCUSDT 365d Backtest | Net: {result['profit_pct']:+.2f}% | MaxDD: {result['max_dd_pct']:.2f}% | Sortino: {result['sortino_ratio']:.4f}", font=dict(size=16)),
                  legend=dict(orientation="h", y=1.06))
fig.update_xaxes(rangeslider_visible=False)
fig.update_yaxes(title_text="TPV / BTC price", row=1, col=1)
fig.update_yaxes(title_text="DD %", row=2, col=1)
fig.update_yaxes(title_text="BTC % change", row=3, col=1)

out = os.path.join(REPORTS, "btc_365d_backtest.html")
fig.write_html(out, include_plotlyjs=True)
print(f"saved {out}")
