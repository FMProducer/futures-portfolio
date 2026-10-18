# Исполнения, комиссии, cash flows и UNKNOWN — 2026-10-17

## Основа и результат

User принял read-only этап и разрешил следующий. Перед работой HEAD=8950cd45964c39f916b709ad2da995a6201b382c, дерево чистое. Checkout: C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio.

Реализованы ограниченное чтение истории Binance, нормализация и встречная сверка cash facts, атомарное применение проверенного пакета к офлайн durable Store. Live CLI только читает и сохраняет evidence; автоматического применения истории счёта к портфелю не выполняет.

## Exact diff

- rebalance/binance_readonly.py: разрешены два дополнительных GET endpoint — userTrades и income, с фиксированным набором параметров.
- rebalance/reconcile.py: history, analyze, apply_offline и read-only CLI.
- rebalance/scenarios/test_reconcile.py: 22 сценария, включая параметризованные случаи.
- Этот документ и reports/reconcile_2026-10-17: тесты, regression replay, verification и локальная приватная history.

kernel.py, durable.py, стратегия, параметры, live config, accounting и реальные SQLite-портфели не менялись. Credentials читаются из указанного User файла в памяти, не выводятся и не копируются.

## История и полнота

GET /fapi/v1/userTrades читается для заданного symbol; GET /fapi/v1/income — для всего счёта, без потери переводов с пустым symbol. Временная область задаётся явно, start/end включительно, меньше 7 суток; live CLI принимает 1..144 часа. Конец live-окна — serverTime минус 10 секунд. Это небольшой lag, не доказательство того, что биржа больше не добавит запаздывающие записи.

Ответ длиной limit разбивается на два непересекающихся временных подокна с включительными границами. Одинаковые timestamps не перескакиваются. Если переполнена одна миллисекунда, исчерпан budget запросов или данные вне окна — ошибка, а не ложный COMPLETE. Identity trades=(symbol,id), income=(incomeType,tranId); конфликтующий payload блокируется. Это разбиение временного диапазона, а не зависимость от неподтверждённого параметра page.

Форматы проверены по официальным [Trade API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) и [Account API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account). Ограничения хранения истории и задержки источника остаются ограничениями доказательств; пустое окно не означает пустую историю счёта.

## Денежные источники

Нормализованный trade содержит symbol, trade/order IDs, сторону, positionSide, qty, price, commission, realized PnL и timestamp. Комиссия и realized PnL проводятся по fills. Income COMMISSION/REALIZED_PNL должны совпасть с ними по symbol/tradeId/типу и сумме; второй денежной проводки по этим income-записям нет.

Income FUNDING_FEE становится FUNDING, TRANSFER — EXTERNAL с исходным знаком. Перевод не становится торговой прибылью. Остальные типы дохода, неизвестные записи, чужой непокрытый trading income, не-USDT активы/комиссии и несовпадающие суммы блокируют применение. Конвертация BNB-комиссии по произвольной текущей цене не выполняется. Поддержка rebates/bonuses/insurance и их экономической классификации требует отдельного расширения, без молчаливого отнесения к прибыли.

## Атомарное разрешение UNKNOWN на офлайн-состоянии

apply_offline использует единственную Store._apply транзакцию BEGIN IMMEDIATE: все fills, комиссии, cash events, order statuses, освобождение резервов и snapshot/journal фиксируются вместе. Любая ошибка откатывает весь пакет. expected_revision блокирует устаревший план. Перекрывающиеся окна не дублируют fills/cash: стабильные namespaced IDs и receipts сохраняются между перезапусками. Повтор того же request event требует тот же исходный revision/payload и возвращает записанный результат.

До применения проверяются client_id, symbol, exchange orderId, BUY/SELL, positionSide, исходный объём и полнота исполнений: сумма предоставленных fills должна совпасть с executedQty order query. FILLED требует полного исходного объёма. PREPARED без dispatch, blocked_trade, неохваченные outstanding orders, off-step qty и новый fill после terminal состояния блокируют пакет. Терминальное состояние нельзя регрессировать.

