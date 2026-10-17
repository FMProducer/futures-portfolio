# Замороженный протокол paper forward/fault qualification

Дата: 2026-10-17. Исходный принятый commit: ba2c6e6dcc950e97cd70bb7789dd7c6eb3da0db4. Commit/push выполнены до начала нового этапа. Production-код и экономические настройки этого исследования не меняются.

Машиночитаемый протокол сохранён **до начала forward** в reports/paper_qualification_2026-10-17/frozen.json. SHA256: `91abeae4b6fabd34fc0485d8e9be20c070e9427e6d6163ee50b9b3f678deadb2`. В нём время freeze UTC, HEAD, SHA всех исполняемых модулей и самого run.py, полный config, фактические exchange filters, расписание и критерии.

## Проверки и критерии

1. Public forward:300секунд foreground на текущих public mark/book BTCUSDT. Новая отдельная виртуальная DB. Требуются STOPPED, отсутствие pending inputs, span между крайними наблюдениями≥240000ms, минимум5применённых минут, неизменные source/settings и точный offline replay всех применённых результатов/portfolio/revision. Это техническая проверка данных/состояния, без veto/цели по прибыли.
2. Ускоренные виртуальные24часа:1441наблюдение через1минуту при постоянных синтетических mark100,bid99.9,ask100.1,topqty100; funding0.0001 каждые480минут. Те же frozen config и фактические filters. Требуются3funding-события,1441revision,2entry+3funding receipts, отсутствие pending/order записей, integrity OK; reopen в середине и дубликаты не меняют результат. Данные синтетические; это не сутки на бирже и не бэктест доходности.
3. Реальный abrupt exit отдельного процесса через os._exit: после inbox commit, после portfolio/funding commit до ack, внутри funding transaction перед INSERT events. Ненулевой funding сверяется с неаварийным контролем. После смерти процесса OS-lock должен освобождаться. До незавершённого commit состояние полностью прежнее; после recover результат равен контролю; повторный recover не создаёт cash/fills.
4. Точечные сценарии paper/session: UNKNOWN, gap, invalid data, conflicts, atomic rollback, single-owner lock. Не запускать whole-pytest/PM2/broker операции.

При failed gate сохранить результат и остановить исследование этой версии. Не менять параметры, filters или код ради прохождения того же forward-окна; не повторять окно как независимое. Однократно созданные DB и output не перезаписываются. Статус FAIL/недостаточная длительность остаётся в отчёте.

## Границы

Только public GET и симуляционный капитал, без ключей, реальных заявок/переводов, signing и покупок. Real account Equity и income не подтверждаются. Нет допуска к реальным операциям. 5минут не подтверждают суточную устойчивость; virtual funding и crash injection не заменяют live funding/network-fault evidence. Критерии исследования стратегии/OOS и strategy_admission_profile остаются отдельной работой до реального пилота.

Новые historical backtest/оптимизация не нужны: экономические модули и входы принятого fixed regression не изменяются. Проверяется их идентичность SHA; исторический PASS не выдаётся за новый прогон. На этом этапе проверяется работа принятой версии, а не новый экономический кандидат.

Команды из корня репозитория с reports/audit_2026-10-16/venv/Scripts/python.exe:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run freeze
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run live
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run faults
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run virtual
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run verify
```

Выполненные команды повторно не запускать поверх существующих входов/результатов. После итогового отчёта — STOP для приёмки.
