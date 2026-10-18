# Пользовательский поток и REST receive fence — 2026-10-17

## Принятый этап опубликован

По команде User checkpoint-этап закоммичен как 2013055dbb4b82fedccf192cba8029c174df4db9 и pushed в origin/master. git ls-remote подтвердил SHA. БД/UID/balances/raw checkpoint из private-папки не включались. Рабочий checkout: C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio.

Новый этап реализован после этого коммита и пока не принят/не закоммичен.

## Exact diff

- Новый rebalance/user_stream.py: ограниченная user-stream сессия, приватный SQLite inbox, эпохи соединений, dirty/recovery state и REST receive fence.
- rebalance/checkpoint.py: необязательный commit_guard внутри той же SQLite транзакции; сверка net inventory LONG/SHORT из истории с flat checkpoint. Прежний wallet-only bridge не доказывал отсутствие незакрытого объёма, поэтому добавлен блокирующий инвариант, без изменения торговых формул.
- rebalance/scenarios/test_user_stream.py:18 новых сценариев; test_checkpoint.py: ещё один inventory сценарий.
- Этот документ и reports/user_stream_2026-10-17 с evidence. Протокол проекта в Obsidian дополнен.

kernel.py, параметры, historical replay и финансовый durable Store не менялись. Дополнительных зависимостей не устанавливалось: используется уже объявленный aiohttp. Реальные торговые/платёжные операции отсутствуют.

## Сессия и секреты

Reader остаётся GET-only. Отдельная start_stream может выполнить только POST https://fapi.binance.com/fapi/v1/listenKey с API-key header — создание сессии чтения, не ордер/перевод. Нет произвольного POST, методов отправки/отмены заявок, изменения плеча или вывода. Token хранится только в памяти; он, signed URLs и ключи не выводятся. Входящие listenKey/apiKey/signature/secret поля рекурсивно удаляются до сохранения события. Ошибки транспорта приводятся к безопасным кодам.

WebSocket URL: wss://fstream.binance.com/private/ws/<listenKey>, TLS по умолчанию, trust_env=False, aiohttp heartbeat=5 секунд, ограниченный размер сообщения. Максимальная заданная фаза ожидания —120секунд, сверх неё возможны REST-запросы с отдельными timeout. Это bounded probe, не бесконечный демон. PUT keepalive для длительной работы пока не реализован. DELETE listenKey не выполняется: POST может вернуть уже активный token другого клиента, поэтому его отзыв нарушил бы чужое соединение. Закрывается только собственный сокет.

Форматы и lifecycle проверены по [Binance USD-M User Data Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams) и [REST User Stream API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/user-data-streams). Заголовки авторизации и socket URL не передаются в журнальные сообщения.

## Состояния и границы доказательств

Таблицы stream_state, immutable stream_records и stream_messages добавляются только к уже привязанной observer DB. Денежные регистры не изменяются событиями WS. Каждое новое соединение получает случайную epoch и NEEDS_REST; перезапуск/новая epoch не означает разрешения работать по старому snapshot. Stale epoch отсекается. Receive counters — локальные, не биржевые sequence numbers.

Payload digest обеспечивает dedup без повторной обработки идентичного события. Событие возвращает состояние в NEEDS_REST. Проверяется регрессия E/T внутри event type/epoch; это признак нарушения порядка, не способ доказать отсутствие пропуска. Expiry, неизвестный тип, конфигурационное или risk-событие блокируют обычную фиксацию. При disconnect состояние DISCONNECTED_NEEDS_REST сохраняется, включая нормальное завершение probe. Новый сеанс обязан снова свериться.

После REST/истории фиксируется локальный receive counter. В Observer.ingest callback внутри BEGIN IMMEDIATE проверяет epoch/counter/fault и события. Пришедшее между чтением REST и commit событие меняет counter и блокирует фиксацию. Состояние потока, evidence, facts и cursor продвигаются одной транзакцией. Ошибка повторной REST-сверки снимает прежний OBSERVING статус, не продвигая cursor. Ошибка SQLite откатывает и fence, и checkpoint; failure записывается отдельно как NEEDS_REST.

Успешный статус называется OBSERVING_NOT_CERTIFIED. trading_authorized всегда false. WS E/T не являются глобальной последовательностью и не доказывают полный согласованный Equity snapshot. Пустой поток тихого счёта не считается подтверждением событийной полноты.

## Встречная проверка событий

