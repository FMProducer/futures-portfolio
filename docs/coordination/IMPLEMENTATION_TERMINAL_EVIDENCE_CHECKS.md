# Terminal evidence consistency — 2026-10-18

User принял IMPLEMENTATION_SYNTHETIC_CLOSEOUT.md и разрешил продолжение. Checkout C:/Python/Prosperous_Bot/autonomous-asset-management/futures-portfolio, HEAD69d528d. Существующие незакоммиченные файлы предыдущих принятых этапов сохранены. Commit/push не выполнялись.

CHECK: отдельный компонент не нужен — расширен существующий rebalance.research_verify.verify. Callers: его CLI, test_research_verify.py, test_closeout.py и reports/closeout_2026-10-18/verify.py. Последний сохраняет прежнюю историческую verification, поэтому здесь не запускался. Изменены только verifier и его тесты; runner/kernel/closeout/конфиги/fixtures/frozen snapshot не менялись. Новые зависимости/экономические параметры не вводились.

## Изменения

- Режим terminal_closeout в metadata должен быть boolean и соответствовать наличию секции результата. Старые OPEN_MTM опыты без флага поддерживаются.
- Последние close и timestamp читаются из зарегистрированного candle artifact. Проверяются synthetic bid/ask из прежних settings, quantities/step, полный REDUCE каждой ненулевой ноги без duplicate/missing fills, units/qty/цена/fee и terminal receipt/time/phase.
- Проверяются Equity_open, price adjustment, exit fees, formula Equity, actual Equity/residual и Net_flat. Числовой bridge использует тот же предел8ULP Decimal context, что принятая реализация; это не новый экономический tolerance или ослабление baseline.
- Проверяются нулевые quantities/margin/unrealized, total fees как прежние+exit fees, неизменность funding, terminal curve point, close+exit drawdown от прежнего peak и delta Net_flat двух arms.
- Выход verifier теперь terminal_closeout_validation=SEMANTIC_CONSISTENCY либо NOT_REQUESTED. Snapshot код не импортируется, процесс не запускается, SQLite остаётся mode=ro. Проверка не восстанавливает всю историю PnL из fills и не доказывает подлинность всего локального архива.

Точные исходники verifier/tests этой версии и sourceSHA сохранены в reports/terminal_verify_2026-10-18/source и source_identity.json. Это отделяет проверенную версию от дальнейших изменений working tree.

## Проверки

```powershell
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m pytest rebalance/scenarios/test_research_verify.py rebalance/scenarios/test_closeout.py -q --confcutdir=rebalance --junitxml=reports/terminal_verify_2026-10-18/tests.xml
& '.\reports\audit_2026-10-16\venv\Scripts\python.exe' -m rebalance.research_verify --database reports/closeout_2026-10-18/runtime/trials.sqlite --study reports/closeout_2026-10-18/fixed_BTCUSDT --output reports/terminal_verify_2026-10-18/verification.json
```

39 tests PASS:22verifier+17closeout. Новые проверки моделируют11вариантов согласованной подмены summary/evidence SHA: отсутствие/лишний terminal mode, неверные price/qty, duplicate fill, fee/equity/funding/drawdown/delta и trading claim. Они отклонены по смысловым связям, а не только SHA. Положительный read-only сценарий проверяет неизменность bytes SQLite и всех study files для обоих режимов. Fixtures используют две синтетические свечи, временные DB/output.

Существующий fixed-BTCUSDT-synthetic-closeout: PASS,9snapshot artifacts+8evidence, SEMANTIC_CONSISTENCY; replay_performed=false. Числа остаются из принятого расчёта: Net_flat candidate7.81013594056799999999999USDT, hold−6.2256576000000USDT. Это проверка прежних результатов, не новый backtest. Расчётная модель не менялась; полный backtest/whole-pytest/оптимизация не запускались. Git diff --check без ошибок.

## Границы и передача

Локальные registry и evidence не имеют независимой аутентификации. Согласованная перепись всей истории и источников владельцем не исключена. Не проверены новым replay depth/partial/latency/liquidation/OOS; PF/cycle и live profile не определялись. Admission/trading flagsfalse. Независимый24h worker/runtime/frozen не изменялись. STOP для приёмки terminal evidence checks.
