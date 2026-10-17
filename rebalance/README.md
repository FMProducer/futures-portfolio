# Офлайн-ядро 25/25/50

Модуль не отправляет биржевые заявки. Один физический wallet, LONG/SHORT,
расчётные цели D_LONG=D_SHORT=E_trade/4, остаток 50%. Остаток не равен
биржевой available margin. Экономика — kernel.py, исторические свечи —
historical.py, сохранение состояния и жизненный цикл — durable.py.

## Запуск принятого исторического сценария с SQLite

Из корня репозитория. Выберите новые имена для БД и результата:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --state-db reports/my_replay.sqlite --output reports/my_replay
```

Режим фиксирует каждое наблюдение в SQLite synchronous=FULL; он значительно
медленнее обычного replay. CSV, config и код идентифицируются SHA256 в manifest.
Файл БД — локальное состояние, его нельзя удалять/создавать заново для обхода
незавершённых намерений. CLI делает новый прогон и не является механизмом
продолжения прерванного бэктеста с середины CSV.

## Офлайн-жизненный цикл

1. Store.initialize(portfolio) — однократно для новой БД; Store.load() — при
   перезапуске. Повторная initialize отклоняется. Сохраняются extrema и receipts.
2. Store.observe(event_id, mark, bid, ask) фиксирует наблюдение и решение.
   Все расчёты и prepare в будущем orchestrator должны последовательно работать
   на одной revision; конкурентные наблюдения требуют нового расчёта.
3. Store.prepare(..., expected_revision=revision) атомарно сохраняет намерение,
   стабильный client_id и резерв. Для начальной ноги entry=True; подтверждённые
   partial fills накапливают initial_units для прежнего cap 2*initial_units.
4. Store.transition(...,'DISPATCHING') выдаёт dispatch_granted=True один раз.
   Повтор того же event_id выдаёт False. В этом модуле фактической отправки нет.
5. При известном принятии OPEN, при таймауте UNKNOWN. Перезапуск не возвращает
   DISPATCHING/UNKNOWN в PREPARED. Сверять существующую заявку по client_id;
   не повторять отправку из-за отсутствия ответа.
6. Store.fill(trade_id,client_id,units,price,fee,mark) принимает отдельный
   подтверждённый trade, а не cumulative executed quantity. Replay одного trade
   не дублирует проводку. Указывать одинаковый нормализованный payload.
7. CANCEL_PENDING не освобождает резерв; поздние fills продолжают проводиться.
   Store.finalize(...,'CANCELED',confirmed_filled=n) разрешён только после
   получения всех fills, составляющих подтверждённое итоговое n.

Любое outstanding намерение блокирует новые намерения и сигналы. При
RECONCILIATION_REQUIRED резерв сохраняется и событие хранится для отдельной
сверки. Автоматического снятия этого состояния нет. Доказательства должен
предоставлять будущий проверенный адаптер; Store не аутентифицирует их.

Приложение не должно напрямую изменять таблицы или переносить старые состояния
без migration/reconciliation. Репозиторий пока не содержит production dispatcher,
нормализации биржевых маржинальных требований и согласованного account snapshot.

Полный отчёт этапа: docs/coordination/IMPLEMENTATION_DURABLE_TRADING_STATE.md.
