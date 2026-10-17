# Account binding, checkpoint и курсор — 2026-10-17

## Основа и публикация принятого этапа

Перед работой HEAD=8950cd45964c39f916b709ad2da995a6201b382c. По явной команде User принятый reconciliation-этап закоммичен как 1795e75c2f4462a077d6dd27efd5ccddb6886d4c и отправлен в origin/master; git ls-remote подтвердил полный SHA. Первая sandbox-попытка push получила Schannel SEC_E_NO_CREDENTIALS; разрешённый повтор прошёл. Private history/credentials в коммит не вошли.

User одновременно поручил протокол Obsidian и следующий этап. Создана Projects/autonomous-asset-management.md в D:/Obsidian/MyVault/Hermes: решения, принятые этапы, code/evidence paths, commits, проверки и открытые ограничения. Исходные заметки не переписывались. Новый checkpoint-кандидат ещё не принят и не включён в этот коммит.

## Exact diff нового этапа

- rebalance/binance_readonly.py: GET /api/v3/account только на фиксированном api.binance.com для UID; GET /fapi/v1/openAlgoOrders для проверки отсутствия условных заявок. Остальные endpoints остаются на fapi.binance.com. Redirect/TLS/GET allowlist сохранены.
- rebalance/checkpoint.py: UID binding, наблюдательный SQLite checkpoint, provisional cursor, overlap/backfill и wallet bridge.
- rebalance/scenarios/test_checkpoint.py: 18 проверок, включая параметризованные случаи.
- Этот документ, reports/checkpoint_2026-10-17 и дополнение протокола Obsidian.

Торговое kernel.py, durable Store, live config, параметры, ledger выплат и T-Invest не изменялись.

## Привязка счёта

Стабильный UID берётся из подписанного GET /api/v3/account тем же ключом, что используется для USD-M. Ответ spot-балансов не сохраняется: используется только UID. В SQLite сохраняется SHA256 от namespace mainnet+UID вместе с USD-M/symbol/USDT/purpose=OBSERVER_ONLY_V1. Это не отпечаток API key: ротация ключа того же UID не переименовывает счёт. Отсутствие UID блокирует привязку, запасного пути по имени файла/ключу нет.

Формат UID проверен по [Binance Spot Account API](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/account). [USD-M Trade API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) используется для algo orders. Реквизиты UID/хеш и реальные суммы не попадают в публичные отчёты.

БД наблюдения отделена от trading Store. Observer отвергает имеющуюся БД другой схемы. Привязка immutable; случайное подключение другого UID, market или symbol блокирует ingest. Изменение файла пути не превращает симуляцию в реальный счёт. Весь balance счёта не назначается автоматически капиталом стратегии; 14/10000 USDT из существующих configs не меняются.

## Начальный checkpoint

Поддерживается только stable flat account: Hedge Mode, single asset, нет активных позиций всего USD-M счёта, обычных и algo orders, USDT UPL=0 и initial margin=0. Два financial REST-brackets должны совпасть. UID читается до и после сбора. Во время initial-collection не должно быть обнаруженных trades/income. Окно baseline history включает 5 минут до начала, чтобы известные старые записи можно было сверять при последующем overlap.

Сохраняются baseline wallet, origin server time, исходное наблюдение/evidence и history facts. initialize не сбрасывает существующую привязку. Это OBSERVER_ONLY checkpoint, не production starting portfolio: REST/история имеют задержки и не обеспечивают атомарный event-stream barrier. Новая поздняя запись <=origin, не известная в baseline, блокирует продвижение; переинициализация для обхода не делается.

## Устойчивый курсор

На каждом опросе читается persisted binding/cursor, проверяется UID и читается окно начиная минимум с through−300000 ms. --full-backfill перечитывает от origin−300000. Прежние facts в reread-окне должны остаться с тем же payload; исчезновение/изменение записи — ошибка. Новые события внутри overlap принимаются один раз по естественным IDs. Cursor не равен последнему event timestamp: пустое успешно проверенное окно тоже продвигает observed through.

Сверка накопленного изменения USDT wallet от origin:

```
expected_wallet = initial_wallet
                + Σ(trade.realizedPnl − trade.commission)
                + Σ(income.FUNDING_FEE + income.TRANSFER)
```

