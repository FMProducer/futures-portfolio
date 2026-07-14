# Market-Neutral Futures Portfolio Rebalancer

Автоматизированная система управления портфелем USD-M фьючерсов на Binance. Извлекает прибыль из волатильности, поддерживая рыночно-нейтральную дельту (≈0) через три ноги портфеля.

## Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd futures_portfolio

# 2. Установить зависимости
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e ".[dev]"

# 3. Настроить окружение
cp .env.example .env
# Отредактировать .env — добавить API ключи

# 4. Запустить тесты
pytest

# 5. Запустить бота
python core/main.py --config config.json --ticker BTCUSDT
```

## Архитектура

```
futures_portfolio/
├── core/                    # Основной код бота
│   ├── main.py             # Точка входа, 24h auto-pilot loop
│   ├── connector.py        # Binance Futures API wrapper
│   ├── calculator.py       # Position sizing & rebalancing math
│   ├── executor.py         # Order execution
│   ├── storage.py          # State persistence
│   └── notifier.py         # Telegram notifications
├── supervisor/              # Управление роем ботов
│   ├── swarm_manager.py    # PM2 process management
│   ├── aggregator.py       # Aggregated monitoring
│   └── supervisor_service.py
├── scanner/                 # Ticker selection
│   └── rank_tickers.py     # Selection Strategy 3.0
├── backtest/                # Backtesting engine
│   └── backtest_rebalance.py
├── optimization/            # Optuna optimization
├── monitoring/              # Telegram sender
├── tests/                   # Unit tests
├── config.json             # Runtime configuration
└── .env                    # Environment variables (NOT in git)
```

## Принципы работы

### Рыночная нейтральность (дельта ≈ 0)

```
Long   27% × 5x = +135% дельта
Short  36% × 5x = -180% дельта
Virtual 35% × 1x = +35% дельта
─────────────────────────────────
Итого: +135 - 180 + 35 ≈ 0
```

Virtual — математически точная симуляция владения базовым активом на споте. Не дериватив, не маржинальная позиция.

### Логика ребалансировки

**Золотое правило: ни цента из свободной маржи на аккаунте!**

Каждый бот управляет **только своим начальным капиталом** и **собственной прибылью**:

1. Продажа излишков — только доли, достигшие порога `surplus`
2. Покупка дефицитов — только доли, достигшие порога `deficit`
3. Дефициты покупаются ТОЛЬКО на proceeds от продажи излишков
4. Если proceeds не хватает — дефицит покупается частично или пропускается

### Безопасность

| Механизм | Описание |
|----------|----------|
| **Trailing Stop** | Просадка от ATH TPV ≥ `equity_trailing_stop_pct` |
| **Margin Ratio** | Экстренное закрытие при ≤2x |
| **Liquidation Guard** | Закрытие позиции при ≤8% до ликвидации |
| **SPIKE/NET Trap** | Фильтр аномальных движений цены |
| **Safety Guards** | Velocity, Trend, Spread protection |

## Конфигурация

Основные параметры в `config.json`:

```json
{
  "portfolios": [{
    "targets": {
      "BASE_LONG":  {"share": 0.27, "leverage": 5},
      "BASE_SHORT": {"share": 0.34, "leverage": 5},
      "VIRTUAL":    {"share": 0.35, "leverage": 1}
    },
    "rebalance_threshold_surplus": 0.015,
    "rebalance_threshold_deficit": 0.03
  }],
  "equity_trailing_stop_pct": 10.0,
  "max_drawdown_limit": 33.0
}
```

## Команды

```bash
# Сканер тикеров
python scanner/rank_tickers.py

# Бэктест
python backtest/backtest_rebalance.py --ticker ZECUSDT
python backtest/backtest_rebalance.py --live --ticker ZECUSDT

# Запуск бота
python core/main.py --config config.json --ticker BTCUSDT

# Supervisor (управление роем)
python supervisor/supervisor_service.py

# Тесты
pytest
pytest --cov=. --cov-report=html
```

## Требования

- Python 3.11+
- Binance Futures API ключи
- Telegram Bot токен (для уведомлений)
- PM2 (для управления процессами)

## Лицензия

Proprietary — All rights reserved.
