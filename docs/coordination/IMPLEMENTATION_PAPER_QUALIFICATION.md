# Paper forward/fault qualification — 2026-10-17

## Принятый этап и публикация

User принял supervised public-paper этап и поручил commit/push. Создан и отправлен в origin/master коммит [ba2c6e6dcc950e97cd70bb7789dd7c6eb3da0db4](https://github.com/FMProducer/futures-portfolio/commit/ba2c6e6dcc950e97cd70bb7789dd7c6eb3da0db4), `Add supervised public paper sessions and funding reconciliation`. Включены принятые source/tests/docs/evidence public-paper и public-session, runtime исключён. Независимый IMPLEMENTATION_ACCOUNTING_RECHECK_20261017.md оставлен нетронутым и не добавлен.

Обычный push завершился локальной schannel SEC_E_NO_CREDENTIALS в sandbox; повтор разрешённой команды вне sandbox завершился успешно (`075bae1..ba2c6e6 master -> master`). Это не ошибка удалённой ветки и не повод менять global git config.

## Новый scope

Проверка принятого неизменённого кода с заранее замороженным протоколом:5минут public forward, ускоренные виртуальные24часа, abrupt process exits в трёх точках записи. Это qualification состояния/проводок, не оптимизация и не новый экономический кандидат. Ключи, private account, real orders, выплаты и приобретение активов не используются.

Сначала создан run.py, затем freeze сохранил HEAD, source/config/harness SHA, фактические filters и критерии до запуска forward. Протокол: PAPER_FORWARD_PROTOCOL_20261017.md и reports/paper_qualification_2026-10-17/frozen.json, SHA256 `91abeae4b6fabd34fc0485d8e9be20c070e9427e6d6163ee50b9b3f678deadb2`. Источники после freeze не менялись; повтор неудачного окна и перенастройка запрещены.

## Exact diff и идентификация

- Новые docs/coordination/PAPER_FORWARD_PROTOCOL_20261017.md и этот отчёт.
- Новый reports/paper_qualification_2026-10-17/run.py: freeze, реальные child-process crashes, синтетические24часа, public forward и exact offline verification. Это исследовательский harness, production-модули не изменяет.
- frozen.json, crash_results.json, virtual_results.json, fault_tests.xml, live/session.json и verification.json — evidence отдельного исследования. Runtime/lock исключены локальным .gitignore. Сам harness закреплён SHA до сбора входов, его полный source является exact add-file diff.
- Рабочее дерево может содержать независимый accounting-recheck документ; он не является частью этой передачи.

Production diff `git diff ba2c6e6 -- rebalance accounting` пуст. Parameters/filters не менялись после freeze. Candidate identity — опубликованный ba2c6e6 плюс добавленные документы/harness/evidence, не новый production release. Этот новый этап отдельно не коммитился и не отправлялся.

## Завершённые проверки отказов

31точечный тест paper/session PASS, fault_tests.xml. Весь pytest не запускался, PM2/broker-вызовов нет.

Реальные дочерние процессы на той же версии Python и source завершались через os._exit без finally/cleanup:

| Точка abrupt exit | Код выхода | Результат после recover |
|---|---:|---|
| После durable inbox, до применения funding/fills |71|Точно как неаварийный контроль|
| После portfolio/funding commit, до inbox ack |72|Кешированный результат, без повторного cash|
| Внутри funding transaction, перед INSERT events |73|Полный rollback, затем точное применение|

Во всех3случаях process exit освободил OS-lock. Состояние до незавершённого commit осталось прежним,1pending input восстановлен, после повторного recover0дополнительных проводок. Конечная revision2, funding0.0005500USDT совпали с контролем. Эта сумма синтетическая; положительный funding не объявляется реальным доходом. В transaction-case тестовый SQLite trigger удалялся после проверки rollback для устранения внесённого fault injection, а не для обхода production guard. Orders пуст, integrity OK.

## Ускоренные виртуальные сутки

PASS:1441наблюдение через1виртуальную минуту, reopen в середине, повторные входы не меняют revision/state. Три scheduled funding события за24виртуальных часа. Revision1441,5receipts=2entry+3funding,0pending,orders0,SQLite integrity OK. Синтетические постоянные mark100,bid99.9,ask100.1; rate0.0001каждые8часов. Config и filters совпадают с freeze. Funding суммарно0.0016500,fees2.002997780300USDT; это модельные значения, не экономический forward-результат.

Эти24часа воспроизведены ускоренно на компьютере; wall-clock live24h НЕ проверены. Constant-price сценарий проверяет накопление/повторы/funding, а не реалистичное торговое исполнение или устойчивость стратегии на изменяющемся рынке.

## Команды

Из C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run freeze
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run live
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run faults
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run virtual
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_public_session.py rebalance/scenarios/test_public_paper.py -q --confcutdir=rebalance --junitxml=reports/paper_qualification_2026-10-17/fault_tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.paper_qualification_2026-10-17.run verify
```

Повтор поверх уже созданных freeze/DB/output блокируется. Критерии установлены до forward и не менялись по результатам. Инженерные gate не заменяют User-approved финансовые admission criteria.

## Исторический backtest

Новый исторический прогон не выполнялся: production-код и экономические входы не менялись; это верификация принятой версии и документация. Принятый previous-stage regression:43200BTCUSDT1m,Net11.06966847132799999999999USDT,fees4.2916094086720,69rebalances,funding assumed0. Это историческое свидетельство ba2c6e6, не новый PASS текущего qualification. Current SHA подтверждены freeze; accepted baseline/threshold не переопределялись. Терминальный backtest OPEN_MTM, издержки закрытия и реалистичные partial/liquidation не покрыты.

## Ограничения

Нет24h/multiday wall-clock live evidence, подтверждённого real-account funding, execution latency/partial/отмен/ликвидации, strategy_admission_profile и независимого OOS. Production error guards намеренно не менялись для прохождения окна. При провале gate требуется отдельное расследование и новое явно отделённое исследование, а не повторное использование этого окна как независимого.

## Итог public forward и приёмка

**PASS**, verification.json, failed_gates пуст. Foreground duration300sec завершён STOPPED / duration complete. 45применённых/сохранённых наблюдений,299750ms между крайними,6минут решений,2simulated entry fills. Pending0. Live read retries0,live funding events0: отсутствие этих событий явно отделено от успешных synthetic/process-crash проверок.

Все45наблюдений воспроизведены офлайн в новой БД с идентичными result/portfolio/revision. Повтор каждого входа не менял состояние. Orders0,integrity OK. HEAD и source/config/harness SHA по-прежнему совпали с предварительным freeze. На этом же окне код/параметры/критерии не менялись и повторный forward не запускался.

| Предварительно закреплённая проверка | Факт | Итог |
|---|---|---|
| STOPPED, span≥240000ms, ≥5минут решений |STOPPED,299750ms,6минут|PASS|
| Exact offline replay, no duplicates, no orders |45наблюдений, точное совпадение, orders0|PASS|
| Виртуальные сутки,3funding,1441revision |1441/3/1441,5receipts|PASS|
|3process crash windows |Коды71/72/73; восстановление равно контролю|PASS|
| Freeze источников и критериев |SHA совпали, source diff пуст|PASS|

Это предварительная bounded qualification, а не полная длительная forward-валидация. Дальше требуется отдельно принимаемая wall-clock сессия, охватывающая funding, и затем многодневное наблюдение/замороженная исследовательская спецификация. Финансовый допуск, изменение плеча и масштабирование этим PASS не разрешены. Цели5M/1M плана не проверялись по доходности.

Фоновый процесс после исследования не оставлен. План и протокол Obsidian обновлены. **STOP — новый результат передан на приёмку; commit/push нового исследования ожидают отдельного поручения.**
