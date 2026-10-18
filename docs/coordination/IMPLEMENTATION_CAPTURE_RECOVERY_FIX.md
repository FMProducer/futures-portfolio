# Capture validation/recovery fix и новый24h запуск — 2026-10-18

## Диагноз

Предыдущий24h опыт завершился FROZEN/FAIL:ValueError invalid capture interval or symbol,после223применённых наблюдений. Код старой validate объединял в это сообщение before/after type,positive time,after-before<0/>10000 иsymbol mismatch. Session перехватывал только ReadError как временную ошибку;любой такой ValueError сразу попадал в общий fatal handler. PublicReader.capture валидировал до возврата,поэтому отвергнутые public bytes/поля не попадали ни в inbox,ни в diagnostic log.

**Подтверждённые дефекты:**нет различения временного непригодного price observation и повреждённого/чужого payload;нет evidence отвергнутого capture. Медленный capture вызывает тот же аварийный путь,что wrong symbol. Это воспроизведено фиксированными тестами.

**Точный исходный trigger не установлен:**завершившийся процесс не сохранил отвергнутый снимок. Превышение10s правдоподобно,но не доказано. Последний принятый снимок валиден и не подменяет отсутствующий rejected input. Не объявляем установленной сетевую/серверную первопричину,которой evidence не содержит. Исходный FAIL и его DB/export не менялись.

## Минимальное исправление

- public_paper.py:CaptureError(kind,retryable,detached sample). Отдельные reasons для capture span,clock regression,symbol mismatch,quote timestamp/future/stale и malformed payload. PublicReader дополнительно сохраняет measured collection_elapsed_ms для диагностики;сам по себе этот diagnostic field не ослабляет/заменяет server-time freshness gates.
- public_session.py:отдельная обработка CaptureError. Только slow span/stale quote допускают discard+bounded retry. Неверные symbol/type/shape,future/regressing time иinvalid price/funding остаются fatal. Отвергнутый public sample,reason,retryable,consecutive_errors,gap flag сохраняются в immutable session_log;в trading inbox он не попадает.
- Не более3последовательных попыток по существующему default,успешный применённый вход сбрасывает счётчик. При превышении120sот последнего применённого наблюдения — FROZEN;cursor/позиции/fees/funding не продвигаются через плохой capture. Пороги10s/120s иretry count не повышались.
- Новые9сценариев test_capture_recovery.py;README с фактическими правилами. Kernel/strategy/accounting/fees/leverage/decision параметры не менялись.

Это изменение технической обработки наблюдений,не разрешение торговать по устаревшим ценам. Partial/UNKNOWN финансовых операций не превращаются в retry-safe;новая логика применяется только к public ценовым входам до simulated decision.

## Версия,exact diff и проверки

Checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio,HEAD3c5c36421110690bb764750faebfde2b840fa161 плюс working-tree fix. Существующие незакоммиченные comparison/docs/старые результаты сохранены. Patch и SHA source/tests — reports/capture_fix_2026-10-18/{code.patch,verification.json};README/этот отчёт и reports/day_retry_2026-10-18 составляют сопутствующие изменения.

**224 scoped tests PASS**,включая9новых:точная классификация,detached diagnostics,slow→fresh без плохих fills,предел3retries,фатальныйwrong symbol,stale/future инепреодолимыйgap. Имеющиеся paper/session/ledger/reconciliation сценарии прошли. Whole-pytest/PM2 не запускались.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/capture_fix_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/capture_fix_2026-10-18/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.capture_fix_2026-10-18.verify
```

Единственный fixed replay итогового кандидата:43200BTCUSDT1m,initial10000,.7/.7/.09,spread2bps,slip1bps,fee.0004,lev7,funding assumed0.

| Метрика | Принятый baseline | Candidate |
|---|---:|---:|
| Net OPEN_MTM |11.06966847132799999999999|11.06966847132799999999999|
| Fees,уже внутри Net |4.2916094086720|4.2916094086720|
| Rebalances |69|69|

Result/trades/equity точно совпали с reports/historical_2026-10-17/BTCUSDT. Это регрессия kernel,не proof исполнения/устойчивости/freshness24h;terminal costs по-прежнему исключены.

## Новый явно разрешённый опыт

User прямо поручил исправить и заново запустить. Создано отдельное окно reports/day_retry_2026-10-18;старое reports/day_forward_2026-10-18 сохраняет FAIL. Generator/harness derived из принятого24hwrapper;до старта сохранены новый frozen.json с source SHA,settings/filters/criteria и новый runtime snapshot. Код основного checkout не подмешивается в процесс.

PID124800/python,старт2026-10-18T10:43:23Z;bounded86400s,ожидаемый конец19октябряоколо12:43Калининград. Первый target funding18октября16:00Z. Критерии24h,≥3distinct funding timestamps,span≥23h,exact replay,no duplicates/pending/orders,integrity/SHA сохранены. State после старта RUNNING,pending0,первые наблюдения применены;PID/time иstderr проверены. Это **не новый PASS**,а выполняющийся prospective опыт. Нет автоматического сброса/продолжения старого frozen состояния.

Монитор funding-paper обновлён на PID/каталог/срок нового опыта,активен каждые5минут,в штатном режиме молчит. При результате/freeze/error уведомит,не перезапустит worker. Финальные24hрезультаты будут проверены отдельно. Исследование не блокирует независимую имплементацию.

Commit/push не выполнялись. Реальные заявки/ключи/переводы/signing не используются. **Fix передан;новое24hокно RUNNING,суточная устойчивость пока не доказана.**
