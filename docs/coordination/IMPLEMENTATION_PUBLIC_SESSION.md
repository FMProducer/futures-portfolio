# Supervised public paper session — 2026-10-17

## Основа и результат

Задание User: продолжать по плану. Выполнен следующий инженерный шаг — supervised foreground REST paper-сессия с повторным чтением, восстановлением сохранённых входов и сверкой simulated funding. Реального исполнения, приватных ключей, подписания, переводов или покупок нет. Это не автономный суточный сервис и не доказательство достижимости финансовой цели.

Checkout: C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio. Исходный HEAD 075bae175fb9958dc67c9d9c263425b2c15e5a36. На входе уже были незакоммиченные public-paper closing fix, отчёт и verified evidence предыдущего этапа; они сохранены. Независимый IMPLEMENTATION_ACCOUNTING_RECHECK_20261017.md не менялся. Commit/push в этом запросе не выполнялись. Итоговый кандидат — HEAD плюс working-tree diff; SHA источников и exact patch приведены в reports/public_session_2026-10-17/candidate.json и code.patch.

## Exact diff и причины

- Новый rebalance/public_session.py: SessionReader для точного public funding timestamp; foreground loop; OS-lock одной DB; durable captured inbox, immutable log и статус; повтор после сбоя между portfolio commit и inbox acknowledgement; ограниченные сетевые повторы; FROZEN без автоматического reset; согласованный экспорт.
- rebalance/public_paper.py: optional funding evidence в step. Проверка одного точного Regular BTCUSDT события, расчёт cash из pre-event симуляционных позиций, rate и funding mark; атомарное применение funding+fills+cursor+horizon. Без evidence прежняя блокировка UNKNOWN сохраняется. Идентичность входа включает funding evidence. Entry определяется по initial_units, чтобы funding receipt не подавил первоначальный вход. Gap проверяется до попытки начисления.
- Новый rebalance/scenarios/test_public_session.py:17 сценариев с parametrization. В прежнем test_public_paper изменён только timestamp funding-случая с680000 вместо900000: теперь он проверяет UNKNOWN внутри разрешённого gap, отдельно от уже проверяемого long-gap блока.
- rebalance/README.md: команда, правила продолжения и ограничения.
- reports/public_session_2026-10-17: текущие public observations, bindings, fixed replay, тесты, offline verification, exact patch/provenance. Runtime/lock-файлы исключены локальным .gitignore.
- Этот отчёт. Торговые параметры, kernel, durable Store, historical replay, ledger и старые dashboards/executor не менялись.

Code binding public-paper изменился. Старые симуляционные DB не мигрируются молча и не обнуляются: новая версия запускается как отдельный эксперимент с новой DB. Идентичные код/config/filters закреплены для последующих restart.

## Экономика и восстановление

Один шаг решения в наблюдаемую UTC-минуту, actual public mark/bid/ask, прежние fee/leverage/slippage assumptions. Симуляционный капитал берётся из config, а не кошелька реального аккаунта. Доступность top quantity не гарантирует исполнение. Числовые пороги торговой политики не менялись.

Порядок: валидировать capture → при необходимости получить funding → заново собрать цены → сохранить inbox → атомарно применить Paper.step → записать acknowledgement. Если процесс остановился до step, pending input применяется при restart; если после commit до acknowledgement — event cache возвращает результат без новых fills. Ошибка записи funding/fills/events откатывает cash, позиции и funding horizon вместе. Inbox payload и завершённый результат неизменяемы. Журнал не разрешает подмену входов.

Funding поддержан только для BTCUSDT и одного ожидаемого timestamp из ранее сохранённого nextFundingTime. Запрос startTime=endTime=due. Пустая история не считается нулевой ставкой; неоднозначные, чужие, Special либо неконечные данные не применяются. Отсутствующий optional rateType трактуется как Regular в этом ограниченном BTC-режиме. Расчёт: (SHORT_qty−LONG_qty) × published_mark × published_rate. Это моделируемая денежная проводка, не подтверждённый income реального счёта. Тест не требует нулевого net inventory: отдельно проверено ненулевое начисление.

