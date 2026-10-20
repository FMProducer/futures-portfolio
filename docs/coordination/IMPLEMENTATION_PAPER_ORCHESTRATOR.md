# Перезапускаемый paper-оркестратор — 2026-10-17

## Основа и принятый коммит

User принял WebSocket-этап и поручил commit. Создан 41dee6e27d809646b908ddd36f64500a182090a5. Push в новом запросе не указан и не выполнялся; origin/master остаётся2013055. В коммит вошли только принятые код/отчёты, приватные stream/observer данные исключены.

Новый этап начат после коммита в checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio: перезапускаемый PAPER_CSV_V1 runner с отдельным симуляционным wallet. Это не live-paper на текущем рынке и не подключение старого dashboard paper_start.

## Exact diff

- Новый rebalance/paper.py: Runner, immutable input binding, атомарные paper observations, cursor, CLI pause/resume и согласованный экспорт.
- Новый rebalance/scenarios/test_paper.py:14 сценариев.
- rebalance/README.md: команды нового paper-режима и безопасного продолжения.
- Этот документ и reports/paper_2026-10-17 с проверками/фиксированным прогоном. Runtime SQLite исключён из Git.

kernel.py, durable.py, historical.py, параметры, live config, сетевые observer-модули, ledger выплат и соседние checkout не менялись. Paper-модуль не читает .env и не обращается в сеть. В нём нет торговых endpoint/клиентов или переключателя на real mode.

## Режим и атомарность

Runner использует тот же Portfolio/next_action/fill, что принятый historical replay. Один CSV-бар — один Store._apply с BEGIN IMMEDIATE: позиции/wallet/fees/funding, extrema, receipt IDs, immutable paper_bars, progress cursor, drawdown/peak и event journal фиксируются вместе. Нет успешного cursor advance без соответствующего состояния и fills. WAIT тоже сохраняет наблюдение/extrema и progress.

Paper fills исполняются атомарно внутри локальной транзакции; это допущение полной симуляционной исполнимости, а не обещание атомарности биржевых ног. Реальный outbox/orders не используется как притворная «отправка»: orders должен оставаться пустым, иначе runner блокируется. UNKNOWN/partial exchange execution остаются отдельным финансовым lifecycle-модулем, а не симуляцией сетевого успеха.

Детерминированный event_id paper-bar:index предотвращает повторную проводку завершённого бара. После restart продолжается next_index; gap, будущий индекс, конфликт price cursor и execution intent в paper DB отвергаются. При коллатеральном дефиците бар откатывается и остаётся блокирующим; фиктивная ликвидация/восстановление капитала не рисуются.

Исходные rows копируются в Runner, OHLC/шаг времени/конечность чисел проверяются. История, effective settings, funding mode/series и SHA исходников связываются с БД. Изменение данных/конфига/кода блокирует resume и требует явной миграции либо новой отдельной исследовательской БД. Переименование пути к неизменным данным не меняет canonical data hash. Для обычного продолжения надо сохранять ту же БД и выбирать новую папку отчёта.

БД иной схемы (observer/live/accounting) отвергается до создания таблиц Store. Новая paper DB использует WAL и synchronous FULL; отдельное keeper connection избегает checkpoint при каждом закрытии короткого Store-соединения. В конце процесса соединение закрывается. Экспорт portfolio/cursor/bars читается одной read transaction, чтобы не смешать разные шаги при конкурентном читателе.

## Семантика симуляции

Начальный капитал — только строка capital исследовательского config (сейчас10000USDT), не реальный баланс счёта и не новая аллокация User. Первые две позиции планируются через прежний preflight. Решения наблюдают предыдущий completed close и исполняются на current open с прежними spread/slippage. Funding в начале бара применяется до fills, внутри бара — после fills; cursor funding_index сохраняется вместе с cash.

Все прежние ограничения candle backtest сохраняются: trade prices вместо настоящего mark/book, full fills, упрощённая initial margin, отсутствие maintenance/liquidation/intrabar риска и издержек закрытия terminal позиций. Risk/TS и sizing не переопределялись, новый Net-floor не вводился.

## Команды pause/resume

Для нового запуска используйте новую paper DB; родительская папка БД должна существовать. Ниже — команды фактического единого fixed прогона:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.paper --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --database reports/paper_2026-10-17/runtime/paper.sqlite --max-bars 21600 --output reports/paper_2026-10-17/paused_21600
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.paper --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --database reports/paper_2026-10-17/runtime/paper.sqlite --output reports/paper_2026-10-17/completed_43200
```

--max-bars ограничивает число новых баров в этом вызове, не общий целевой индекс. Без флага обрабатывается весь остаток. Полностью завершённая БД даёт no-op при run, а не новый цикл торговли. Существующий output CLI отклоняет до начала обработки; файлы предыдущего отчёта не перезаписываются. Funding CSV либо явный --funding-zero обязательны.

result.json/trades.json/equity.json — сопоставимый результат всей уже обработанной части, manifest.json — PAPER binding, progress и COMPLETE/PAUSED с trading_authorized=false. Это cumulative report, не дублирующий набор новых сделок для импорта.

## Проверки

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/paper_2026-10-17/tests.xml
```

162 PASS:14 paper+148 принятых. Проверены restart parity, repeated bar, rollback progress/fills/extrema/funding при SQLite ABORT, nonzero funding после изменения количества ног, отказ при смене capital/step/candles/costs/funding, foreign DB без изменения байтов, entry min-notional fail, пропущенный бар, будущий индекс, закрытая сеть и независимая копия input. Тесты относятся к текущей версии. Не выполнялись whole-pytest, брокерские операции, оптимизация и независимая рецензия.

## Один fixed backtest конечного кандидата

Один BTCUSDT-кандидат,43200 свечей сентября2026, действительно завершён в двух процессах на одной БД: PAUSED после21600, затем COMPLETE после43200. Это не два независимых baseline прогона. Reference — ранее принятый reports/historical_2026-10-17/BTCUSDT. Проверка уже завершённых артефактов выполнена reports/paper_2026-10-17/verify.py, без повторного бэктеста; evidence — verification.json.

| Метрика | Принятый baseline | Candidate после restart |
|---|---:|---:|
| Свечи | 43200 | 43200 |
| Net OPEN_MTM, USDT | 11.06966847132799999999999 | 11.06966847132799999999999 |
| Fees, USDT | 4.2916094086720 | 4.2916094086720 |
| Ребалансы | 69 | 69 |

Совпали не только метрики result.json, но все fills в trades.json и каждая точка equity.json. В БД43200 paper_bars,43200 events,revision43200; orders0. SQLite integrity_check=ok. Binding исходников между процессами совпал и сверился с текущими SHA файлов. Funding ASSUMED_ZERO; комиссии уже внутри Net, повторно не вычитать. Подтверждена воспроизводимость offline pause/resume, не прибыльность на реальном рынке или готовность интеграции всего приложения.

## Границы и передача

Реализованы offline orchestration, pause/resume и paper evidence. Нет подключения live market feed, wall-clock scheduler, длительного supervisor, simulated partial fills/latency, risk admission, объединения с real user stream/observer или разрешения реальных заявок. Для корректности resume после аппаратного сбоя SQLite и ОС должны соблюдать fsync; испытано логическое ABORT/перезапуск между процессами, не обрыв питания/повреждение диска.

Следующий этап после приёмки — отдельный paper-runner на текущем публичном потоке с валидированными market snapshots, сохраняя симуляционный баланс и сетевые/риск-блокировки. Это потребует самостоятельной проверки входов; старый dashboard paper_start не используется. Новый paper-кандидат пока не закоммичен. После отчёта STOP.
