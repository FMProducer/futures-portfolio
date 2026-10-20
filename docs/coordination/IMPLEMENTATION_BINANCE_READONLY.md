# Binance USD-M: read-only наблюдение — 2026-10-17

## Основа и выполненный scope

User указал C:/Python/Prosperous_Bot/futures_portfolio/.env и разрешил продолжить. Использован только для чтения ключей в памяти процесса; содержимое, значения ключей и подписанные URL не выводились и не сохранялись. Credentials не копировались в новый checkout.

Checkout: C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio. HEAD перед работой: 0a695f9d4522513ef0b37f503f34c096800f6b2e, дерево чистое. Предыдущая durable-реализация уже присутствует в этом коммите. Торговое ядро, параметры и SQLite state не изменялись.

Добавлены rebalance/binance_readonly.py и rebalance/scenarios/test_binance_readonly.py, этот документ и reports/binance_readonly_2026-10-17. Публичные отчёты содержат тестовые результаты/регрессию; приватное REST-наблюдение исключено из Git своим .gitignore. Проверка git check-ignore подтвердила исключение observation.json.

## Граница доступа

Клиент использует фиксированный HTTPS host fapi.binance.com, GET-only Request и закрытый список endpoints/параметров. Нет методов создания/изменения/отмены ордеров, переключения Hedge/Multi-Assets, изменения плеча, переводов/выводов или listen-key POST. Redirect блокируется, TLS проверяется стандартным urllib. HMAC вычисляется в памяти. Ошибки сохраняют только kind/http_status/api_code; тело ответа, API key и signature в диагностику не попадают. Таймаут 15 секунд, автоматических повторов нет.

HTTP ProxyHandler отключает HTTP-прокси из среды/Windows. Это не обход системного TUN: маршрут actual сетевого соединения этот этап отдельно не доказывает. VPN-конфигурация в этом этапе не менялась.

Публичные чтения: server time и exchangeInfo. Подписанные: account V3, positionRisk V3, position mode, multi-assets mode, openOrders, опционально query order по origClientOrderId. Источники форматов: [Account API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account), [Trade API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade).

## Реальная проверка доступа

Команда из корня checkout:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.binance_readonly --env-file C:\Python\Prosperous_Bot\futures_portfolio\.env --symbol BTCUSDT --output reports/binance_readonly_2026-10-17/private_live_01
```

Выполнена успешно. Hedge Mode=true, Multi-Assets=false. Активных позиций=0, обычных открытых ордеров=0. Два REST-чтения финансовых данных совпали, USDT wallet+UPL=marginBalance. Прочитаны реальные фильтры BTCUSDT; исследовательский Config автоматически ими не заменяется. Финансовые суммы и raw observation остаются локальными; в публичный отчёт не копируются. Имя output должно быть новым при повторном запуске.

Это проверка доступа и арифметической согласованности наблюдения, не сертификация риск-модели. До/после REST сравниваются все финансовые поля account, позиции и обычные open orders; updateTime исключён как служебное поле. Совпадение двух чтений не доказывает отсутствие промежуточного события. Результат всегда coherence=REST_OBSERVATION_ONLY и trading_authorized=false.

Проверяется USDT bucket, не сумма номиналов позиций плюс wallet. availableBalance хранится отдельно от Equity и margin. One-way, Multi-Assets, несходящееся wallet+UPL и изменившиеся данные отмечаются как блокирующие причины. Активные позиции других валют, Portfolio Margin и isolated/cross нюансы не сертифицированы. Счёт не инициализирует автоматически симуляционный portfolio и не определяет размер выделенного капитала.

## UNKNOWN и границы сверки

При --state-db SQLite открывается mode=ro без Store.initialize и без проводок; читать следует только БД, доказуемо относящуюся к этому инструменту/счёту. Автоматической привязки account identity к БД пока нет. Симуляционные БД бэктестов не являются live evidence.

inspect_order проверяет client_id, symbol, направление LONG/SHORT и BUY/SELL. Ответ -2013, таймаут и другие ошибки дают KEEP_UNKNOWN, а не FAILED/отмену/разрешение повторной отправки. FILLED/CANCELED возвращаются только как EVIDENCE_ONLY_NO_STATE_CHANGE: query order не реконструирует отдельные fills, комиссии, funding или внешние потоки. В реальной проверке --state-db не передавался; неизвестных live-заявок для сверки не заявлено. Поведение UNKNOWN проверено на fixtures, не на созданных реальных ордерах.

Обычный openOrders не является проверкой всего algo-order контура. Нет полной user-trades pagination/history, учёта неторговых потоков, WebSocket sequence/gap recovery и подтверждённого атомарного снимка. Автоматическое разрешение UNKNOWN и применение результатов в durable Store остаются заблокированы до реализации этих источников. Ключевые права withdrawals/trading и IP restrictions отдельно не аудировались; успешный GET не доказывает отсутствие лишних прав ключа.

## Проверки

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/binance_readonly_2026-10-17/tests.xml
```

89 PASS: 14 read-only, 15 durable, 8 historical, 17 kernel, 35 accounting. Проверены allowlist, только GET, безопасная диагностика HTTP-ошибки с чувствительным ответом, bracket mismatch, mode/equity checks, missing USDT, UNKNOWN при not-found, отсутствие автоматической проводки terminal order, identity mismatch, неизменность SQLite при чтении и отсутствие изменения os.environ при загрузке .env. Тесты не используют реальный ключ.

Один fixed replay конечного экономического кандидата, без оптимизации:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/binance_readonly_2026-10-17/regression_BTCUSDT
```

| Метрика | Принятый baseline | Новый fixed replay |
|---|---:|---:|
| Свечей | 43200 | 43200 |
| Net OPEN_MTM, USDT | 11.06966847132799999999999 | 11.06966847132799999999999 |
| Fees, USDT | 4.2916094086720 | 4.2916094086720 |
| Ребалансы | 69 | 69 |

Файлы result/trades/equity сравнены с принятым baseline; результат сравнения и source hashes — verification.json. Комиссии уже в Net. Funding ASSUMED_ZERO, прежний моделируемый spread/slippage, открытая терминальная позиция. Исторический replay не использует реальные балансы и не является торговлей на счёте. Whole-pytest не запускался.

## Передача

Read-only доступ подтверждён. Включение live trading, принятие snapshot как стартового состояния, отправка/отмена ордеров, изменение настроек Binance, соединение с ledger выплат, commit/push не выполнялись. Следующая техническая задача после приёмки — полная нормализация исполнения/комиссий/неторговых потоков и доказуемая сверка с durable состоянием. После отчёта STOP.
