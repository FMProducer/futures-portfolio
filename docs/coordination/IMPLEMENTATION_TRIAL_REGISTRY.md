# Research trial registry — 2026-10-18

## Результат и scope

User поручил продолжать исследования по draft параллельно24h. Реализован независимый offline инструмент учёта попыток,не изменяющий существующий runner/экономику. HEADe1b48839f8d587dc401d8e43fcec556afb01ab4b;исходный checkout чистый,User ранее закоммитил capture fix. Текущий кандидат — HEAD+новые registry/tests/docs/evidence. Commit/push не выполнялись.

## Exact diff

- Новый rebalance/trial_registry.py:dedicated SQLite,start/finish/report,CLI manifest/outcome JSON,exclusive output,immutable records,idempotency/concurrency.
- Новый rebalance/scenarios/test_trial_registry.py:7проверок invariants/guards.
- RESEARCH_ADMISSION_DRAFT.md дополнен реализованным scope и ограничениями.
- Этот отчёт;reports/trial_registry_2026-10-18 с tests.xml,verify.py,registry_report.json,verification.json и fixed_BTCUSDT. Runtime DB исключена .gitignore.
- Production strategy/kernel/historical/accounting/public session/settings и текущий24h snapshot не менялись. SHA нового кода/tests и точный HEAD записаны в verification.json. Полные новые source files представляют add-file diff;нет скрытого overlay экономического модуля.

## Семантика

start требует identity,code/config/data artifact paths,metadata и явныйmode. Байты каждого файла читаются дляSHA/size;registration timestamp назначается текущим UTC,не принимается отcaller. Исходные bytes не переписываются,source snapshot не копируется реестром. Идентичныйstart возвращает прежнюю запись без новой попытки,конфликт блокируется.

finish допускает COMPLETED/FAILED/ABORTED;нет статусаPASS. COMPLETED требует evidence и повторного совпаденияinput artifactSHA. FAILED/ABORTED требуют reason и могут фиксироваться,даже если исходные файлы изменились. Уже записанный terminal outcome не заменяется. Trials/outcomes/schema marker имеют SQL UPDATE/DELETE guards. Операции сериализованы BEGIN IMMEDIATE,synchronousFULL,foreign keys;соединения явно закрываются.

Отсутствие terminal outcome видно какINCOMPLETE,включая reopening после прерывания. Report всегда выводит все зарегистрированные попытки,счётчики status и occurrences data hash. Неудачные и незавершённые записи не исключаются ради представления победителя. Проверка двух конкурирующих outcome подтверждает,что второй не перезаписывает первый.

Не делается ложных гарантий:PROSPECTIVE — mode declaration,не доказательство запуска зафиксированного source;файл можно временно заменить и вернуть,чего endpoint-SHA не обнаружит. Реестр знает только явно зарегистрированные attempts;автоматическое исполнение/регистрация всех запусков не подключены. Data SHA не обнаруживает semantic overlap различающихсяCSV и не доказывает независимостьOOS. SQLite triggers не являются hash-chain/независимой tamper-evidence из соглашения. Admission/OOS status не повышаются автоматически.

## Проверки

**7targeted tests PASS**:reopen/idempotency/incomplete,видимостьfailed/aborted/retrospective и повторногоdata hash,изменённыеinputs/conflicting registration,transaction rollback иimmutable записи,racing outcomes,foreign DB byte preservation,requiredmode/artifacts/evidence/reasons/status guards. Whole-pytest/PM2 не запускались.

Пример заполнил2фактические записи,обеRETROSPECTIVE:текущий fixed replay(COMPLETED) и исходный неуспешный24h опыт(FAILED). Использованы только уже открытыеpublic/historical evidence,без изменения прежнихDB/исходов. Историческийfailed trial связан с его archived source snapshot;config artifact — полный frozen manifest,data artifact — captured input/result export. Registration выполнена сейчас,не представлена как freeze перед прошлым запуском. Counts COMPLETED1/FAILED1,total2;integrityOK. Это демонстрацияархивации,не новая оценкастарогоPASS/FAIL.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_trial_registry.py -q --confcutdir=rebalance --junitxml=reports/trial_registry_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.historical --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/trial_registry_2026-10-18/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.trial_registry_2026-10-18.verify
```

CLI start manifest:JSON files(role→path),metadata,mode. CLI finish outcome:JSON status,reason,evidence(role→path). Всеrelative paths разрешаются из текущегоcwd;для повторяемости запускать из repo root. Report output создаётся эксклюзивно и не перезаписывается. verify.py использует новую dedicated DB и не допускает повторную запись поверх примера.

## Единственный fixed backtest итогового кандидата

| Метрика | Принятый baseline | Candidate |
|---|---:|---:|
| BTCUSDT1m rows |43200|43200|
| Initial equity |10000|10000|
| Net OPEN_MTM |11.06966847132799999999999|11.06966847132799999999999|
| Fees уже внутриNet |4.2916094086720|4.2916094086720|
| Rebalances |69|69|
| Funding assumption |0|0|

Result/trades/equity точно совпали с reports/historical_2026-10-17/BTCUSDT. Параметры.7/.7/.09,spread2bps,slip1bps,fee.0004,lev7. Manifest содержитSHAinputs/kernel/historical. Это использованныйSeptember2026,неOOS;OPEN_MTM безterminal closing costs/реалистичныхpartial/liquidation.

## Передача

24h PID124800 во время итоговой проверкиRUNNING,pending0,source snapshot не затронут. Реальных заявок/ключей/переводов/signing нет. Цикл/PF/terminal closeout иlive admissionprofile не определеныэтиминструментом. Следующая независимаяработа — интеграциярегистрации с исследовательскимrunnerпослесогласованияинтерфейса;она не требуетожидания24h.

**STOP для приёмки реестра.**
