"""Plot ZECUSDT 12-month backtest: equity curve, drawdown, price."""
import json, os
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
res = json.load(open(os.path.join(ROOT, "reports", "zec_12m_backtest_result.json"), encoding="utf-8"))
curve = json.load(open(os.path.join(ROOT, "reports", "zec_12m_equity.json"), encoding="utf-8"))
prices = pd.read_feather(os.path.join(ROOT, "data", "ZECUSDT_12m.feather"))["close"].astype(float).values

n = min(len(curve), len(prices))
curve = curve[-n:]
prices = prices[-n:]

# Use bar index (not actual dates from feather)
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.05)

eq = pd.Series(curve)
running_max = eq.cummax().clip(lower=res.get("initial_capital", eq.iloc[0]))
dd = (running_max - eq) / running_max * 100

fig.add_trace(go.Scatter(y=eq, name="TPV (equity)", line=dict(color="#4dabf7", width=1.2)), row=1, col=1)
fig.add_trace(go.Scatter(y=prices, name="ZECUSDT price", line=dict(color="#ffd43b", width=1), yaxis="y2"), row=1, col=1)
fig.add_trace(go.Scatter(y=dd, name="Drawdown %", fill="tozeroy", line=dict(color="#fa5252", width=0.8)), row=2, col=1)
fig.add_trace(go.Scatter(y=(prices - prices[0]) / prices[0] * 100, name="ZEC chg %", line=dict(color="#868e96", width=1)), row=3, col=1)

fig.update_layout(template="plotly_dark", height=900,
                  title=dict(
                      text=f"ZECUSDT 12M Backtest | Profit: {res['profit_pct']:.2e}% | MaxDD: {res['max_dd_pct']:.2f}% | Sortino: {res['sortino_ratio']:.4f} | Cycles: {res['cycles']}",
                      font=dict(size=14)),
                  legend=dict(orientation="h", y=1.06))
fig.update_yaxes(title_text="USDT", row=1, col=1)
fig.update_yaxes(title_text="DD %", row=2, col=1)
fig.update_yaxes(title_text="ZEC %", row=3, col=1)
fig.update_xaxes(title_text="Bar (1 minute)", row=3, col=1)
html_path = os.path.join(ROOT, "reports", "zec_12m_backtest.html")
fig.write_html(html_path)
print(f"saved {html_path}")
