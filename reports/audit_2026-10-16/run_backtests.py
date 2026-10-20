"""Fixed-config audit replay. No optimization and no external calls during replay."""
import asyncio
import hashlib
import json
import math
import socket
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent.resolve()
ROOT = OUT / "_sandbox" / "futures_portfolio"
sys.path.insert(0, str(ROOT))
from backtest import backtest_rebalance as engine

original_connect = socket.socket.connect
def no_network(self, address):
    if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost"):
        return original_connect(self, address)
    raise OSError("Audit replay is offline")
socket.socket.connect = no_network

states, simulators = [], []
OriginalState = engine.BacktestState
OriginalSim = engine.MarketOrderSlippageSimulator


class ObservedState(OriginalState):
    def __post_init__(self):
        states.append(self)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        states.append(self)


class ObservedSim(OriginalSim):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fees = Decimal(0)
        self.slippage_cost = Decimal(0)
        simulators.append(self)

    def simulate_market_execution(self, side, qty, mid_price):
        price, fee = super().simulate_market_execution(side, qty, mid_price)
        self.fees += fee
        self.slippage_cost += abs(Decimal(str(qty))) * abs(price - Decimal(str(mid_price)))
        return price, fee


engine.BacktestState, engine.MarketOrderSlippageSimulator = ObservedState, ObservedSim


async def main():
    data = OUT / "market_data"
    data.mkdir(exist_ok=True)
    inputs = []
    for symbol in ("BTCUSDT", "ETHUSDT", "ZECUSDT"):
        csv = data / (symbol + ".csv")
        if not csv.exists():
            continue
        frame = pd.read_csv(csv, header=None)
        times = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
        frame = frame.loc[times.notna()].copy()
        times = pd.to_numeric(frame.iloc[:, 0]).astype("int64")
        assert len(frame) == 43200 and times.is_unique and (times.diff().dropna() == 60000).all()
        assert pd.to_datetime(times.iloc[0], unit="ms", utc=True).isoformat() == "2026-09-01T00:00:00+00:00"
        assert pd.to_datetime(times.iloc[-1], unit="ms", utc=True).isoformat() == "2026-09-30T23:59:00+00:00"
        ohlcv = frame.iloc[:, 1:6].astype(float)
        ohlcv.columns = ["open", "high", "low", "close", "volume"]
        assert (ohlcv[["open", "high", "low", "close"]] > 0).all().all()
        ohlcv.reset_index(drop=True).to_feather(data / (symbol + "_live.feather"))
        inputs.append(dict(symbol=symbol, type="historical", bars=len(frame), days=30,
                           first_utc="2026-09-01T00:00:00Z", last_utc="2026-09-30T23:59:00Z"))
    i = np.arange(1440)
    paths = {"AUDITFLATUSDT": np.full(1440, 100.0),
             "AUDITWAVEUSDT": 100 + 2 * np.sin(2 * math.pi * i / 120),
             "AUDITTRENDUSDT": 100 * np.exp(math.log(2) * i / 1439)}
    for symbol, prices in paths.items():
        pd.DataFrame(dict(open=prices, high=prices, low=prices, close=prices, volume=np.ones(1440))).to_feather(data / (symbol + "_live.feather"))
        inputs.append(dict(symbol=symbol, type="synthetic", bars=1440, days=1))
    results = []
    for item in inputs:
        for slip in ((0.0005, 0.001) if item["type"] == "historical" else (0.0005,)):
            result = await engine.run_backtest(str(ROOT / "config.json"), str(data), live_mode=False,
                    ticker_override=item["symbol"], days=item["days"], commission=0.0004, slippage=slip, quiet=True)
            state, sim = states[-1], simulators[-1]
            close = Decimal(str(pd.read_feather(data / (item["symbol"] + "_live.feather"))["close"].iloc[-1]))
            # Independently mark positions using the configured 7x rather than state's default 5x.
            cfg = json.loads((ROOT / "config.json").read_text())["portfolios"][0]
            lev_l = Decimal(str(cfg["targets"]["BASE_LONG"]["leverage"]))
            lev_s = Decimal(str(cfg["targets"]["BASE_SHORT"]["leverage"]))
            marked = (state.val_cash + state.pos_long * state.long_entry_price / lev_l +
                      state.pos_short * state.short_entry_price / lev_s +
                      state.pos_long * (close - state.long_entry_price) +
                      state.pos_short * (state.short_entry_price - close) +
                      state.virt_qty * close - state.virt_debt + state.siphoning_reserve)
            record = dict(**item, commission=0.0004, slippage=slip, result=result,
                          actual_fee_sum=str(sim.fees), slippage_cost=str(sim.slippage_cost),
                          state_leverage=[str(state.l_lev), str(state.s_lev)], configured_leverage=[str(lev_l), str(lev_s)],
                          independently_marked_end_equity=str(marked), reported_end_equity=state.history[-1],
                          marked_net=str(marked - Decimal(str(cfg["initial_capital"]))),
                          feather_sha256=hashlib.sha256((data / (item["symbol"] + "_live.feather")).read_bytes()).hexdigest())
            results.append(record)
            print(json.dumps(record), flush=True)
    (OUT / "backtest_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


asyncio.run(main())
