# Matched strategy/hold OPEN_MTM — 2026-10-18

## Результат и идентификация

User сообщил commit первого пункта draft и поручил продолжить. Checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio,HEAD3c5c364 (полный HEAD в verification.json),исходный status чистый. Добавлен отдельный offline comparison,не меняющий принятый planner/strategy.24h snapshot/worker не затронут;не ждём его результата для независимой реализации.

Candidate identity:HEAD+новые comparison.py/tests/docs/evidence. Manifest содержит SHA comparison/historical/kernel и входов;test SHA и HEAD — reports/comparison_2026-10-18/verification.json. Commit/push не выполнялись,торговые параметры/исторические входы не менялись.

## Exact diff

- Новый rebalance/comparison.py:hold,compare,CLI и provenance manifest.
- Новый rebalance/scenarios/test_comparison.py:10сценариев с parametrization.
- RESEARCH_ADMISSION_DRAFT.md:описание фактически реализованного matched OPEN_MTM контроля.
- Этот отчёт;reports/comparison_2026-10-18:tests.xml,verify.py,verification.json и fixed_BTCUSDT с полными arm results/trades/equity/manifest/comparison.
- Существующие historical/kernel/durable/accounting/public modules и settings не изменялись.

## Семантика контроля

Одинаковые initial virtual capital/config,source rows,funding rates,cost assumptions,timeline. Hold входит теми же двумя fills,что candidate,затем не вызывает next_action и не меняет inventory. Первые fills проверяются на exact equality,не просто совпадение долей. Funding на первом open до входа относится к flat positions;внутри/после баров — к фактическим arm quantities. Разные позиции после ребалансировок могут давать разный funding cash при одинаковом rate input:это не расхождение источников.

Обе стороны оцениваются на одинаковых bar closes. И entry fees,и funding уже внутри Net. Внешние потоки не поддержаны в интерфейсе;нет фиктивных deposits/сброса капитала. При невозможности entry/исчерпании collateral hold блокирует сравнение,не превращается в cash-only контроль и не рисует ликвидацию.

Валидация malformed/gapped/nonfinite rows,funding order/range/mark,costs выполняется до запуска candidate. Входы копируются;caller data не меняются. CLI требует явный funding CSV или funding-zero и новый output. Module не использует сеть/ключи/реальные orders/SQLite.

**Terminal OPEN_MTM у обеих сторон:**closing fills/издержки не моделируются. Это сознательно ограниченный исследовательский инструмент на принятой candle-модели;определения cycle/PF/terminal closeout не объявлены согласованными. profit_factor=null,cycle_definition=NOT_APPROVED,admission_ready=false.

## Проверки

18targeted tests PASS:10новых+8существующих historical. Покрыты неизменный hold inventory/отсутствие next_action,matched initial fills/curves,нулевой delta когда candidate не ребалансирует,ненулевой funding по pre-state и phase до entry/после entry,fees/Net identity,invalid inputs до candidate,entry block и отсутствие мутации inputs. Первоначальный тест сравнивал разные строковые записи нуля;исправлено на Decimal numeric equality,экономика кода не менялась.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_comparison.py rebalance/scenarios/test_historical.py -q --confcutdir=rebalance --junitxml=reports/comparison_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.comparison --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/comparison_2026-10-18/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.comparison_2026-10-18.verify
```

## Единственный fixed comparison итогового кандидата

BTCUSDT September2026,43200минут,initial10000USDT,.7/.7/.09,spread2bps,slippage1bps,fee.0004,leverage7. Funding assumed0. Эти данные уже использованы,не новый OOS.

| Метрика | Принятый strategy baseline | Candidate strategy arm | Hold |
|---|---:|---:|---:|
| Net OPEN_MTM |11.06966847132799999999999|11.06966847132799999999999|−3.0163046400000|
| Fees,уже внутри Net |4.2916094086720|4.2916094086720|2.0108697600000|
| Rebalances |69|69|0|
| Terminal LONG/SHORT qty |.030/.035|.030/.035|.032/.032|

Delta strategy−hold:14.08597311132799999999999USDT. Strategy result/trades/equity точно совпали с reports/historical_2026-10-17/BTCUSDT;hold initial fills совпали с первыми двумя candidate fills,hold fills всего2;все43200valuation timestamps совпали. Проверены current source/input SHA.

Положительный delta на одном использованном месяце не доказывает alpha/устойчивость/OOS. Closing costs исключены с обеих сторон,но при разных конечных inventory это не гарантирует нейтральности разницы. Реалистичные depth/latency/partial/liquidation иhistorical funding пока не покрыты. Admission и пороги договора не считаются выполненными.

## Передача

STOP для приёмки matched OPEN_MTM инструмента. При финальной проверке отдельный24h опыт уже завершился FROZEN/FAIL из-за невалидного capture interval/symbol;это не сбой comparison,см.IMPLEMENTATION_DAY_FORWARD_RESULT.md. Проход comparison не разрешает менять frozen inputs/повторять неудачное окно. До реализации PF/cycle/terminal policy нужны утверждённые определения. Независимую подготовку fixtures/данных можно продолжать после приёмки.