Сетевые ReadError повторяются с интервалом5s, максимум3 подряд по умолчанию; успешное чтение снимает счётчик. Незавершённый read failure к окончанию сессии сохраняет FROZEN. Невалидные данные, long gap>120s, неисполняемые filters/volume и иные ошибки замораживают сессию. Нет восстановления выдуманных экстремумов, автоматического сброса позиций или продолжения через неизвестный gap. Короткое отключение возобновляет REST-чтение; WebSocket reconnect/sequence continuity здесь не реализованы.

CLI ограничен duration1..86400sec, default60sec, foreground. Ctrl+C оставляет состояние; после restart требуется прежний code/config и допустимый gap. OS-lock снимается при смерти процесса и не требует удаления «устаревшего» файла. Выход по duration не закрывает виртуальные позиции и не отправляет real orders. Дальнейший процесс после этой передачи не оставлен работающим.

## Проверки итогового кандидата

**193 PASS**, tests.xml; 17 новых сценариев. Проверены короткая ошибка чтения, durable freeze, reopen/pending replay до и после commit, конфликты inbox, OS-lock, ненулевой funding, duplicate, несовпадение identity/type/rate/mark, UNKNOWN и rollback. Первоначальная точечная проверка обнаружила неверное ожидание теста: исходные LONG/SHORT количества отличаются из-за bid/ask/fees; ожидаемый funding исправлен на формулу по полному pre-state, код не подгонялся под равные количества.

Публичный CLI запущен с duration70sec и завершился STOPPED / duration complete. Сохранено11наблюдений, между крайними67627ms, применены2минуты решения,2simulated fills. Сетевых ошибок и пересечений funding в этом реальном коротком окне не было. Поэтому повторы сети и funding подтверждены фиксированными сценариями, а не выданы за live-fault proof.

verify.py воспроизвёл все11записанных public inputs офлайн с reopen и повторами. Portfolio/results/revision полностью совпали; дополнительных fills0; orders пуст; SQLite integrity OK; SHA текущего кода совпали с capture. Первый вызов verify до завершения foreground-процесса не нашёл ещё не экспортированный session.json и не был PASS; успешная проверка выполнена после завершения процесса.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios accounting/scenarios -q --confcutdir=rebalance --junitxml=reports/public_session_2026-10-17/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.public_session --config rebalance/historical_example.json --database reports/public_session_2026-10-17/runtime/paper.sqlite --output reports/public_session_2026-10-17/live --duration 70
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/public_session_2026-10-17/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.public_session_2026-10-17.verify
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.public_session_2026-10-17.provenance
```

Новые CLI-запуски требуют новых output, новый эксперимент — новой DB. Для продолжения того же эксперимента DB сохраняется. whole-pytest, PM2, broker-операции и оптимизация не выполнялись.

## Единственный fixed backtest итогового кандидата

| Метрика | Baseline | Candidate |
|---|---:|---:|
| BTCUSDT 1m candles | 43200 | 43200 |
| Initial virtual equity, USDT |10000|10000|
| Net OPEN_MTM, USDT |11.06966847132799999999999|11.06966847132799999999999|
| Fees, уже внутри Net |4.2916094086720|4.2916094086720|
| Rebalances |69|69|
| Funding assumption |0|0|

Baseline: reports/historical_2026-10-17/BTCUSDT. Result/trades/equity совпали точно. Входные SHA и параметры — fixed_BTCUSDT/manifest.json; .7/.7/.09,spread2bps,slippage1bps,fee.0004,leverage7. Это контроль общего ядра, не экономическая валидация нового публичного режима. Терминальный портфель открыт, комиссии закрытия не включены; CSV модель не отражает latency/partial/depth/maintenance/liquidation.

## Что ещё не доказано

Суточный/многодневный forward прогон, публикация funding и реальные сетевые отказы в живом потоке, все варианты изменения расписания funding, реалистичная очередь/partial/отмена/ликвидация, утверждённый admission profile и независимый OOS. Максимальная CLI duration не означает, что такая длительность проверена. Непрерывный WebSocket feed и его последовательность — будущая работа. Старые dashboards не подключены. Допуска к реальным операциям нет.

Следующий предмет приёмки — этот supervised paper-этап; после приёмки потребуется отдельный длительный forward/fault прогон и замороженная спецификация исследовательского допуска. Финансовые цели плана не являются обещанием доходности.

Источник схемы: [Binance USD-M market data: funding history](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#get-funding-rate-history).

**STOP — кандидат и результаты переданы Пользователю на приёмку.**
