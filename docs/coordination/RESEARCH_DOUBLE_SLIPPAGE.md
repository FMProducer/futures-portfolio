# Double-slippage diagnostic — 2026-10-18

## Scope и идентификация

После приёмки IMPLEMENTATION_PORTFOLIO_CYCLE_PF.md выполнен один диагностический stress на ранее использованных данных. Основание: ACCOUNTING_SPEC_V3 §4.1 требует неотрицательного OOS результата при удвоенном предположении slippage. Здесь проверена работоспособность и чувствительность уже существующего runner; OOS окно не выбрано и не открывалось.

Checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio, HEAD768e285. Предыдущие working changes сохранены; commit/push не выполнялись. Новый production-код/абстракции/CLI options не требовались: использован существующий registered runner. ЕгоSOURCES, baseline frozen code и текущиеbytes совпали до запуска поSHA (preflight.json). Baseline не перезапускался.

Изменения этапа: только reports/slippage_stress_2026-10-18 с изолированным config.json, preflight, одной зарегистрированной попыткой со snapshot/input/evidence, runtime.gitignore, verify.py и verification.json; этот отчёт и статус draft. Исходные rebalance/historical_example.json, strategy inputs и действующий24h snapshot не менялись.

## Входы и воспроизведение

Baseline: reports/cycles_2026-10-18/fixed_BTCUSDT. Данные взяты непосредственно из его frozen/inputs/data.csv:43200BTCUSDT1m,September2026, SHA6b482fc0d05691cda897515d6cdb81d878b8fd3d40c3d46a2e2a88e5fc99284a. Previouslyused,неOOS. Funding явноASSUMED_ZERO. Capital10000,spread2bps,fee.0004,leverage7,surplus/deficit/trail.7/.7/.09 неизменны. Единственный изменённый setting: slippage_bps1→2, тоестьстрого×2. Код/data/fundingmode/terminalmode одинаковы; сверка выполненаverify.py.

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.research_runner run --database reports/slippage_stress_2026-10-18/runtime/trials.sqlite --id fixed-BTCUSDT-double-slippage --candles reports/cycles_2026-10-18/fixed_BTCUSDT/frozen/inputs/data.csv --config reports/slippage_stress_2026-10-18/config.json --funding-zero --terminal-closeout --output reports/slippage_stress_2026-10-18/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.slippage_stress_2026-10-18.verify
```

Команды уже выполнены, output exclusive; повтор требует новыхtrialid/output. Это полный candle replay: entry, subsequent decisions/rebalances и synthetic exit рассчитаны с2bps; изменение количества/времени сигналов не запрещалось искусственно. Это не поправка толькоterminal fees и не parameter search. OutcomeCOMPLETED, единственныйновыйbacktest.

## Результаты, USDT

| Метрика | Baseline1bps | Stress2bps |
|---|---:|---:|
| Candidate Net OPEN_MTM |11.06966847132799999999999|10.19730366097999999999998|
| Candidate Net_flat |7.81013594056799999999999|6.39450456483999999999998|
| Candidate totalfees, внутриNet |6.4646422394320|6.4642821951600|
| Candidate rebalances |69|69|
| Candidate PF/status |null/NO_LOSS|null/NO_LOSS|
| Hold Net_flat |−6.2256576000000|−7.2632672000000|

Net_flatcandidateснизилсяпримернона1.41563138USDT, сохранилнеотрицательностьнаэтомпериоде. КомиссииотдельноизNetповторноневычитались. Небольшоеснижениеfeesнеозначаетулучшениеисполнения: fee_rateфиксирован, fee_amountзависитотфактическихрасчётныхprices/qty. Количестворебалансировоксовпало,ноисполняемыеценыиequitycurvesдругие.

Baselineиstressкаждыйсодержатодинsyntheticцикл,0natural_simulatedи0real_quarantine;PFcandidateнеопределён. ПоложительныйstressNetнеисправляетнедостатокциклови не являетсяPF/admissionPASS.

## Проверки и ограничения

verify.pyсодержитassertдляединственногоизмененияsettings,×2slippage,идентичностивсехsource/dataartifactSHA/размеров,одинаковыхfunding/terminalmodes;используетсуществующийread-onlyresearch_verifyдляобоихготовыхstudy. ОнпроверяетSHA/registry/terminal/cycleconsistency,нестартуетновыйbacktest. VerificationstatusPASSозначаетсогласованностьэксперимента;stress_candidate_nonnegative=trueотноситсятолькоэтойиспользованнойвыборке. oos_requirement_verified=false,admission_ready=false,trading_authorized=false.

Productionрасчётнеизменялся,прежний67testнаборнеповторялся;новыеunittestsнеписались,проверкиreporthelperвыполненыassert. Full24month/closed6monthOOSидоговорныйOOSstressнепроверены. Candleproxy/fullfills/fundingzero/depth/latency/partial/liquidationограничениясохраняются. Порогигейтовнеизменялись. Работающий24hprocessотдельный:PID124800жив,RUNNING,pending0,stderrпустнаитоговойпроверке. STOPдляприёмкидиагностикиstress.
