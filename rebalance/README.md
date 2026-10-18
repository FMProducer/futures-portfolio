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

## Paper CSV с продолжением после остановки

Новый модуль работает только с локальными CSV и отдельным симуляционным
wallet из config. Ключи, Binance observer DB и старый dashboard не используются.
Пример первого запуска на 500 новых свечах (выберите новую БД и папку отчёта):

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.paper --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --database reports/my_paper.sqlite --max-bars 500 --output reports/my_paper_first
```

Продолжение теми же входами на **той же БД**, с новой папкой отчёта:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.paper --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --database reports/my_paper.sqlite --output reports/my_paper_complete
```

Не удаляйте БД для продолжения и не меняйте входы/config/source code между
запусками: immutable binding блокирует такое смешивание. --max-bars означает
число новых наблюдений в данном вызове. COMPLETE означает, что CSV обработан;
это не допуск стратегии к реальной торговле. Файлы отчёта содержат накопленный
результат, а не только последнюю порцию. Runtime БД не следует добавлять в Git.

Отчёт: docs/coordination/IMPLEMENTATION_PAPER_ORCHESTRATOR.md.
# Public REST paper: isolated balance

Capture validation now records structured rejected public snapshots in the immutable session log. A capture span >10s or quote age >10s is discarded and retried within the existing consecutive-error bound (default3). It never produces fills or moves the portfolio cursor. Wrong symbols, malformed/future/regressing timestamps and invalid financial payloads remain fatal. The >120s observation-gap guard remains in force; retrying cannot bridge it. Historical failed windows remain failed and require a separate explicitly authorized experiment/version.

For a supervised foreground session, use:

`python -m rebalance.public_session --config rebalance/historical_example.json --database reports/public_session_manual/runtime/paper.sqlite --output reports/public_session_manual/run_01 --duration 3600`

Duration is bounded to 1–86400 seconds (default 60), polling every 5 seconds; Ctrl+C stops and keeps state. The session holds an OS lock, journals captured inputs before applying them, and recovers unacknowledged inputs idempotently after restart. Use a NEW report folder each time; resume with the SAME database and code/config within the 120-second observation bound. This version changes the code binding, so previous public-paper databases require a separate experiment, not silent migration. A gap >120 seconds, invalid data or exhausted read retries durably FROZEN the session; there is no automatic reset. Network failure does not liquidate or clear simulated positions.

BTCUSDT regular funding can be reconciled using the exact scheduled timestamp from public funding history. The cash amount uses the simulator's pre-event quantities and the published funding mark/rate, not private account receipts. Quotes are collected again after the funding fetch. Funding, fills and time/horizon advance commit together. Missing/ambiguous/Special funding cannot advance state. See docs/coordination/IMPLEMENTATION_PUBLIC_SESSION.md for checks and remaining limitations.

`python -m rebalance.public_paper --symbol BTCUSDT --config rebalance/historical_example.json --database reports/public_paper_manual/runtime/paper.sqlite --output reports/public_paper_manual/probe_01 --samples 2`

This bounded command reads only public Binance endpoints, without keys. Use a NEW database for a new experiment and a NEW output folder on every invocation. The database is bound to code, configuration, symbol and exchange filters. It cannot import an observer, CSV-paper or real account database.

The first observation initializes simulated positions from the configured virtual capital. Subsequent observations permit at most one decision per UTC minute, using the observed mark and best bid/ask with adverse configured slippage. The observed top quantity and exchange quantity/notional filters gate simulated full fills. Configured spread_bps is retained in the binding for provenance but is NOT added to the actual observed spread. These fills are theoretical and do not prove available exchange execution or account Equity. Fee/leverage settings remain research assumptions.

Capture span and quote age must be <=10 seconds; a gap >120 seconds freezes processing. Crossing a previously recorded funding timestamp with an open simulated position freezes with funding UNKNOWN, without assuming zero or submitting anything. No reconciliation/reset shortcut is supplied. Repeating an identical observation is idempotent; conflicts and old observations are blocked. Restart within these bounds continues the SAME simulation. This is a bounded REST paper experiment, not a continuous WebSocket service or real-trading admission. See docs/coordination/IMPLEMENTATION_PUBLIC_PAPER.md.

