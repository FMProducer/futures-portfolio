# Portfolio flat-to-flat cycles and PF — 2026-10-18

## Основание и scope

User утвердил цикл всего портфеля отflatдоflat сfees/funding, затем четыре правилаPF: ratio положительных/отрицательных Net, включение synthetic terminal вresearch с отдельной отметкой, null безциклов/убытков и0при однихубытках, отсутствие допуска при открытомinventory. Пороги fullPF>1.5/OOSPF>1.2 не менялись. Это не разрешение live/оптимизации/периодического принудительного закрытия.

Checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio, HEAD4714f6e. Предыдущие принятые, но незакоммиченные terminal verifier/docs/evidence сохранены. Commit/push не выполнялись. CHECK: отдельный модуль/конфиг не нужен, использован существующий comparison и Portfolio.fill/cash_event. Callers compare: CLI comparison, registered runner worker, тесты. Callers verifier: CLI/tests/report scripts. Конфиги/runner/kernel/сигналы не изменены.

## Exact diff

- rebalance/comparison.py: cycles восстанавливает только ledger по рассчитанным trades/funding, без вызова next_action. Для fill используется исходный candle open как mark, для terminal последняяclose; это сохраняет funded-margin проверку Portfolio.fill. Funding сtimestamp<=fill начисляется доfill по прежней последовательности, остальной внутриокна — доterminal. Funding внеокна/неупорядоченный блокируется.
- Началоэпизода — wallet перед первым открывающим fill изflat; завершение — обе legs units0. Net=wallet_end−wallet_start, fees/funding дельты выводятся отдельно и не вычитаются повторно. Partial REDUCE безflat не создаёт цикл. Вход/выходдвухног — одинпортфельныйэпизод. Synthetic episode маркируется отдельно.
- Восстановленные Equity/fees/funding/числособытий/qty обязаны совпасть сopen result; приterminal также проверяютсяflat иwallet. Внешниепотоки не поддержаны, входного параметра для них нет. Unknown/pending не моделируются offlinefills; функция не используетсядляliveконтроля.
- summary.cycles содержит candidate/hold: episodes, gross_profit/gross_loss, PF/status, natural_simulated/synthetic/zero/open counters, real_quarantine_cycles=0, terminalEquity/Net иfalseadmissionflags. Верхнийprofit_factor — PFcandidate, cycle_definition=PORTFOLIO_FLAT_TO_FLAT.
- Status: OPEN_PORTFOLIO/null даже при закрытыхпобедителях; NO_CYCLES/null; ZERO_ONLY/null; NO_LOSS/null; NO_PROFIT/0; DEFINED сratio при обеихсторонахPnL. Нулевыеэпизодыотдельно,PFникакнеприсваиваетPASS/admission.
- rebalance/research_verify.py: дляновойdefinition восстанавливаетcycleledger и проверяетполныйreport/PF; старыеNOT_APPROVEDархивы читаются безновыхполей. Это тотжеproductioncalculator, проверкаconsistency, не независимаявалидацияэкономическойметодики. Стратегия/rebalanceсигналы приverifierнепересчитываются.
- test_cycles.py:9новыхсценариев. test_research_verify.py:3дополнительныхподмены PF/netэпизода/realcyclecounterприобновлённыхSHA. test_closeout.py отделяетновыеcycle/PFfieldsотпроверкинеизменностиOPEN_MTM.

Полный working candidate comparison/historical/kernel/runner/closeout/registry сохранён в frozen/code registered study; входы в frozen/inputs, SHA в registry/execution manifest. ДополнительныеSHA source/tests — verification.json. Точные тесты текущего этапа сохранены в source/ рядом сevidence.

## Проверки

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_cycles.py rebalance/scenarios/test_comparison.py rebalance/scenarios/test_closeout.py rebalance/scenarios/test_research_verify.py rebalance/scenarios/test_research_runner.py -q --confcutdir=rebalance --junitxml=reports/cycles_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.research_runner run --database reports/cycles_2026-10-18/runtime/trials.sqlite --id fixed-BTCUSDT-portfolio-cycles --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --terminal-closeout --output reports/cycles_2026-10-18/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.cycles_2026-10-18.verify
```

Финальный candidate:67testsPASS(9cycles+10comparison+17closeout+25verifier+6runner). Проверены mixedwinner/loserPF, всеособыеPFcases, открытыйостаток, fundingдовхода/послевхода, feesодинраз, двеterminalногиодинцикл, дроблениеREDUCEбезувеличениясчётчика, zerocycle, отсутствиеadmission иизменениявходов. Initial tests:63PASS/1FAIL — прежнийcloseoutтестошибочнотребовалравенстваPFдозакрытияипосле; теперьсравниваетOPEN_MTMотдельноотутверждённойновойметрики. Initial XML сохранён tests_initial.xml. Послеисправлениятеста и3новыхfaultсценариев выполненодинфинальныйtargetedнабор. Whole-pytest/PM2/оптимизациянезапускались.

Одинfixedbacktest завершёнCOMPLETED изprospective frozenchild; noextra baseline run. verify.py только сверяет готовыеevidence. Старыеverificationфайлынепереписаны. Gitdiff--checkбезошибок.

## Фактические результаты

Входы:43200BTCUSDT1m September2026, dataSHA6b482fc0d05691cda897515d6cdb81d878b8fd3d40c3d46a2e2a88e5fc99284a;capital10000, .7/.7/.09,spread2bps/slip1bps/fee.0004/lev7;fundingASSUMED_ZERO. Этоиспользованныеданные,неOOS,числанеliveprofile.

| Метрика | Принятый baseline | Candidate текущего этапа | Hold текущего этапа |
|---|---:|---:|---:|
| Net OPEN_MTM,USDT |11.06966847132799999999999|11.06966847132799999999999|−3.0163046400000|
| Net_flat,USDT |7.81013594056799999999999|7.81013594056799999999999|−6.2256576000000|
| Fees входов/rebalance/exit,USDT |6.4646422394320|6.4646422394320|4.1504384000000|
| Rebalances |69|69|0|
| Natural simulated cycles |невычислялись|0|0|
| Synthetic completed cycles |невычислялись|1|1|
| Реальныециклыкарантина |неподтверждены|0|0|
| PF/status |невычислялись|null/NO_LOSS|0/NO_PROFIT|

Baseline — принятый synthetic closeout study. Result/trades/equity обеихarms ивесьterminal_closeout точносовпали; новаяинформациятолькоциклы/PF. NetэпизодасовпалсNet_flat. PFcandidateнеопределён;PFgateнеподтверждён. EngineeringPASSнеявляетсяPF/admissionPASS.

## Ограничения и STOP

Непрерывныйпортфельможетдажезадлинноеокноиметьодинsyntheticepisode; дополнительныециклыдлядопусканевыдумываются. Неподтверждены24месяца/closed6monthOOS,stressdouble-slippage,depth/partial/latency/liquidation/liveprofile. Syntheticfillнеявляетсябиржевымисполнением;natural_simulatedтакженереальныйцикл. ЛокальныеSHA/SQLiteнеимеютнезависимойаутентификации. Работающий24hpublicpaper snapshotнеизменён. STOPдляприёмкиреализацииPF.
