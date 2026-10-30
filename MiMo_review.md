Разобрал backtest/backtest_rebalance.py (1652 строки) против заметки от 2026-10-21. Статический разбор по коду, тесты не гонял.
Критические несоответствия
1. Второй бюджет сверх CASH — profit_budget (backtest_rebalance.py:1054-1055, core/calculator.py:399-400, core/profit_budget.py:67-69)
Expansions дополнительно ограничены «подтверждённой прибылью» (pnl − fee только с reduce_only; released margin в бюджет не попадает). Документ §3.3/§4.2: CASH — единственный бюджет, поступления от surplus включают высвобождённое обеспечение. Доказательство из же документа: paper-мост §5.1 — expansion SHORT на 44.6 маржи после reduction LONG с прибылью ~9.9 был бы заблокирован profit_budget'ом, но paper его исполнил.
2. Дефолтный funding — legacy CASH-first (backtest_rebalance.py:681-685, pay_funding_legacy в core/funding.py:325-335)
funding_model по умолчанию legacy_constant: списывает с CASH, V — только остаток. §7 требует: «списывать funding сначала непосредственно с USDT-доли VIRTUAL». V-only модель (signed_actual, apply_funding_backtest) есть, но не дефолт и требует сценарный журнал. Плюс legacy тянет постоянный платёж (long_notional + short_notional) × rate с обеих ног — §7 прямо: «постоянный расход обеих ног не объявляется точным историческим funding».
3. CASH может уйти в минус без guard'а — нарушает §3.3/§6.2 «CASH всегда ≥ 0; отрицательное — дефект»:
- legacy funding: state.val_cash -= amount без пола (funding.py:333); а shortfall-детект (get_funding_shortfall, backtest_rebalance.py:251-253) смотрит только на virt_debt, который release_virtual не даёт уйти в минус → в legacy halt — мёртвый код, CASH молча < 0;
- siphoning: state.val_cash -= siphon_amount без проверки (backtest_rebalance.py:1338) — прибыль largely в unrealized ногах, CASH мал → минус.
4. Initial build + cash_reserve_usdt против §3.5 (backtest_rebalance.py:1178, core/capital.py:21, share_basis_scale :564)
При reserve>0 и V=0 (сейчас в конфиге: reserve=8900, V=0) остаток стартового капитала остаётся в CASH, targets масштабируются на trading/total. Документ: после initial build CASH строго 0, весь неизрасходованный капитал — в VIRTUAL, цели 33/33/34 от TPV. Механизм funded reserve в документе отсутствует.
5. Funding shortfall → permanent halt вместо Spot top-up (backtest_rebalance.py:700-743, 752-756)
При исчерпании V: закрытие ног + навсегда-халт. §7: «если VIRTUAL исчерпан, разрешено покрытие со Spot с проверкой cross-margin guard». Переводов Spot↔Futures в бэктесте нет вообще — §8 требует их симуляцию в backtest/paper (единичное признание события, рост/снижение V, не CASH).
Расхождения в семантике
6. Приоритет VIRTUAL обратный документу (core/calculator.py:403, backtest_rebalance.py:1051)
sort(key=lambda x: x["key"] == "VIRTUAL") → VIRTUAL в конце (комментарий в коде: «Futures deficits have first claim»). §4.2: «VIRTUAL приоритизирован среди deficit-действий».
7. Share-trailing (backtest_rebalance.py:143-147,638-642, core/calculator.py:222-235) — при trail>0 гейтинг ног идёт по наблюдаемым экстремумам, а не по порогу доли. Документ (21.10) описывает чисто пороговую логику; контракт трейлинга датирован 23.10 — позже документа. Помечаю как расхождение с описанным в документе поведением, не как баг.
Совпадает с документом (проверено)
TPV=L+S+V+C и формулы ног (§6.2) ✓; reductions перед expansions ✓; deficit cap по фактическому CASH после reductions ✓; requested/approved/executed/partial/skipped записываются ✓; negative initial CASH → cash_invariant_violation без обнуления ✓; residual initial → VIRTUAL ✓ (кроме кейса reserve); DESIGNATE/RELEASE сохраняют TPV ✓; FUSE только на surplus ✓; cross-margin guard считает весь V ✓; SAFE не тратится на докупки ✓.
Дополнительно: текущий конфиг (V share=0, reserve=8900, funding_model=default) живёт вне модели 33/33/34 документа — заметь, что сами по себе значения конфига документ не запрещает, но кейсы 2 и 4 активны именно с ним.
Не проверено: runtime-поведение (бэктест/тесты не запускал), сверку с core/main.py (paper/live контур) — вопрос был только про backtest_rebalance.py.



