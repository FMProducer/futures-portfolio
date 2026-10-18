# Проверка сохранённых research evidence — 2026-10-18

Принятый этап registered research runner закоммичен и отправлен в origin/master: 69d528d (Add registered snapshot research runner). Checkout: C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio. Перед новой работой дерево чистое. Новый этап передаётся отдельно, без commit/push до приёмки.

## Изменения и назначение

- Новый rebalance/research_verify.py: отдельная проверка уже сохранённого registered study. SQLite открывается только mode=ro в одной read-транзакции; Registry.__init__, запускающий schema/write, не вызывается. Код из snapshot не импортируется, subprocess/network не используются.
- Новый rebalance/scenarios/test_research_verify.py: 10 сценариев, включая повреждения входов/исходников/результата, отсутствие evidence/БД, FAILED study и противоречивые результаты с согласованно обновлёнными локальными SHA.
- docs/coordination/RESEARCH_ADMISSION_DRAFT.md: фактический статус инструмента. reports/research_verify_2026-10-18: tests.xml, verification.json и source_identity.json.

Проверяются существование PROSPECTIVE/COMPLETED записи, integrity SQLite, aware timestamps регистрации/завершения и их порядок, совпадение конкретной записи с registry_report и registration; точные роли snapshot, funding mode, SHA/размер и размещение файлов внутри study. Затем проверяются восемь evidence, execution manifest/settings/import path, summary/arm results, OPEN_MTM convention, исходный капитал, Net=Equity−capital, сумма fees, количество trades, длина/границы/непрерывность curve, равенство timeline двух arms и первых fills с hold, delta Net. Fees повторно из Net не вычитаются.

Повреждения дают исключение в Python API; CLI записывает FAIL и exit2 для ожидаемых ошибок входов. Optional --output создаёт только новый файл через exclusive open; существующий файл не перезаписывается. По умолчанию вывод только stdout. Архив должен оставаться по исходным абсолютным путям; перенос не поддержан.

## Проверки

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_research_verify.py -q --confcutdir=rebalance --junitxml=reports/research_verify_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.research_verify --database reports/research_runner_2026-10-18/runtime/trials.sqlite --study reports/research_runner_2026-10-18/fixed_BTCUSDT --output reports/research_verify_2026-10-18/verification.json
```

10 tests PASS. Интеграционные fixtures используют только две синтетические свечи и собственные временные DB/output. Тест read-only проверяет неизменность bytes DB и всех study files. Тесты смысловых конфликтов явно моделируют переписанные владельцем локальные evidence/SHA и доказывают обнаружение проверяемых противоречий; это не доказательство защиты от произвольного злонамеренного изменения всего архива.

Существующий fixed-BTCUSDT-current: PASS EXPORTED_EVIDENCE_CONSISTENCY, 8 snapshot artifacts +8 evidence. Экономические модули/параметры не менялись; это анализ готового evidence, поэтому нового полного backtest не запускали. Ранее принятые результаты не выдаются за новый расчёт:

| Метрика сохранённого опыта | Baseline | Сохранённый registered candidate |
|---|---:|---:|
| Strategy Net OPEN_MTM |11.06966847132799999999999|11.06966847132799999999999|
| Strategy fees (в Net) |4.2916094086720|4.2916094086720|
| Rebalances |69|69|
| Hold Net OPEN_MTM |−3.0163046400000|−3.0163046400000|

Новый верификатор проверяет внутренние связи; он не повторяет прежнее независимое сравнение всех рядов с baseline и не пересчитывает позиции/PnL/funding из fills. Источник чисел и прежней регрессии: IMPLEMENTATION_REGISTERED_RESEARCH_RUNNER.md и его frozen evidence. Git diff --check без ошибок.

## Ограничения и передача

PASS не доказывает независимую аутентичность реестра, выполнение snapshot в прошлом, отсутствие незарегистрированных попыток или OOS independence. Циклы/PF, terminal closeout, liquidity/partial/latency/liquidation и live profile остаются открытыми предметами draft. Admission/trading flags false. Новые экономические решения не принимались.

Во время проверки отдельный PID124800 жив, start18Oct10:43:23Z, RUNNING, captured450, pending0, last_server_time1792323402679, stderr пуст. Snapshot суточного опыта не изменён; его итог ещё не получен.

STOP для приёмки отдельного инструмента проверки evidence.
