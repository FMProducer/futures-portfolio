# CLAUDE.md — Binance Futures Portfolio Rebalancer

## Project Overview

**Type**: Market Neutral Futures Trading System
**Exchange**: Binance Futures
**Core**: Dynamic synthetic hedging with Long/Short hedge + Margin Simulation

### Target Allocation (Market Neutral 2.0)
1. **Long x5 (29%)**: Long futures position
2. **Short x5 (36%)**: Short futures position
3. **Margin Simulation (35%)**: Virtual base asset position on free margin

---

## Architecture

```
futures_portfolio/
├── main.py              # Entry point, 24h auto-pilot loop
├── connector.py         # Binance Futures API wrapper
├── calculator.py        # Position sizing & rebalancing math
├── executor.py          # Order execution
├── rank_tickers.py      # Scanner: selects best ticker (Selection Strategy 3.0)
├── backtest_rebalance.py       # Backtesting engine (with trailing stop & hysteresis)
├── config.json          # Runtime configuration
├── state.json           # Live session state
├── paper_state.json     # Paper trading state
├── .env.example         # Environment variables template
├── .gitignore           # Git ignore rules
└── tests/              # Unit tests
```

---

## Key Metrics

| Metric | Description |
|--------|-------------|
| **Real Equity** | `Available_Balance + Sum(Unrealized_PnL)` |
| **TPV** | Total Portfolio Value (includes virtual leg) |
| **TPV_Active** | `TPV - siphoning_reserve` (working capital) |
| **tpv_ath** | High-Water Mark for Trailing Stop |
| **Margin Ratio** | `totalMarginBalance / totalMaintMargin` (safety indicator) |
| **reference_tpv** | Fixed baseline for hysteresis logic (doesn't change on siphon) |

---

## Security & Environment

### API Keys (CRITICAL)
**NEVER store API keys in config.json!** Use environment variables:

```bash
# Windows PowerShell
$env:BINANCE_API_KEY="your_key"
$env:BINANCE_SECRET_KEY="your_secret"

# Windows CMD
set BINANCE_API_KEY=your_key
set BINANCE_SECRET_KEY=your_secret

# Linux/Mac
export BINANCE_API_KEY=your_key
export BINANCE_SECRET_KEY=your_secret
```

Or create `.env` file from template:
```bash
copy .env.example .env
# Edit .env and add your keys
```

### Files Added (18 Apr 2026)
- **.env.example** — Template for environment variables
- **.gitignore** — Excludes .env, state files, logs from git

---

## Safety Mechanisms

### 1. Equity Trailing Stop
- Drawdown from ATH → automatic position closure
- Triggers: `equity_trailing_stop_pct: 24.0` in config (Optuna-optimized)
- Activation: `equity_trailing_stop_activation_pct: 7.5` (must be in profit first)
- Timeout: `equity_trailing_stop_timeout_sec: 60`

### 2. Margin Ratio Monitoring
Real-time tracking of liquidation risk via `get_margin_ratio()` in connector.py:

| Level | Threshold | Action |
|-------|-----------|--------|
| **Warning** | ≤5x | Log warning about low margin |
| **Critical** | ≤2x | Emergency close all positions and stop bot |

Config parameters:
```json
"margin_ratio_warning": 5.0,
"margin_ratio_critical": 2.0
```

### 3. Position-Level Liquidation Guard (NEW — 30 May 2026)
Monitors distance to per-position liquidation price (works with ISOLATED margin).
Uses `get_position_risk()` in connector.py.

| Level | Threshold | Action |
|-------|-----------|--------|
| **Warning** | ≤15% distance | Log warning |
| **Critical** | ≤8% distance | Emergency close that position immediately |

Config parameters:
```json
"liquidation_distance_warn_pct": 15.0,
"liquidation_distance_crit_pct": 8.0
```

Key: closes **one position** before exchange liquidates it. Does NOT stop the bot.
Prevents the "PORTALUSDT SHORT liquidation" cascade of 30.05.2026.

### 4. SPIKE/NET Traps
Filters during ticker selection (rank_tickers.py):
- **SPIKE TRAP**: >10% in 1 hour → disqualify
- **NET TRAP**: >15% over 48h → disqualify

### 5. Supervisor Auto-Restart Guard (NEW — 30 May 2026)
`_ensure_real_bots_alive()` in supervisor.py:
- After each swarm cycle, verifies all `live_swarm` tickers have PM2 processes
- Missing real bot → auto-relaunch
- Prevents the "supervisor restart killed real-portal" bug of 30.05.2026

---

## Rebalancing Logic

### Soft Hysteresis
- Double rebalance threshold on drawdown (reduces fees by 40%)
- **FIXED (18 Apr 2026)**: Uses `reference_tpv` (fixed) instead of `initial_tpv` (changes on siphon)
- Location: main.py:271-274, backtest_rebalance.py:205-208

### ATR Removal
- Dynamic ATR threshold removed — caused instability, conflicted with hysteresis

---

## Selection Strategy 3.0 (rank_tickers.py)

Filters applied to 48h data:
1. **Realistic Cycles**: Price moves >1.5% (rebalance trigger at x5 leverage), target >15 cycles/48h
2. **SPIKE TRAP**: Disqualify if >10.0% move in 1 hour (flash-crash protection)
3. **NET TRAP**: Disqualify if total price drift >15.0% over 48h (hedge breakdown protection)
4. **Trend Efficiency**: Ideal <3.0%, penalize if >7%

---

## Operational Workflow (Auto-Pilot 24h)

1. **Scanner**: `python rank_tickers.py` → pick ticker with max SCORE from Tier-1/Tier-2
2. **Live Verification**: `python backtest_rebalance.py --live --ticker ZECUSDT`
   - Criteria: NET PROFIT >+0.5% over 48h AND Profit Factor >1.2
3. **Launch**: Run `main.py` for 24h
4. **Rotation**: Next cycle `initial_tpv` = previous `tpv_ath`

---

## Commands

```bash
# Scanner
python rank_tickers.py

# Backtest
python backtest_rebalance.py --ticker ZECUSDT
python backtest_rebalance.py --live --ticker ZECUSDT

# Live trading
python main.py

# With explicit config
python main.py --config config.json
```

---

## Verified Results (April 2026, 10% Trailing Stop + Scanner 3.0)

| Ticker | Period | NET Profit | Max Drawdown | Result |
|--------|--------|------------|--------------|--------|
| ZECUSDT | 48h Live | +1.08% | 2.04% | GOLD STANDARD |
| CLUSDT | 48h Live | +1.98% | 2.17% | Profit |
| BZUSDT | 48h Live | +1.64% | 1.40% | Profit |
| ENAUSDT | 70 days | +18.55% | 7.41% | Max Alpha |
| MOVRUSDT | 48h Dump | -8.79% | 9.38% | Stop triggered (protection) |

---

## Change Log

### 14 July 2026 — Environment Stabilization & Dependency Pinning
1. **Dependencies Pinned**: `requirements.txt` and `pyproject.toml` — all 14 packages with exact `==` versions
2. **pyarrow**: Installed `pyarrow==23.0.1` (<24 — no feather deprecation warnings)
3. **Test Fix**: `test_send_monitor_report.py` — added `patch("dotenv.load_dotenv")` to prevent module-level TypeError
4. **ecosystem.config.js**: Fixed venv path from `../.venv` to `./.venv`

### 14 July 2026 — Clock Sync & recvWindow Hardening
1. **Clock Drift**: Local clock drift ~-7s (stable), but could spike beyond `REQUEST_RECVWINDOW=15000`
2. **NTP Sync**: `timestamp_offset = server_time - local_time` eliminates drift entirely
3. **recvWindow**: Increased 15000 → 30000 as safety margin
4. **Spot Ping Eliminated**: `Client(ping=False)` + `futures_ping()` in verify_connection — avoids 15s timeout on blocked `api1.binance.com`
5. **Tests**: 5 assertions updated in `test_connector.py`

### 16 July 2026 — Revert timestamp_offset (CRITICAL FIX)
1. **Root Cause**: `timestamp_offset` вычислялся через сырой `requests.get()` с системным прокси в РФ — давал невалидный offset, ломая реальных ботов (`-1021 Timestamp ahead`)
2. **Fix**: Удалён блок `timestamp_offset` из `connector.py`. Оставлен `recvWindow=30000` (работает months без проблем)
3. **Retry**: `-1021` добавлен в retryable ошибки (`retry_on_network_error`) — транзиентная ошибка, не фатальная
4. **Tests**: Новый тест `test_retry_on_network_error_timestamp_retryable`, 328/328 pass

### 16 July 2026 — Skills Stack: 6 Custom Skills
1. **binance-api-ref** — Binance Futures API: эндпоинты, ошибки, rate limits, clock sync
2. **pm2-workflow** — PM2: restart, logs, ecosystem, safety, диагностика
3. **sqlite-telemetry** — SQL-запросы к trading_stats.db: PnL, win rate, drawdown
4. **futures-trading-debugging** — отладка live-проблем: смерть бота, дрифт позиций
5. **optuna-optimization** — Optuna: objective function, Sortino, multi-ticker
6. **connector-patterns** — python-binance: async wrappers, retry, hedge mode, gotchas
- Skills.txt: 90 → 96 скиллов

### 16 July 2026 — SQLite Telemetry: External Analytics Pipeline
1. **Расследование**: Skill sqlite-telemetry ссылался на мёртвую систему (parse_logs_to_sqlite.py, cron на паузе)
2. **Источник**: 5509 trades были от parse_logs_to_sqlite.py (парсинг PM2 логов), stale 42 дня
3. **Решение**: archive_to_sqlite.py — внешний скрипт, читает history/*.json, заполняет trading_stats.db
4. **Данные**: 29 real сессий (+139.75 USDT, PF 79.95, WR 89.7%), 167 paper сессий
5. **Анализ**: EPICUSDT (+85.13) основной драйвер, DODOXUSDT стабильный (7/7 wins)
6. **Выводы**: Trailing stop теряет ~80% прибыли, paper предсказывает направление (10/15)
7. **Перенос**: scripts/archive_to_sqlite.py, trading_stats.db в корень проекта

### 16 July 2026 — PM2 Log Parser: Intermediate State Telemetry
1. **Расследование**: PM2 логи (6726 файлов) содержат heartbeats, trades, rebalances — но не парсились
2. **Решение**: pm2_log_parser.py — внешний скрипт, парсит PM2 логи, заполняет 4 таблицы в trading_stats.db
3. **Баг fix**: TRADE_PATTERN группы 5-7 (ticker, pos_type, price) использовались неверно → 86K trades молча пропускались
4. **Данные**: heartbeats (1.27M), rebalances (55K), pm2_trades (84K), guards (100K)
5. **Cron**: b0ff3538f9df, ежедневно 23:00 — archive_to_sqlite.py + pm2_log_parser.py
6. **Структура БД**: bot_sessions (196), daily_stats (79), trades (5509, stale), heartbeats (1.27M), rebalances (55K), pm2_trades (84K), guards (100K)

### 14 July 2026 — Binance API recvWindow Fix (initial)
1. **Clock Drift**: Local clock drift -7.6s exceeded default `recvWindow=10000ms`, causing `-1021` errors
2. **Fix**: `connector.py` — `client.REQUEST_RECVWINDOW = 15000` (compensates drift up to 15s)
3. **Note**: `recvWindow` is a Binance API parameter, NOT a `requests.Session` kwarg

### 18 April 2026 — Security & Safety Update
1. **API Security**: Keys moved to env vars (BINANCE_API_KEY, BINANCE_SECRET_KEY)
2. **Margin Safety**: Added margin ratio monitoring with emergency shutdown
3. **Hysteresis Fix**: `reference_tpv` now fixed baseline, doesn't change on siphon
4. **Project Hygiene**: Added .env.example and .gitignore

