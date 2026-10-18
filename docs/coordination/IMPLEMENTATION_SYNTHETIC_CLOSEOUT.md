# Synthetic terminal closeout — 2026-10-18

## Основание и идентификация

User явно утвердил TERMINAL_VALUATION_PROPOSAL.md для offline-исследований. Checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio, HEAD69d528d. До изменений присутствовали принятый evidence verifier, его tests/evidence/docs и предложение terminal methodology; они сохранены. Этот этап не коммитился: в последнем сообщении commit/push не запрошены.

## Exact diff и поведение

- rebalance/closeout.py: closeout на deepcopy Portfolio. Проверка costs/curve/целочисленных units/leg balances/reserve/liability. Отдельный полный REDUCE LONG по synthetic bid и SHORT по synthetic ask, уникальные receipts и отдельные terminal fills. Нулевые ноги не создают fills. При ошибке исходный Portfolio не меняется. Убыточная нога закрывается без прибыльностного rebalance gate.
- rebalance/historical.py: optional state_sink получает detached terminal Portfolio; стандартные result/trades/curve не меняются. rebalance/comparison.py: аналогичный callback hold и opt-in terminal_closeout; добавлен отдельный summary.terminal_closeout, исходные OPEN_MTM arms и summary fields сохранены.
- rebalance/research_runner.py: --terminal-closeout, boolean mode в prospective metadata, седьмой source closeout.py в frozen snapshot; child запускает compare с зарегистрированным режимом. Evidence остаются восемью JSON, terminal данные и fills внутри comparison.json защищены его SHA. Исторические snapshots не изменяются.
- rebalance/research_verify.py: поддержаны старый и новый наборы source roles. Явно сообщает terminal_closeout_validation=HASH_ONLY; прежние semantic checks относятся к OPEN_MTM. Отдельный reports/closeout_2026-10-18/verify.py сверяет terminal arithmetic и baseline этого этапа.
- rebalance/scenarios/test_closeout.py:17 сценариев. Документы proposal/draft обновлены с явным решением User. Frozen candidate, все input/codeSHA, execution manifest/registration и результаты находятся в reports/closeout_2026-10-18/fixed_BTCUSDT; runtime SQLite исключенаGit. verification.json содержит SHA текущих source/tests.

Цена-прокси — последний candle close M; h=(spread_bps/2+slippage_bps)/10000; sell=M(1−h),buy=M(1+h). Формула Equity_flat=Equity_open+qL(sell−M)+qS(M−buy)−exit_fees. Старые fees/funding уже внутри Equity_open и повторно не вычитаются. После применения Portfolio.fill нулевые units/margin/avg/open_fees проверяются отдельно. Результат сохраняет фактическую Equity от fill; формульная Equity и residual экспортируются. Допуск округления только для Decimal bridge:8ULP масштаба Equity при текущем decimal context, не Net floor и не правило принятия стратегии. На фиксированном опыте residual численно0 у обеих arms.

Terminal point имеет тот же timestamp с phase=TERMINAL; новый timestamp/funding не выдумывается. max_close_plus_exit_drawdown_pct пересчитывается по прежней close curve и этому exit point с исходным capital и peak. Это не intrabar drawdown. Основная curve/trades не дополняются, чтобы не менять OPEN_MTM контракт и число rebalances; terminal point/fills сохранены отдельно.

## Проверки и команды

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_closeout.py rebalance/scenarios/test_comparison.py rebalance/scenarios/test_historical.py rebalance/scenarios/test_research_runner.py rebalance/scenarios/test_research_verify.py -q --confcutdir=rebalance --junitxml=reports/closeout_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.research_runner run --database reports/closeout_2026-10-18/runtime/trials.sqlite --id fixed-BTCUSDT-synthetic-closeout --candles reports/audit_2026-10-16/market_data/BTCUSDT.csv --config rebalance/historical_example.json --funding-zero --terminal-closeout --output reports/closeout_2026-10-18/fixed_BTCUSDT
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m reports.closeout_2026-10-18.verify
```

51tests PASS:17новых+10comparison+8historical+6runner+10verifier. Проверены обе ноги, no netting, убыточный выход, funding один раз, flat безfills/fees, прежний peak drawdown, malformed costs/units/state/curve/receipt и неизменность исходногоPortfolio, matched OPEN_MTM exact на fixtures, funding финальной свечи доexit, snapshot registration сновыммодулем. Whole-pytest/PM2/parameter search не запускались.

Один fixed backtest выполнен через prospective registry и frozen child; outcomeCOMPLETED. Повторять по существующему output нельзя. Для проверки готового опыта используется последняя команда, она не пересчитывает backtest.

Входы: BTCUSDT1m September2026,43200 candles, ранее использованный период. DataSHA6b482fc0d05691cda897515d6cdb81d878b8fd3d40c3d46a2e2a88e5fc99284a. Capital10000USDT, surplus/deficit/trail .7/.7/.09, spread2bps, slippage1bps, fee.0004, leverage7;funding явноASSUMED_ZERO. Эти числа не live profile. Исходники/входы полностью сохранены snapshot и SHA.

## Фактические результаты

| Метрика USDT | Baseline | Candidate этого этапа | Hold этого этапа |
|---|---:|---:|---:|
| Net OPEN_MTM |11.06966847132799999999999|11.06966847132799999999999|−3.0163046400000|
| Fees до выхода, уже вNet |4.2916094086720|4.2916094086720|2.0108697600000|
| Rebalances |69|69|0|
| Synthetic exit fees |не рассчитывались|2.1730328307600|2.1395686400000|
| Synthetic Net_flat |не рассчитывался|7.81013594056799999999999|−6.2256576000000|
| Итоговая Equity_flat |не рассчитывалась|10007.81013594056799999999999|9993.7743424000000|
| Max close+exit DD,% |не рассчитывалась|0.2944687246513410952860231718|0.0622565760000|
| Остатки LONG/SHORT/margin |открытые|0/0/0|0/0/0|

Baseline: принятый registered fixed-BTCUSDT-current. Новые OPEN_MTM result/trades/equity для обеих arms и исходная часть summary точно совпали с ним; только terminal extension новая. Portfolio exit Equity точно совпала с ранее показанной арифметической иллюстрацией. Delta Net_flat14.03579354056799999999999USDT не является доказательством alpha.

## Ограничения и передача

Режим только synthetic candle full fills; не моделирует depth/latency/partial/cancel/UNKNOWN/liquidation/исторические filters. Reserve/liability блокируют helper; это не универсальная процедура ликвидации реального аккаунта. Продажа и покупка не netted, но предполагаются исполнимыми в одну терминальную фазу, без дополнительного времени/funding. Stress doubled slippage всего окна не запускался; данный этап его не заменяет. PF/cycle,24/6месячныйOOS и live profile не определены и не приняты. Admission/trading flagsfalse.

Активный24hpublic-paper snapshot/runtime не менялись, биржевых операций нет. STOP для приёмки реализации closeout.