- ACCOUNT_UPDATE — delta: сохраняются изменения отдельных активов/позиций, без трактовки отсутствующих строк как нулей. Последний USDT wallet сравнивается с REST, последняя известная changed-position запись должна соответствовать flat состоянию. Другие активы блокируют этот scope. bc не проводится как второй доход.
- ORDER_TRADE_UPDATE — client_id/orderId/symbol/side/positionSide/origQty и cumulative qty сверяются с отдельным Query Order. Terminal status не подтверждается одним отсутствием openOrders. Полный executedQty должен покрываться REST fills.
- TRADE — id/qty/price/fee asset/fee/PnL сверяются с userTrades. Денежной проводки из WS нет: предыдущий источник trades/income остаётся единственным.
- Событие новее REST end требует нового fence; позднее событие до initial origin требует расследования. Новая epoch не удаляет старые экономические сообщения. Историческая expiry сама денег не меняет и допускает восстановление новой эпохой; исторические risk/config events автоматически не снимаются.

Net inventory из всех trades после flat origin должен быть нулевым по каждой LONG/SHORT ноге. Совпавший wallet с незакрытым объёмом теперь блокируется. Этот инвариант проверяет flat-only контракт, не добавляет принудительное закрытие позиций и не заменяет риск-модель.

## Проверки

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/user_stream_2026-10-17/tests.xml
```

148 PASS:129 принятых+18 stream+1 inventory. Проверены epoch/reopen, disconnect/reconnect, dedup, receive race, expiry/redaction, time regression, risk/unknown types, WS/REST wallet mismatch, future event, trade match, Query Order для terminal cancel, непокрытый executedQty, SQLite rollback, failed second fence и точный служебный POST endpoint. Новые fault scenarios не создают реальных заявок. Whole-pytest не выполнялся.

## Реальные probe

Использована уже привязанная private observer DB предыдущего этапа. Подключение read-only, без изменений выделенного капитала. Команда конечного кандидата:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.user_stream --env-file C:\Python\Prosperous_Bot\futures_portfolio\.env --database reports/checkpoint_2026-10-17/private/observer.sqlite --seconds 2 --output reports/user_stream_2026-10-17/private/live_final
```

Предварительные bounded probes на10 и3секунды проверяли подключение/закрытие; после добавления guards выполнен live_final. У конечного кандидата 2 успешных REST fences, received_events=0, итог DISCONNECTED_NEEDS_REST, trading_authorized=false. Новых ордеров ради получения событий не создавалось; положительные и fault event paths подтверждены fixtures, а не реальными trades.

Socket и приватные таблицы/evidence локальны; parent private .gitignore сохраняет их вне Git. После выхода нет фонового monitor-процесса. Никаких выводов о непрерывной24/7 работе или полном восстановлении при нагрузке не делается.

## Один fixed backtest конечного экономического кандидата

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/user_stream_2026-10-17/regression_BTCUSDT
```

| Метрика | Принятый baseline | Candidate |
|---|---:|---:|
| Свечи | 43200 | 43200 |
| Net OPEN_MTM USDT | 11.06966847132799999999999 | 11.06966847132799999999999 |
| Fees USDT | 4.2916094086720 | 4.2916094086720 |
| Ребалансы | 69 | 69 |

result/trades/equity совпали точно. Хеши исходников/evidence — verification.json и replay manifest. Funding ASSUMED_ZERO, прежние spread/slippage допущения, terminal позиции открыты. Реальная WS-сессия не используется как историческая цена/история funding. Экономические параметры и floor не менялись, оптимизации нет.

## Ограничения и передача

Нет production trading admission, WebSocket биржевого replay/sequence API, гарантированной полноты global event order, длительного keepalive/lease/автореконнекта, нагрузочного теста непрерывного потока, multi-symbol non-flat state, repair blocked_trade и интеграции наблюдения с live финансовым Store/ledger. ACCOUNT_UPDATE delta и REST fill evidence не устраняют net-zero gaps автоматически. Full-backfill ограничен прежним window contract; слишком старый origin блокирует и требует отдельного восстановления, а не skip.

Успешное тихое окно доказывает транспорт и два наблюдательных fence на проверенном счёте в это время. До реальных операций нужны отдельные этапы и приёмка. Следующий практический этап — paper orchestrator нового ядра с независимым симуляционным балансом и этими блокировками либо долгоживущий observer supervisor; новых политик риска/размера в этом этапе нет.

Принятый checkpoint уже pushed2013055. Новый user-stream кандидат остаётся рабочими изменениями для приёмки. После отчёта STOP.
