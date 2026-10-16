# Исторический replay общего кошелька — 2026-10-17

## Результат и scope

По команде User «Продолжай реализацию» после обсуждения проверочного бэктеста добавлен отдельный исторический replay нового ядра 25/25/50. Он читает локальные минутные CSV и не использует сеть, ключи, PM2 или Binance connector. Рабочий бот не переключался. Параметры не оптимизировались.

Checkout: C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio, базовый HEAD 1b4eb18937c5223dab6da9052eae1a5ebe15b91d. Существующие untracked файлы предыдущего этапа сохранены. rebalance/kernel.py не изменялся в этом этапе.

## Exact diff

Добавлены rebalance/historical.py, rebalance/historical_example.json, rebalance/scenarios/test_historical.py, этот документ и reports/historical_2026-10-17. Торговый код core, настройки live, accounting и соседний T-Invest checkout не менялись.

historical.py использует тот же Portfolio/next_action/fill, что и синтетические сценарии. Вход на первом open, затем одно решение на минуту: состояние оценивается по close предыдущей завершённой свечи, доступные цены исполнения — текущий open с заданными adverse costs. Это дискретная модель с оценкой, отстающей на один бар; не эквивалент тикового realtime. Размер сделки и fee gates рассчитываются на доступных в момент open ценах. Будущие close/high/low в решение не входят.

spread_bps — полный спред; половина спреда плюс slippage_bps добавляется к BUY и вычитается из SELL. Комиссии отдельно списываются с wallet. Funding CSV содержит timestamp,rate,mark_price; платеж=(qty_SHORT−qty_LONG)*mark_price*rate. События с временем начала бара применяются перед исполнением; остальные события бара — после исполнения и перед close. Дубликаты и неупорядоченные funding timestamps отвергаются. Числа читаются в Decimal. Свечи должны быть непрерывными минутными, упорядоченными, с корректным OHLC; пробелы/дубликаты блокируют запуск.

CLI требует либо --funding FILE, либо явный --funding-zero. Нулевой funding не маскируется под загруженную историю. Проверка полноты реальной funding-истории ещё отсутствует: CSV считается предоставленным источником событий. Стратегия может существенно измениться при реальном funding.

## Команда запуска

Из корня futures-portfolio (папка output должна быть новой):

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/historical_manual_BTCUSDT
```

Результаты: result.json (метрики), manifest.json (SHA256 входов/ядра/replay, effective config и ограничения), trades.json (исполнения), equity.json (кривая). Существующая output-папка не перезаписывается. Пример config — исследовательские допущения, не загруженные актуальные фильтры/комиссии Binance. Шаг .001 и min_notional 5.3 не являются подтверждённой спецификацией BTCUSDT.

## Проверки и один fixed backtest

Тесты:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/historical_2026-10-17/tests.xml
```

60 PASS: 8 исторических сценариев, 17 ядра, 35 accounting. Проверены отсутствие влияния будущих баров на прошлые исполнения, отсутствие сигналов по внутрисвечным extrema, издержки на постоянной цене, funding асимметричных ног, дубликаты и пропущенные свечи. Первый запуск одного нового теста ожидал неизменность последующих сделок после funding; событие перенесено в конец последнего бара для изолированной проверки денежной проводки. Код стратегии ради PASS не менялся. Whole-pytest не выполнялся.

Один fixed replay конечного кандидата:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/historical_2026-10-17/BTCUSDT
```

Вход: 43200 минут BTCUSDT, сентябрь 2026 (UTC), локальный CSV предыдущего аудита. Хеши и exact timestamp границы — в manifest/result. Начальная Equity 10000 USDT, leverage 7, fee .0004, полный spread 2 bps, adverse slippage 1 bps, surplus/deficit/trail=.7/.7/.09. Funding ASSUMED_ZERO. Сумма в config не меняет live capital=14.

| Метрика | Baseline | Candidate |
|---|---|---:|
| Net OPEN_MTM, USDT | сопоставимого нет | 11.069668471328 |
| Equity, USDT | — | 10011.069668471328 |
| Realized PnL до fees, USDT | — | 81.157954177179 |
| Unrealized PnL, USDT | — | −65.796676297179 |
| Комиссии, USDT | — | 4.291609408672 |
| Funding, USDT | — | 0 (допущение) |
| Ребалансы, без двух входных fills | — | 69 |
| Max drawdown по минутным close | — | 0.294468724651% |

Net уже включает fees, spread и slippage через цены fills. Положительный realized не заменяет Net; значительная часть компенсирована отрицательным UPL. Терминальные позиции LONG .030 / SHORT .035 остаются открытыми. Издержки их последующего закрытия не включены. Результат около +0.110697% не является прогнозом или допуском стратегии.

Старый simulator futures-portfolio признан недостоверным, поэтому его цифры не служат baseline. Рублёвый floor донора не применяется к этому новому кошельку и другому рынку. Нет нового одобренного порога Net; baseline не подменялся ради PASS.

## Ограничения и передача

Торговые candle prices выступают proxy mark, книги и ликвидность отсутствуют; full fills и фиксированная collateral модель, нет maintenance brackets/liquidation, intrabar риска, реальных фильтров и funding coverage validation. При Equity<=моделируемой initial margin replay останавливается с ошибкой, не рисует выдуманный результат ликвидации. Риск не оценивается только по close drawdown. CSV funding может отражать лишь один платёж на timestamp одного инструмента.

Durable состояние/outbox, UNKNOWN, partial fill/cancel races и read-only сверка биржи остаются следующими отдельными задачами. Автоматический выбор капитала/параметров, OOS и сравнение funding вариантов не выполнялись. Commit/push и реальные операции не выполнялись. После отчёта STOP для приёмки этого этапа.