Objective
- Проверить актуальное описание торговой логики бота C:\Python\Prosperous_Bot\futures_portfolio из Obsidian и найти все несоответствия между backtest\backtest_rebalance.py и спецификацией D:\Obsidian\MyVault\Hermes\Docs\futures-portfolio-trading-logic-2026-10-21.md.
- Эмпирически подтвердить найденные несоответствия запуском python.exe backtest\backtest_rebalance.py --config config.json --ticker IRYSUSDT, используя строго текущий config.json как единственную базовую конфигурацию (без переопределений, кроме --ticker IRYSUSDT).
Important Details
- Obsidian vault зарегистрирован в %APPDATA%\obsidian\obsidian.json: D:\Obsidian\MyVault\Hermes и C:\Python\Prosperous_Bot\t-invest. Актуальная заметка по торговой логике: D:\Obsidian\MyVault\Hermes\Docs\futures-portfolio-trading-logic-2026-10-21.md.
- Ключевые требования спецификации: TPV = L+S+V+C, цели 33/33/34; после initial build CASH строго 0 (остаток → VIRTUAL); CASH всегда ≥0; deficit только в пределах CASH; funding списывается с VIRTUAL в первую очередь во всех режимах; Spot↔Futures переводы моделируются в backtest; при исчерпании V разрешён Spot top-up с cross-margin guard; VIRTUAL приоритизирован среди deficit-действий; SAFE/siphoning — отдельный резерв; «CASH пополняется released margin + realized PnL — не ограничено чистой прибылью».
- Режим плана (plan mode) активен — только чтение; прогон выполнялся in-memory через run_backtest() из Python без записи отчётов в reports/ (CLI-обёртка пишет файлы — ей не пользоваться).
- config.json меняется внешними правками между чтениями (наблюдено: cash_reserve_usdt 8900 → 1000000 → 100000 → 59000 → 60000; sha256 менялся f6439d3b → c54eb19c). В последнем прогоне sha до/после совпал (c54eb19c99705d61), но значения на момент прогона: cash_reserve_usdt=60000, initial_capital=1000, base_ticker=APRUSDT, V share=0, funding_model=None (=legacy_constant), backtest_days=28, trail_distance=0.058, trailing stop pct=0.211/activation=1.295/timeout≈34s, toxic_cooldown_days=0.09, targets L=S=0.5/V=0 (lev 3/3/1).
- Данные: data/IRYSUSDT_live.feather, 46080 минутных баров, бэктест 28 дней = 40320 баров.
- Замечание AGENTS.md проекта: lazy-dev стиль ответов, код-ссылки на английском; в текстовых ответах без изменения кода двухстрочный footer не выводится.
Work State
Completed
- Найден и прочитан источник истины: D:\Obsidian\MyVault\Hermes\Docs\futures-portfolio-trading-logic-2026-10-21.md (status: "Решения зафиксированы; initial CASH и parity открыты").
- Статический аудит backtest\backtest_rebalance.py (1534+ строк) + core/calculator.py, core/profit_budget.py, core/capital.py, core/funding.py. Выявлены 7 несоответствий:
1. F1 (критично) — profit_budget как второй бюджет сверх CASH (backtest_rebalance.py:1054-1055, core/calculator.py:399-400, profit_budget.py:67-69): expansions ограничены pnl−fee, released margin не учитывается — противоречит §3.3/§4.2.
2. F2 (критично) — дефолтный funding_model=legacy_constant = CASH-first waterfall (core/funding.py:325-335), противоречит §7 (V-first); legacy тянет платёж (long+short)*rate с обеих ног.
3. F3 — CASH может уйти в минус без guard'а: val_cash -= amount без пола в pay_funding_legacy (funding.py:333), shortfall-детект смотрит только на virt_debt (backtest_rebalance.py:251-253); siphoning: val_cash -= siphon_amount без проверки (backtest_rebalance.py:1338).
4. F4 — cash_reserve_usdt механизм против §3.5: остаток остаётся в CASH, targets масштабируются (backtest_rebalance.py:1178, core/capital.py:21, share_basis_scale).
5. F5 — funding shortfall → permanent halt + закрытие (backtest_rebalance.py:700-743,752-756) вместо разрешённого Spot top-up; переводов Spot↔Futures нет вообще (§8 требует симуляцию в backtest).
6. F6 — приоритет VIRTUAL обратный документу: sort(key=lambda x: x["key"] == "VIRTUAL") → VIRTUAL в конце (calculator.py:403, backtest_rebalance.py:1051), §4.2 говорит «приоритизирован».
7. F7 — share-trailing (backtest_rebalance.py:143-147,638-642, calculator.py:222-235): при trail>0 гейтинг по экстремумам вместо порогов доли; контракт трейлинга датирован 23.10 (позже документа).
- Эмпирическая верификация (in-memory прогон на текущем config.json, sha стабилен):
- F1 подтверждён: 4 expansions обрезаны profit budget'ом при ~98% CASH (bar 10922: req 295.77 → exec 56.33; bar 22304: req 787.32 → exec 224.97; reasons capped_by_available_funds+capped_by_free_cash); финальный profit_budget ≈ 0.0000087.
- F2 подтверждён: funding_model=legacy_constant, funding_paid=15.70, funding_from_virtual=0.
- F4 подтверждён: initial_formation cash=60685.79, virtual=0.0 — CASH после build ≠ 0 (cash_invariant_violation=False).
- F7 подтверждён активен: trail signals=289, consumes=36.
- F3/F5/F6 не воспроизведены в этом прогоне (CASH слишком большой, V=0 → нет VIRTUAL-действий и shortfall'а).
- Статус: completed, cycles=30, profit_pct=+1.40%, final_tpv=61855, accounting_ok=True, trailing stop 1 (bar 35343, DD 0.27% от ATH, cooldown 0.09д, rebase, второй initial build на bar 35476).
- Начата диаганостика аномального max_dd=97.09%: equity curve min=1784.07 @bar 33623 (провал на 3 бара, потом восстановление), только 10 баров ниже 50% от max. Рядом события: share_trail_signal с огромными запросами, trend_guard_block net move 7-13%, trend_add_block move 67.23% — похоже на кратковременный accounting-артепакт или скачок цены.
Active
- Диагностика провала equity-кривой на bar 33623 (min 1784 при TPV~61000): вывод обрезан на событиях около min; нужно установить причину (вероятно spike цены IRYS → unrealized PnL/liquidation-guard артефакт или проблема учёта TPV на один бар) — потенциально восьмое несоответствие/баг.
Blocked
- (none)
Next Move
1. Закрыть диагностику bar 33623: посмотреть tpv/tpv_before в rebalance у bar 33623, fills рядом (execution_price, slippage), поведение hard_liquidation_guard/accounting_equity на этот бар — определить, артефакт ли это кривой (например, close_price скачок) или реальная проблема учёта.
2. Собрать финальный отчёт несоответствий (F1–F7 + возможный F8 про equity-curve spike) с подтверждениями по запуску на текущем config.json, отдельно отметив, что F3/F5/F6 не exercised при текущем конфиге (V=0, огромный CASH reserve).
Relevant Files
- D:\Obsidian\MyVault\Hermes\Docs\futures-portfolio-trading-logic-2026-10-21.md: эталонная спецификация торговой логики (веха истины).
- C:\Python\Prosperous_Bot\futures_portfolio\backtest\backtest_rebalance.py: проверяемый файл бэктеста (1534 строки); ключевые места: :1054-1055 (profit_budget cap), :1178 (residual→CASH при reserve), :1338 (siphoning без пола), :251-253 (shortfall только по virt_debt), :700-756 (funding halt).
- C:\Python\Prosperous_Bot\futures_portfolio\core\funding.py: pay_funding_legacy (:325-335, CASH-first), apply_funding_backtest (:306-320, V-only).
- C:\Python\Prosperous_Bot\futures_portfolio\core\profit_budget.py: бюджет подтверждённой прибыли (второй гейт expansions).
- C:\Python\Prosperous_Bot\futures_portfolio\core\calculator.py: :399-400 (available_funds cap), :403 (VIRTUAL-сортировка), :222-235 (share-trail).
- C:\Python\Prosperous_Bot\futures_portfolio\core\capital.py: capital_with_reserve — масштабирование targets при cash_reserve_usdt.
- C:\Python\Prosperous_Bot\futures_portfolio\config.json: единственная разрешённая конфигурация прогона; меняется внешними правками — фиксировать sha256 перед/после прогона.
- C:\Python\Prosperous_Bot\futures_portfolio\data\IRYSUSDT_live.feather: входные данные (46080 баров).
- C:\Python\Prosperous_Bot\futures_portfolio\docs\CONFIG_REFERENCE.md: справочник параметров (последнее обновление 30.10.2026).



Всё сходится: git diff показывает, что с момента прогона конфиг менялся только самим приложением (tickers ротация, probation_paper timestamp) — торговые параметры идентичны прогнанному снимку. Финальный отчёт по задаче:
Проверенный снимок
- Прогон: sha256 c54eb19c99705d61 (3043 байт), до/после прогона совпал; данные IRYSUSDT_live.feather, 28 дней = 40320 бара, offset 5760.
- Текущий config.json = 344e38656001eaaf4 (3047 байт): отличается от прогонного только runtime-книговедением самого приложения (tickers, probation_paper) — торговые параметры те же, выводы 1:1 применимы к текущему конфигу.
- Параметры прогона: reserve 60000 (total initial 61000), targets L/S/V = 0.5/0.5/0 (lev 3/3/1), rebalance_share_trail_distance=0.058, equity trailing 0.211%/1.2949%/34.45s, toxic_cooldown_days=0.0901, max_drawdown_limit=200, funding_model не задан → legacy_constant, siphoning_threshold_pct=1438.4.
- Результат: profit +1.402%, MaxDD 97.09%, 30 cycles, accounting_ok=True (два идентичных прогона).
Сводная таблица несоответствий
#	Несоответствие (спека vs код)
F1	§3.3/§4.2: deficit-действия лимитируются только CASH; код — второй бюджет profit_budget сверх CASH: calculator.py:399-400 (available_funds = min(cash+proceeds, profit_budget+net_profit)), backtest_rebalance.py:1054
F2	§7: funding списывается с VIRTUAL первым; дефолт legacy_constant = CASH-first: funding.py:325-335
F3	CASH может уйти в минус: val_cash -= без пола (funding.py:333), siphoning без проверки (backtest_rebalance.py:1338), shortfall-детект смотрит только virt_debt (:251-253)
F4	§3.5: после initial build CASH строго 0, остаток → VIRTUAL; код при cash_reserve>0 и V target=0 оставляет остаток в CASH: backtest_rebalance.py:1178
F5	§8: shortfall → Spot top-up; в backtest его нет вообще, вместо этого permanent halt + закрытие (backtest_rebalance.py:700-756)
F6	§4.2: VIRTUAL «приоритизирован»; код сортирует его последним: calculator.py:403, backtest_rebalance.py:1051 (sort(key=...=="VIRTUAL") → False раньше True)
F7	§4.1: пороги в п.п. доли; код — share-trail по экстремумам (backtest_rebalance.py:143-147,638-642, calculator.py:222-235) + share_basis_scale ужимает пороги до 0.000656
F8	Equity-провал bar 33623 → 1784
Побочные наблюдения
- config.json мутируется самим приложением во время работы (probation/tickers) — sha нужно фиксировать до прогона; рядом изменён core/paper_budget.sqlite (фоновый paper-режим — не трогал).
- initial_formation хранит только последнюю сборку (bar 35476 перезаписал bar 0) — первый build виден только по fills.
- Требование спеки «CASH пополняется released margin + realized PnL — не ограничено чистой прибылью» нарушается именно F1 (это подтверждено числами выше).
Что пропущено/не проверено: runtime-верификация F3/F5/F6 невозможна на этом конфиге (V=0, резерв глушит CASH-пути, shortfall не наступал) — запрет на переопределения это исключает; paper/live контур core/main.py и CLI-обёртка (пишет reports/*) не запускались.
Оставшиеся риски: числа прогона воспроизводимы только на sha c54eb19c — при следующих внешних правках конфига агрегаты сдвинутся; F8-дыра 97% MTM устраняется только конфигом (max_drawdown_limit/activation), код это не чинит.