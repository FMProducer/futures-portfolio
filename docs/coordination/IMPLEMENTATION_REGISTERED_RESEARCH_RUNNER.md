# Registered offline research runner — 2026-10-18

## Принятый этап и новый scope

Принятый реестр закоммичен и отправлен в origin/master: [788a2286d41f1622fca1249a15ebc36bdf6d6339](https://github.com/FMProducer/futures-portfolio/commit/788a2286d41f1622fca1249a15ebc36bdf6d6339). Включены только принятые registry/source/tests/docs/evidence,runtime исключён.

Следующий этап — новый offline entry point,автоматически регистрирующий запускаемый comparison. Checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio;source HEAD788a228+новые файлы. Working tree перед изменениями чистый. Новый этап не коммитился.24h public-paper PID124800/snapshot не затронут,во время итоговой проверки RUNNING,pending0.

## Exact diff

- Новый rebalance/research_runner.py:exclusive output,snapshot source/inputs,PROSPECTIVE Registry.start,изолированный child,export,Registry.finish и полный registry report.
- Новый rebalance/scenarios/test_research_runner.py:6integration сценариев с реальным дочернимPython.
- Draft дополнен фактическим статусом нового entry point;этот отчёт.
- reports/research_runner_2026-10-18:tests.xml,verify.py,verification.json,один fixed registered study с frozen source/inputs/result/evidence/registration иexport registry. Runtime DB исключёнGit.
- Существующие registry/comparison/historical/kernel/accounting/public modules и экономические параметры не менялись. SourceSHA иsourceHEAD в verification/execution manifest;полные frozen6sourcefiles иbyteSHA сохранены.

## Порядок исполнения

1. Эксклюзивно создаётся новая output directory;это не режим resume. Копируются6sourcefiles иполные data/config/optional funding bytes. Связь с исходными paths сохраняется какmetadata.
2. Реестр принимает start с modePROSPECTIVE,sourceHEADеслидоступен иSHA всех frozen artifact paths. Запись обязана существовать до вычисления. Конфликтtrial id/входов/output блокирует запуск.
3. Child Python запускается сcwd/PYTHONPATH из frozen/code;он проверяет собственный __file__,создаёт одноразовыйworker.claim,проверяетINCOMPLETE registration/output иSHA. Funding mode должен совпадать с наличием CSV artifact;ноль не выбирается неявно.
4. Только после проверок читаются расчётные rows/settings ивызывается прежний compare. После export8JSON evidence реестр получаетCOMPLETED сSHA. Числовая прибыль/PF/admission не присваиваются.
5. Ошибка validation/calculation/export фиксируетfailure.json иFAILED. Дочерний процесс,завершившийсябезoutcome,получаетFAILED отsupervisor. Ctrl+C завершает только принадлежащийемуchild ификсируетABORTED. Еслисамsupervisorаварийноисчез,INCOMPLETE остаётсявидимым;автоперезапускане предусмотрено.

Другой output с тем жеidentity меняет snapshot paths/metadata иконфликтует с записью;тот жеoutput вообще не создаётся повторно. Отдельныйworker.claim предотвращает повторнуюworker-обработкуэтихфайлов. Контроль являетсяоперационным,не security boundary длявладельцафайлов/SQLite.

## Проверки

**13targeted tests PASS:**6новых+7registry. Проверены правильныйsnapshot import/registration,failed invalidCSV безрезультата,повторныйoutput/id доchild,изменениеoriginalfile послеregistration безвлияниянаcopy,изменениеsnapshot доchild сFAILED дорасчёта,явныйfunding choice. Тестподменыизначальнозадевалтакжепроцессgitrev-parse доregistration;инъекцияограниченапроцессомPython worker. Это исправлениетестовойточкиинъекции,нетподгонкиэкономики.

ПервоначальныйfixedBTC запуск выполнен один раз через новый entry point:

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_research_runner.py rebalance/scenarios/test_trial_registry.py -q --confcutdir=rebalance --junitxml=reports/research_runner_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.research_runner run --database reports/research_runner_2026-10-18/runtime/trials.sqlite --id fixed-BTCUSDT-current --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --output reports/research_runner_2026-10-18/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.research_runner_2026-10-18.verify
```

Для следующего исследования выбирать новыйid/output;существующийstudy не перезапускать. verify.py только проверяетevidence/hashes,непересчитываетbacktest. Whole-pytest/PM2/optimisation не запускались.

## Fixed result и регистрация

| Метрика | Принятый baseline | Candidate через registered runner |
|---|---:|---:|
| BTCUSDT1m candles |43200|43200|
| Initial equity |10000|10000|
| Strategy Net OPEN_MTM |11.06966847132799999999999|11.06966847132799999999999|
| Strategy fees,внутриNet |4.2916094086720|4.2916094086720|
| Rebalances |69|69|
| Hold Net OPEN_MTM |−3.0163046400000|−3.0163046400000|

Strategy result/trades/equity точно совпали с historical_2026-10-17/BTCUSDT;hold result/trades/equity — с comparison_2026-10-18. Registry modePROSPECTIVE,outcomeCOMPLETED;artifact/evidence SHA совпали,SQLite integrityok,execution __file__ внутриsnapshot. SHAкоммитированнойосновынезаменяетSHAworking candidate:онсохранёнполностьювsnapshot.

Исследовательскиеsettings.7/.7/.09,spread2bps,slip1bps,fee.0004,lev7,funding assumed0. September2026 ранее использован;неOOS. OPEN_MTM безcostsзакрытия,полнойdepth/latency/partial/liquidation. НикакогоновогодопускаилиростаNetне заявлено.

## Ограничения и передача

Автоматическаяregistration относится только этомуentry point. Legacy/manual historical/comparison иработающийpaper ещёнеподключены;ихотсутствиевреестренедоказываетотсутствиепопыток. Snapshotзащищаетотобычныхизмененийоригиналов,нонеотнамеренноговмешательствавладельцаruntime/SQLite/интерпретатора. EndpointSHAнеявляетсянезависимойtamper-evidence. Registry/prospective snapshotне заменяютпредварительноеутверждениеadmission criteria иOOSдоступа.

Реальныезаявки/переводы/ключи/signingнет. PF/cycle/terminal closeout/liveprofileнепереопределены. **STOP дляприёмкиинтеграции;суточныйопытпродолжаетсяотдельно.**
