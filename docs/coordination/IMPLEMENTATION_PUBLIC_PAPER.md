# Public REST paper — 2026-10-17

## Результат и границы

Принятый CSV paper-этап закоммичен по поручению User: 452b765c811bc57d04250f94267e58acb9fb245e. Push не выполнялся. Затем реализован отдельный bounded public REST paper-режим: текущие mark/bid/ask Binance, отдельный виртуальный капитал, атомарные симуляционные fills. Приватные ключи, баланс реального счёта и торговые endpoint не используются.

Во время работы HEAD параллельно изменился на 075bae175fb9958dc67c9d9c263425b2c15e5a36 (`docs: readme`), куда вошли public_paper.py и первоначальные evidence. Этот коммит не создавался данным агентом и сохранён. Итоговый кандидат — этот HEAD плюс текущий diff: явное закрытие SQLite-соединения проверки чужой БД, этот отчёт, verify.py и verified_* evidence. Непосредственные SHA кода записаны в verified_probe/binding.json. Независимый файл IMPLEMENTATION_ACCOUNTING_RECHECK_20261017.md не изменялся.

## Exact diff относительно принятого 452b765

- rebalance/public_paper.py: PublicReader с фиксированными public GET, сбор ограниченного ценового наблюдения, валидатор свежести, Paper с привязанной к входам SQLite, CLI 1–10 наблюдений.
- rebalance/scenarios/test_public_paper.py: 14 сценариев входов, возобновления, повтора, конфликтов, funding/gap, фильтров, объёма и rollback.
- rebalance/README.md: команда и ограничения public-paper.
- reports/public_paper_2026-10-17: public observations/results/bindings, тестовые XML, fixed regression, verify.py и verification.json. Runtime исключён локальным .gitignore.
- Этот документ. Учётный ledger, kernel/durable/historical, торговые параметры и старый dashboard/executor не менялись.

Патч кода относительно принятого этапа: reports/public_paper_2026-10-17/code.patch. Исторический ввод и его SHA, настройки и SHA kernel/historical — verified_BTCUSDT/manifest.json. В verified_probe/binding.json зафиксированы public-paper/kernel/durable SHA, символ, исследовательский config и фактические exchange filters.

## Поведение

Собираются serverTime → premiumIndex → bookTicker → serverTime. Интервал сбора и возраст каждого ценового источника ограничены 10 секундами. Это техническая проверка давности; REST-ответы не объявляются единым атомарным снимком биржи. Equity вычисляется исключительно из симуляционных wallet/позиций, а не из свежего E или публичной цены реального счёта.

Один применённый шаг решения в UTC-минуту. Вход и последующие решения используют наблюдённый mark и book, а не прошлый candle close/current open. Это отдельный исследовательский режим источников, не доказательство совпадения его доходности с CSV replay. Наблюдения в той же минуте сохраняются без повторных fills. Пропущенные экстремумы не восстанавливаются выдуманными сделками; gap >120 секунд блокирует дальнейшую обработку.

Используется прежний kernel, фактический observed spread плюс заданный adverse slippage; synthetic spread_bps здесь не добавляется. Фиксированные fee/leverage из research config не объявляются реальными комиссиями/маржинальными условиями аккаунта. До каждого simulated fill проверяются LOT_SIZE, MARKET_LOT_SIZE, MIN_NOTIONAL и доступный объём лучшей стороны book. Превышение приводит к rollback всего шага; увеличение размера или фиктивное частичное исполнение не производится.

BEGIN IMMEDIATE фиксирует wallet, позиции, extrema, fee receipts, время, funding horizon и immutable event вместе. Идентичный event повторно возвращает результат; изменение payload при том же ID блокируется. Ошибка второй ноги или записи журнала откатывает первую ногу и cursor. Конфигурация/код/символ/filters закреплены; чужие DB не импортируются. Orders остаётся пустым. Все simulated fills локальны и не обещают атомарности ног на бирже.

При пересечении ранее записанного nextFundingTime с открытой симуляционной позицией — funding UNKNOWN и блокировка, а не нулевое начисление. Механизм сверки funding и восстановления после долгого gap в этот этап не включён; автоматического reset/сброса позиции нет.

## Проверки

Итоговый кандидат: **176 тестов, errors=0, failures=0**, verified_tests.xml. Из них 14 новых, остальные 162 ранее существовавшие. whole-pytest/PM2/broker-операции не запускались.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/public_paper_2026-10-17/verified_tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.public_paper --symbol BTCUSDT --config rebalance/historical_example.json --database reports/public_paper_2026-10-17/runtime/verified.sqlite --output reports/public_paper_2026-10-17/verified_probe --samples 2
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/public_paper_2026-10-17/verified_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.public_paper_2026-10-17.verify
```

Для новых CLI-прогонов выбирать новые output и, для отдельного эксперимента, новую DB. verify.py воспроизводит сохранённые публичные наблюдения офлайн в временной БД и проверяет повтор после reopen, отсутствие дополнительных fills, пустой orders, SQLite integrity, SHA кандидата и точное совпадение result/trades/equity контрольного replay.

Первоначальные probe/final_probe/tests.xml/fixed_BTCUSDT относятся к предварительной версии, включённой в параллельный коммит. При офлайн-проверке выявлена удерживаемая Windows SQLite handle при reopen; добавлено явное closing. Поэтому выполнены новые проверки итоговой версии в verified_*. Первые неуспешные запуски verify.py завершились ошибкой удаления временной БД и не считаются PASS. Новая проверка не меняет экономику.

| Метрика fixed BTCUSDT | Принятый baseline | Итоговый кандидат |
|---|---:|---:|
| Свечей, 1 мин | 43200 | 43200 |
| Капитал | 10000 USDT | 10000 USDT |
| Net OPEN_MTM | 11.06966847132799999999999 | 11.06966847132799999999999 |
| Комиссии, уже включены в Net | 4.2916094086720 | 4.2916094086720 |
| Ребалансировок | 69 | 69 |
| Funding assumption | 0 | 0 |

Baseline — reports/historical_2026-10-17/BTCUSDT. Параметры .7/.7/.09, spread2bps, slippage1bps, fee.0004, leverage7. Терминальные позиции открыты, стоимость закрытия не включена; нет стаканной глубины, latency/partial/liquidation модели. Числовое совпадение проверяет отсутствие регрессии общего ядра; это не backtest публичного режима и не подтверждение цели 5M USDT.

## Оставшиеся этапы

Длительный непрерывный feed с fault/reconnect контролем, reconciliation funding, проверка реалистичного исполнения и независимого периода, утверждённый User admission profile. Старые dashboards не подключались, их ранее найденные дефекты остаются открытыми. Blockchain/signing/покупки не реализовывались. Сеть и контракт токена ещё не выбраны. Реального дохода этот paper-этап не создаёт.

Документация endpoint: [Binance official USD-M market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).

План в Obsidian обновлён по новому решению: ≥5M USDT чистого капитала проекта и отдельно ≥1M USDT свободного бюджета развития, принадлежащего User/его организации. Личное накопление агентом и неограниченное самостоятельное распоряжение средствами не принимаются. «Около 25%» не заменяет прогрессивную формулу договора и не приравнивает капитал к прибыли.

**STOP — результат передан на приёмку. Допуск к реальной торговле не выдан.**