Только после всех проводок CANCELED/REJECTED/EXPIRED освобождают остаток резерва; EXPIRED/EXPIRED_IN_MATCH нормализуются как CANCELED с сохранением исходной evidence в журнале. OPEN/PARTIALLY_FILLED сохраняют резерв на остаток. UNKNOWN с доказанными fills и terminal response может стать конечным состоянием без повторной отправки заявки.

Реализованный PnL сверяется с локальным базисом: расхождение больше 0.00000001 USDT блокируется. В пределах этой явно заданной технической точности cash/realized корректируются до фактического биржевого значения, чтобы не копить модельный дрейф. ADD требует строго нулевого realized PnL. Это не изменение Net-floor или ослабление торговых gates: gate не применяется задним числом к уже состоявшемуся fill.

Cash и trade в одну миллисекунду блокируются из-за неустановленного порядка. При multi-order одинаковых timestamps нужна отдельная проверка exchange sequence; производственный глобальный порядок пока не сертифицирован. Автоматическая обработка тяжёлого blocked_trade из предыдущего этапа по-прежнему не предусмотрена: нужен ремонт/сверка экономического состояния, а не снятие блокировки.

## Реальное чтение

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.reconcile --env-file C:\Python\Prosperous_Bot\futures_portfolio\.env --symbol BTCUSDT --hours 24 --output reports/reconcile_2026-10-17/private_live_01
```

Выполнено успешно: 0 trades BTCUSDT, 0 income за выбранное 24-часовое окно, blocking_reasons пусты. Полный raw evidence и границы окна — local history.json. Папка исключена из Git своим .gitignore, что проверено git check-ignore. Факт чтения сохранён в публичном verification.json без финансовых сумм/идентификаторов счёта и ключей.

Реальный UNKNOWN ордер не создавался, заявок для «проверки» не отправлялось. Разрешение UNKNOWN проверено на воспроизводимых fixtures. Отсутствие записей за сутки не доказывает полноту стартового баланса и не инициализирует реальный портфель.

## Проверки

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/reconcile_2026-10-17/tests.xml
```

111 PASS: 22 reconciliation, 14 read-only, 15 durable, 8 historical, 17 kernel, 35 accounting. Среди сценариев: UNKNOWN→partial→cancel с комиссией ровно один раз, reopen/overlap replay, funding и transfer, недостаточные fills, неверная сторона/ID/qty, PnL/basis mismatch, SQLite ABORT rollback, дубликаты, неизвестные активы/типы, saturated timestamp, exhausted request budget, terminal regression и округление реального PnL. Whole-pytest не выполнялся.

Один fixed backtest конечного кандидата:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/reconcile_2026-10-17/regression_BTCUSDT
```

| Метрика | Принятый baseline | Новый кандидат |
|---|---:|---:|
| Свечей | 43200 | 43200 |
| Net OPEN_MTM USDT | 11.06966847132799999999999 | 11.06966847132799999999999 |
| Fees USDT | 4.2916094086720 | 4.2916094086720 |
| Ребалансы | 69 | 69 |

Все result, trades и equity сверены с принятым baseline. Хеши кода/входов — verification и regression manifest. Funding в экономическом replay по-прежнему ASSUMED_ZERO; actual funding проверялся отдельными сценариями проводок и чтением истории, но не подменяет отсутствующую историческую funding-серию сентября. Комиссии уже в Net. Терминальные позиции открыты. Новый порог Net не вводился, оптимизации не было.

## Ограничения и передача

CLI не изменяет live state. apply_offline требует заранее доказанной привязки БД к счёту/инструменту и полного набора ордеров/исполнений; этого факта сам не аутентифицирует. Нет автоматического применения произвольной истории чужого/симуляционного счёта. Для production остаются account binding, начальный checkpoint wallet/positions, полный multi-symbol scope, поздние history records и постоянный cursor/watermark с overlap, WebSocket gap detection, account-wide сверка конечных балансов/позиций и recovery незавершённого цикла. REST-окно не выдаётся за согласованный snapshot.

Отправка/отмена заявок, торговые настройки Binance, live-проводки, ledger выплат и commit/push не затрагивались. Следующий этап после приёмки — привязка и checkpoint-сверка счёта с локальным состоянием, включая устойчивый курсор истории. После отчёта STOP.
