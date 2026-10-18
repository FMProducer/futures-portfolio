# Wall-clock public-paper funding window — старт 2026-10-17

## Публикация принятого этапа

Создан и отправлен в origin/master [544f36102195e41aaa6486faaa767b1ec5b53a4d](https://github.com/FMProducer/futures-portfolio/commit/544f36102195e41aaa6486faaa767b1ec5b53a4d), `Record frozen paper forward and process-crash qualification`. Runtime не включён; независимый accounting-recheck документ сохранён отдельно.

## Новый этап: RUNNING, ещё не PASS

По поручению User запущено следующее prospective wall-clock исследование: от текущего времени до5минут после ближайшего опубликованного nextFundingTime Binance. Server funding target:2026-10-18T00:00:00Z, то есть18октября02:00 Europe/Kaliningrad. Freeze рассчитывает9805секунд длительности; фактический старт21:22:02Z17октября, ожидаемое завершение около00:05:27Z18октября (02:05по Калининграду). Запуск может закончиться раньше при FROZEN/ошибке.

На передаче процесс151940/python существует, status и DB RUNNING, начальные наблюдения обработаны, pending0. Проверено после запуска; это не утверждение о будущем состоянии процесса. Итоговая verification.json пока отсутствует. Суточная и многодневная устойчивость не доказаны.

## Exact diff и freeze

- Новый reports/funding_forward_2026-10-17/runner.py: prepare/run/status, копирование принятого source в изолированный runtime, bounded Session.run, итоговый export и exact replay.
- frozen.json: HEAD544f361, byte SHA принятого source и runner, полный прежний config, фактические filters и исходное public наблюдение, target timestamp, duration и критерии. Freeze создан до запуска окна; перезапись существующего протокола/автоматический restart запрещены.
- launch.json: PID и фактическое время старта; status.json: PREPARED/RUNNING/PASS/FAIL/FAILED. Status обновляется в начале/конце, а не heartbeat; актуальное продвижение смотрится в readonly DB и проверяется существование PID.
- Локальный .gitignore исключает runtime/code, SQLite и process logs. Этот документ.
- Production rebalance/accounting, торговые параметры и принятые guards не менялись. Новый этап не коммитился; это ещё не результат для приёмки.

Снимок из6файлов (`__init__`,public_session,public_paper,kernel,durable,binance_readonly) byte-identical принятому коду. Worker стартует свежим Python и импортирует rebalance из runtime/code; изменение основного checkout не меняет загруженный эксперимент. Перед запуском/после проверяются snapshot SHA, actual filters сравниваются с freeze. Ключи и .env не читаются; транспорт только public GET. Непроверенная ставка из premiumIndex не проводится как funding: требуется published funding history на точный ранее записанный timestamp.

Preflight подтвердил import path из snapshot, создание отдельной симуляционной DB/Session, отсутствие orders. Это проверка изоляции, не full integration PASS. Новые исторические backtest и whole-pytest не запускались: economics и production code не менялись; в предыдущем принятом этапе были31точечный PASS и process-crash evidence. Здесь они не выдаются за новую проверку funding window.

## Ограничение процесса и остановка

Start-Process -WindowStyle Hidden запустил один bounded worker с redirect stdout/stderr в runtime. Это отдельный процесс исследования, не торговый daemon, не PM2 и не глобальный scheduler. После выполнения duration или FROZEN он экспортирует результат и завершится. Внесения startup/автозапуска, изменения VPN/config или фонового автоматического повторения нет.

При сетевых ошибках действуют принятые3повтора; gap>120s, неизвестный/неподдерживаемый funding, filters/volume и другие ошибки сохраняют freeze. Сон/выключение компьютера могут прервать опыт; старое RUNNING в status.json само по себе не доказывает живой процесс. Нет автоматического освобождения UNKNOWN, сброса позиций или повторного forward на том же окне.

## Завершение и критерии

Worker самостоятельно создаст result/session.json, verification.json и финальный status.json. PASS требует STOPPED/duration complete, хотя бы одного успешно применённого события на целевой funding timestamp, отсутствия pending, точного offline replay всех применённых result/portfolio/revision, no-op дубликатов, пустого orders, SQLite integrity и неизменных snapshot SHA. Funding рассчитывается по виртуальным позициям с опубликованными rate/mark; реальный account income не подтверждается.

Если сессия FROZEN, её сохранённый префикс воспроизводится и итоговые критерии отмечаются FAIL. Если сломан export/verify/worker — FAILED с причиной, не PASS. Искусственно завершённый процесс может не успеть записать итог; требуется чтение DB/PID/логов, а не трактовка отсутствия файла как успеха. Никакие критерии доходности не добавлены; цель5M/1M этим опытом не проверяется.

## Проверка статуса

Из C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.funding_forward_2026-10-17.runner status
Get-Process -Id 151940 | Select-Object Id,ProcessName,StartTime
```

Если PID уже завершился, читать verification.json/status.json и runtime/stderr.log. Не запускать prepare/run повторно поверх этих файлов. Финальная интерпретация evidence и User-приёмка требуют отдельной передачи после завершения.

**Этап выполняется. Финальный PASS не заявлен. Реальных заявок/переводов/signing нет.**