COMMISSION/REALIZED_PNL income сверяются, но повторно не прибавляются. Используется предыдущий analyze: неизвестные income типы/валюты и несогласованные торговые потоки блокируются. Все active facts после origin проверяются повторно, включая поздние внутри overlap. Реальный wallet должен совпасть точно. При несовпадении новые facts/checkpoint/cursor не фиксируются. Cursor/facts/evidence commit одной BEGIN IMMEDIATE/synchronous FULL транзакцией; stale cursor и параллельный устаревший poll отклоняются.

Курсор **предварительный**, не обещание окончательной полноты. Поздние записи за пределами overlap могут потребовать full-backfill; net-zero пропуск не обнаруживается одним wallet bridge. Полнота WS sequence отсутствует. Один history запрос поддерживает область меньше7суток; длительный пропуск/старый full-backfill блокируется, автоматического skip вперёд нет. Неплоский счёт тоже блокируется: модель активных позиций/initial units/open fees ещё не восстанавливается из live snapshot.

## Реальное выполнение

Одна приватная БД: reports/checkpoint_2026-10-17/private/observer.sqlite. Вся private-папка исключена из Git (проверено git check-ignore). Реальные UID, финансовые суммы и raw evidence в публичный отчёт не копируются. Credentials читаются из ранее разрешённого User .env в памяти.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.checkpoint --env-file C:\Python\Prosperous_Bot\futures_portfolio\.env --database reports/checkpoint_2026-10-17/private/observer.sqlite --symbol BTCUSDT --initialize --output reports/checkpoint_2026-10-17/private/init_01
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.checkpoint --env-file C:\Python\Prosperous_Bot\futures_portfolio\.env --database reports/checkpoint_2026-10-17/private/observer.sqlite --symbol BTCUSDT --full-backfill --output reports/checkpoint_2026-10-17/private/poll_01
```

Обе команды выполнены успешно: OBSERVER_INITIALIZED, затем OBSERVER_CHECKPOINT_MATCHED, new_facts=0. БД открыта заново вторым процессом, привязка сохранилась, wallet bridge совпал и cursor продвинут. Обычных/algo orders и позиций не было. trading_authorized=false на обоих шагах. Для повторного poll нужен новый output, но та же БД; --initialize повторять нельзя. Реальная БД уже существует и не должна заменяться симуляцией.

## Проверки и один fixed backtest

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/checkpoint_2026-10-17/tests.xml
```

129 PASS:18 новых+111 принятых. UID-only binding/no fallback, exact signed GET host, reopen/no reset, wrong-account/scope, foreign DB schema, stale/missing overlap, wallet mismatch, disappearing facts, late pre-origin record, active initial account, activity during bootstrap, SQLite fault rollback, late facts внутри overlap, duplicate-free cash и flat roundtrip fees проверены fixtures. Whole-pytest не выполнялся.

Один новый fixed replay на неизменных входах/параметрах:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/checkpoint_2026-10-17/regression_BTCUSDT
```

| Метрика | Принятый baseline | Candidate |
|---|---:|---:|
| Свечи | 43200 | 43200 |
| Net OPEN_MTM, USDT | 11.06966847132799999999999 | 11.06966847132799999999999 |
| Fees, USDT | 4.2916094086720 | 4.2916094086720 |
| Ребалансы | 69 | 69 |

Все result/trades/equity точно совпали. SHA исходников/evidence — verification и replay manifest. Funding ASSUMED_ZERO, spread/slippage модельные, terminal positions открыты, close costs не включены. Нет нового Net-floor, оптимизации или изменения sizing.

## Передача

Выполнена наблюдательная привязка и flat wallet checkpoint с перезапускаемым курсором. Нет live execution, автоматического выделения капитала, связи observer DB с real trading Store, WebSocket sequence barrier, полноты после произвольной многодневной задержки, восстановления неплоского портфеля и автоприменения UNKNOWN в live. Следующий этап — наблюдательный event-stream/barrier и проверка полноты account-wide истории либо отдельный paper orchestrator; допуск реальных заявок не выдан.

Новый кандидат не включён в коммит принятого reconciliation-этапа. После отчёта STOP для приёмки.
