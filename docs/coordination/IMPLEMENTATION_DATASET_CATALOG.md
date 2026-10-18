# Research dataset catalog — 2026-10-18

## Результат и scope

User поручил параллельно продолжать RESEARCH_ADMISSION_DRAFT.md. Реализован первый независимый инструмент: offline каталог данных. Реальные операции,новые экономические решения,скачивание24месячной истории и открытие закрытого OOS не выполнялись. Работающий day-forward/PID95292/snapshot не изменялись;во время передачи RUNNING,pending0.

Checkout:C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio,HEADd55cdf74e7cc59431e45452633136e3991d4ad41. На входе существовали документы/состояние запуска24h;они сохранены. Current candidate:HEAD+новые файлы,точные source SHA в reports/dataset_catalog_2026-10-18/verification.json. Commit/push не выполнялись.

## Exact diff

- Новый rebalance/dataset_catalog.py:captured-byte SHA,size,path;CSV table/1m/OHLC/time bounds,duplicates/gaps/order;optional funding CSV иfiltersJSON artifacts;declared provenance/usage;exclusive-create CLI output.
- Новый rebalance/scenarios/test_dataset_catalog.py:12сценариев идентичности/ограничений/ошибок данных.
- RESEARCH_ADMISSION_DRAFT.md дополнен фактическим статусом инструмента.
- Этот отчёт и reports/dataset_catalog_2026-10-18 с inventory/tests/fixed replay/verification.
- kernel/historical/durable/accounting/public-paper/session и параметры не менялись. Новый модуль не импортируется текущим worker и не использует сеть/ключи/SQLite.

## Свойства и границы

Файл читается в captured bytes один раз:SHA и CSV parse относятся к одному набору байтов. Структурные ошибки дают FAIL либо исключение с отсутствием результата;не объявляются валидными. Источник и символ явно caller-declared. Наличие URL/UTC даёт только DECLARED_NOT_AUTHENTICATED,а не подтверждение биржевого происхождения. Метаданные времени не восстанавливаются из mtime. SHA доказывает byte identity,не подлинность.

Usage unknown →OOS NOT_ESTABLISHED;previously_used →ALREADY_USED. CLI не позволяет объявить данные «fresh OOS». Catalog сам не знает всю историю доступов:unknown не означает свежесть. Даже отличный CSV не получает admission_ready или trading_authorized. Не установлен24/6месячный research split и не введены новые экономические gates.

Funding CSV проверяется по timestamp/rate/mark,порядку/диапазону,конечности чисел;полнота/расписание остаются NOT_ESTABLISHED. Пустой/отсутствующий funding не превращается в assumption zero. JSON filters архивируется по SHA,но не признаётся историческим snapshot автоматически.

## Проверки

**12 targeted tests PASS**,tests.xml. Проверены duplicate/out-of-order/missing/misaligned timestamps,некорректный OHLC/NaN/row width/headers,identity SHA,UTC/source declarations,funding range,отсутствие ложной полноты/OOS/admission иисторической применимости filters. whole-pytest/PM2 не запускались.

Известный BTCUSDT CSV:43200строк,1788220800000..1790812799999ms,ни одного gap/duplicate/out-of-order/misaligned/invalid row. SHA6b482fc0d05691cda897515d6cdb81d878b8fd3d40c3d46a2e2a88e5fc99284a совпадает с ранее закреплённым CSV. September2026 уже использован. URL взят из прежнего market_data_manifest.json;его archive SHA относится к ZIP,а не CSV,эти значения не приравниваются. Acquisition UTC неизвестен;provenance INCOMPLETE. Funding/filter files не предоставлены и не считаются проверенными.

BTCUSDT.json — первый inventory без declared source;BTCUSDT_declared_source.json — итоговый с URL из имеющегося manifest,без выдуманного acquisition UTC. Это повторная инвентаризация тех же байтов,не новый forward или новый независимый период.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_dataset_catalog.py -q --confcutdir=rebalance --junitxml=reports/dataset_catalog_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.dataset_catalog --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --symbol BTCUSDT --source https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip --usage previously_used --output reports/dataset_catalog_2026-10-18/BTCUSDT_declared_source.json
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/dataset_catalog_2026-10-18/fixed_BTCUSDT
```

## Один fixed backtest итогового кандидата

| Метрика | Baseline | Candidate |
|---|---:|---:|
| Candles |43200|43200|
| Initial equity |10000|10000|
| Net OPEN_MTM |11.06966847132799999999999|11.06966847132799999999999|
| Fees уже внутри Net |4.2916094086720|4.2916094086720|
| Rebalances |69|69|
| Funding assumption |0|0|

Result/trades/equity точно совпали с reports/historical_2026-10-17/BTCUSDT. Manifest содержит входные SHA/settings/code. Параметры.7/.7/.09,spread2bps,slippage1bps,fee.0004,leverage7. Этот regression replay не повышает provenance/OOS статус и не доказывает доходность;terminal позиции открыты,costs закрытия/реалистичные partial/liquidation не учтены.

**STOP для приёмки каталога.** Длительный24h опыт продолжает выполняться независимо. Определения торгового цикла/PF/terminal closeout и числовой live admission profile остаются предметом отдельного решения User,не заполнены автоматически.
