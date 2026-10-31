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



+ Thought: 51.5s
Диагностика F8 завершена. Важно: конфиг снаружи сменился — текущий config.json (sha 4B201CB5…, reserve=0, capital=1000) уже другой, чем прогонный снимок из ревью (sha c54eb19c, reserve=60000). Прогон на нём: 28 дней IRYSUSDT, in-memory, sha до/после совпал.
Вердикт: не артефакт учёта, а реальный mark-to-market провал шорт-ноги
Что было в старом прогоне (F8, bar 33623):
- В данных IRYSUSDT_live.feather строки 39373–39393 — настоящий памп: 0.21 → 0.496 (+136%) за ~10 баров, затем дамп обратно к 0.20. Объёмы на пике ×10 обычных (4.7e7–1.5e8).
- Equity-кривая метится по close: history.append(get_tpv(mid_price)+reserve) (backtest_rebalance.py:1434). На пике пампа unrealized шорта уходит в глубокий минус → TPV проседает до 1784 и восстанавливается при дампе. Провал «на 3 бара» = ровно столько длил памп-дамп.
- Учёт в том прогоне сходился (accounting_ok=True) — check_accounting на каждом баре проходил. Ошибка учёта исключена.
- Сопутствующие события из ревью (гигантские share_trail_signal-запросы, trend_add_block move 67%) — следствие: трейлинг пытается перебалансировать ноги внутрь движения, гварды блокируют.
Что делает текущий конфиг (sha 4B201CB5…, reserve=0):
- Тот же механизм, но без подушки: equity ~95–150 при огромном нотионале → на bar 33049 памп всего +5.2% за бар (строка feather 38809: 0.0359→0.0371) пересекает MM → HARD CROSS LIQUIDATION, весь аккаунт обнулён, cooldown_dead на bar 33178 («capital below 5 USDT»), дальше equity=0 навсегда. Итог: profit -100%, max_dd 100%, liquidations=1. Bar 33623 в этом прогоне уже мёртв — цифры F8 из ревью на текущем конфиге невоспроизводимы, они принадлежат снимку c54eb19c.
Побочное наблюдение (ограничение модели, не баг): и предиктивный, и post-factum liquidation-guard считаются по close (mid_price), а не по high бара. На баре 39383 high=0.504 vs close=0.496 — в реальности ликвидация могла сработать внутри бара, бэктест её пропустил. Старый прогон «выжил» с TPV=1784 именно на close; это случайность close-прайса, а не устойчивость позиции.
Вывод для F8: закрыть как «real MTM drawdown on price pump, accounting consistent» — восьмым несоответствием спеке не является. Отдельное решение за тобой: считать ли close-only liquidation-guard недочётом (тогда это новая находка F9, чинится проверкой hard_liquidation_triggered по high/low бара).
Что пропущено/не проверено: старый снимок конфига c54eb19c недоступен — точные числа F8 (min=1784, позиции на bar 33623) восстановлены по данным цены и логике кода, не повторным прогоном; intrabar-ликвидация на high не численно верифицирована (нужны позиции старого прогона).
Оставшиеся риски: config.json снова мутируется снаружи — все числа выше валидны только для sha 4B201CB5…; paper/live контур не смотрел.

---

# Ликвидации 35 тикеров (30.10.2026): причины и исправления

Задача: объяснить 6 × -100% (AGT, AIN, APR, IRYS, RLC, US) из CLI-прогона на текущем config.json (sha 4B201CB5…, reserve=0) и проверить reports/profit_only_fix_20261030/FINAL_RESULTS.md.

## Проверка FINAL_RESULTS.md — подтверждена
- Все агрегаты сходятся с final/report.json: net −21150.856982 (−6.104%), MaxDD 6.707%, 606 циклов, accounting_ok у всех 35, liquidated=[AGT, IRYS]; AERO_cli_result.json == AEROUSDT_result.json (без equity-кривой).
- Код не менялся с финального реплея: все 14 code_sha256 из final/job.json совпадают с текущим деревом (git-ветка «docs: liquidation fix» код не трогала).
- Данные те же: sha256 текущих data/*_live.feather совпадают с input-sha из job.json (проверено на 6 проблемных тикерах).
- Провенанс-нюанс: working_config_sha256=c70aa2ce… в job.json унаследован из родительского jobs reserve_search35; реально использованный снапшот final/config_snapshot.json имеет sha fcb7644f… (reserve=8900). FINAL_RESULTS.md это не обещает — расхождений нет, но поле вводит в заблуждение.

## Корневая причина 6 × -100%
Единственное отличие текущего прогона от финального реплея (код+данные идентичны): внешняя правка конфига cash_reserve_usdt 8900 → 0 (и paper_account_free_margin 1000→60000 — не читается бэктестом). С reserve=8900 ликвидировались только AGT и IRYS; AIN (−28.8%), APR (−8.4%), RLC (+1.8%), US (−5.2%) пережили то же самое движение цены благодаря 8900-башковому CASH-буферу (механизм F4-из-аудита). Без буфера: торговый аккаунт 1000 USDT при нотионале ног 2–6× equity и 30–94% adverse-движении за 28 дней механически обнуляется.
Механика каждого из 6 (состояние на баре ликвидации, из in-memory прогона):
- AGT bar23858: S 54543 @0.0378 → цена 0.0733 (+94%), TPV −233 < MM 16
- AIN bar4710: S 83038 @0.0287 → 0.0506 (+76%), TPV −46
- APR bar24658: L 30812 @0.0869 → 0.0418 (−52%), TPV −7
- IRYS bar33049: S 114468 @0.0227 → 0.0371 (+63%), TPV −40
- RLC bar7612: S 6606 @0.4064 → 0.636 (+57%), TPV −0.17
- US bar12856: S 145560 @0.0174 → 0.0288 (+66%), TPV −1.3
Общие факторы: (а) трейл-доги доlosing-ноги наращивают нотионал сверх equity; (б) trailing stop срабатывает, но после cooldown rebase аккаунт повторно заходит в рынок и догорает смертью-спиралью; (в) предиктивный guard отключён конфигом (warn/crit = 0.0 — осознанно, см. CHANGELOG).

## Найденные и исправленные баги (backtest/backtest_rebalance.py)
1. Post-factum hard-liquidation проверялся ТОЛЬКО по close. Биржа маркает ликвидацию непрерывно: adverse-экстремум бара (high для net-short, low для net-long) — худшая точка TPV/MM. Исправлено: adversal_price = max(high,close) / min(low,close) по знаку net-позиции, wipe выполняется на этом экстремуме, чек учёта и событие «liquidation» получают поле price. Тест: tests/test_backtest_rebalance.py::test_hard_liquidation_uses_adverse_bar_extreme (2 стороны). Эффект: исходы 6 тикеров не изменились (−100% и так), но бар ликвидации стал честным (раньше на 3–57 баров: AGT 23858→23855, AIN 4710→4653, IRYS 33049→33016, APR 24658→24657); контрольные тикеры совпали с CLI-числами 1:1.
2. Потенциальный краш: config.json шлёт reinvestment_ratio=null → portfolio_cfg.get(...) вернёт None (ключ существует) → TypeError в siphon-ветке (1 - None). Исправлено: float(... or 0.0). На текущих порогах (siphoning_threshold_pct=1438%) не триггерится — минировано только.

## Не исправлено (решения за пользователем — доменная политика, не трогал)
- Метрика предиктивного guard ((TPV−MM)/TPV — это доля equity, а не цена): при текущем sizing она не может сработать раньше последнего ~1% equity; формула специально зафиксирована тестами как parity с paper A2a (test_main_coverage.py::test_a2_paper_guard_boundaries_are_exact, test_backtest_rebalance.py::test_hard_guard_and_predictive_distance_share_tpv_formula). Замена на price-distance до liquidation price = смена семантики live-guardа.
- Guard отключён в конфиге (0/0): включение (crit>0) — конфиг-решение; с честной price-метрикой он спасал бы все 6 тикеров от -100% (закрытие на уровне ~8% до liq price).
- Rebase после trailing stop снова заводит 3x-книгу в тренд (смерть-спираль US: 13 стопов → liquidation). Политика cooldown/re-entry — стратегия, не баг.
- Удалённый reserve 8900 — основной источник 4 новых -100%; вернуть буфер или пересчитать risk-params = доменное решение.

## VERIFY
- pytest tests/test_backtest_rebalance.py: 38/38 passed (вкл. 2 новых параметра).
- In-memory пере-прогон 11 тикеров на текущем config.json (sha 4B201CB5…, до/после совпало): 6 × -100% сохранены (accounting_ok=True, liq_price логгируется), PONS −55.55%, ARIA +12.30%, BR +42.24%, ERA +36.80%, AERO −0.68% — совпадение с CLI-числами пользователя 1:1.
- Затронутые пути: backtest_rebalance.py (2 правки), test_backtest_rebalance.py (новый тест). core/main.py и paper-метрики не трогались.

Что пропущено/не проверено: полный 35-тикерный прогон после фикса не гонялся (гонялись 6 ликвидированных + 5 контрольных); paper/live-контур с новой интрабар-семантикой не синхронизирован (main.py по-прежнему close-based) — расхождение backtest/paper осознанно оставлено до решения по price-метрике; working_config_sha256 в job.json не пересчитывался.
Оставшиеся риски: интрабар-wipes могут добавить ликвидации на тикерах, которые сейчас «выживают» (в выборке из 11 не наблюдалось); config.json продолжает мутироваться снаружи — числа выше валидны только для sha 4B201CB5… и текущего состояния кода.

---

# Семантика cash_reserve_usdt: внешняя свободная маржа аккаунта (30.10.2026)

Контракт (пользователь): портфель — `L:50.0% {+500} S:50.0% {+500} V:0.0% {0} C:0.0% {0}` от trading capital 1000; `cash_reserve_usdt` (сейчас 100000) НЕ входит в портфель — это свободная маржа аккаунта, которая подстраховывает только liquidation-модель. Funding остаётся портфельным расходом.

Исправлено в backtest/backtest_rebalance.py (ранее резерв смешивался в портфель через capital_with_reserve: база P&L/стопов/кривой = 101000, цели масштабировались, share_basis_scale=0.0099):
- setup: capital_with_reserve в бэктесте не вызывается; цели — сырые из конфига (0.5/0.5/0 от 1000 → маржа 500/500, C=0); share_basis_scale=1.0; `cash_reserve_usdt` валидируется и уходит в `state.account_free_margin`.
- Liquidation-модель (post-factum hard + предиктивный guard + интрабар-экстремумы из прошлой правки): collateral аккаунта = TPV + account_free_margin; при full-wipe внешняя маржа обнуляется.
- `portfolio_default` — аккаунтовый уровень: стоп только если TPV + free_margin ≤ 0 (1000-портфель может быть отрицательным в MTM, аккаунт жив).
- F4: residual initial теперь безусловный → VIRTUAL (CASH после build = 0).
- Экспорт: settings.account_free_margin = фактический резерв; initial_capital/trading = 1000.

Тесты: test_cash_reserve.py::test_backtest_keeps_reserve_external_to_portfolio (initial_capital=200, cash=0, margins 100/100); test_backtest_rebalance.py: rename guard-теста + 2 новых (внешняя маржа гасит hard-ликвидацию; run-level wiring). 50/50 passed (test_backtest_rebalance + test_cash_reserve).

Полный прогон 35 тикеров (config sha ED64DFBA, reserve=100000, 28 дней; in-memory, 4 воркера): 0 ликвидаций, accounting_ok=True у всех 35.
- 29 тикеров: значения 1:1 совпали со списком пользователя (AERO −0.68/10.71/14 … BR +42.24/54.82/87 … PONS −55.55/97.86/8, VVV +5.59/10.67/16) — «stale»-значения оказались верными для новой семантики.
- 6 прежде ликвидировавшихся (теперь выживают за счёт подушки, новые значения на 1000-базе):
  AGT +54.33 / DD 315.43 / 84; AIN +32.60 / 118.52 / 88; APR −85.83 / 158.54 / 10; IRYS −8.38 / 4776.80 / 46; RLC −31.14 / 582.17 / 10; US −126.95 / 172.58 / 84.
- Кавычки-оговорки: MaxDD>100% и P&L<−100% — честный MTM портфельной базы 1000 на низких высокотяжёлых альтах (IRYS: котировка 0.02→0.5 в окне, qty ~130k → unrealized до −60k), подушка 100k держит аккаунт; это не clamp к −100%.

Не синхронизировано (live-контур, решение за пользователем): core/main.py (строки 493, 1049, 2411), supervisor/swarm_manager.py и scanner/backtest_gate.py по-прежнему используют старую склеенную семантику capital_with_reserve — paper-строка «L:50.0%{+500}» на текущем paper-контуре не воспроизведётся, пока main.py не приведут к тому же контракту.



Готово. Итог:
Причины 6 × -100% (AGT/AIN/APR/IRYS/RLC/US):
- Код и данные идентичны финальному реплею (проверено по хешам); единственное отличие — внешняя правка конфига cash_reserve_usdt 8900 → 0. С буфером 8900 ликвидировались только AGT и IRYS (это и подтверждает FINAL_RESULTS.md — все его числа воспроизводятся из final/report.json).
- Без буфера: нотионал проигрывающей ноги 2–6× equity (3x cross, трейл докармливает losing-ногу) + движение 30–94% против позиции за 28 дней → механическое обнуление. Trailing stop спасает несколько раз, но после cooldown rebase аккаунт снова заходит в рынок и догорает.
Исправленные баги (backtest/backtest_rebalance.py):
1. Post-factum hard-liquidation проверялся только по close — биржа маркает ликвидацию непрерывно. Теперь чек по adverse-экстремуму бара (high для net-short / low для net-long), wipe на этом уровне. Исходы −100% сохранены, бар ликвидации стал честным (AGT 23858→23855, AIN 4710→4653, IRYS 33049→33016). Тест test_hard_liquidation_uses_adverse_bar_extreme (2 стороны).
2. reinvestment_ratio: null в конфиге → потенциальный TypeError в siphon-ветке (1 - None); заменено на float(... or 0.0).
Не трогал (решения за тобой): метрика предиктивного guard (TPV−MM)/TPV не-предиктивна, но зафиксирована тестами как parity с paper A2a; guard отключён в конфиге (0/0) — включение + замена на price-distance до liquidation price спасли бы все 6 тикеров от −100%, но это смена live-семантики; rebase-death-spiral — стратегия.
VERIFY: 38/38 тестов; 11 тикеров перегонялись in-memory на текущем конфиге (sha стабильно): контрольные (ARIA/BR/ERA/AERO/PONS) совпали с твоими CLI-числами 1:1. Исследования и верификация FINAL_RESULTS.md записаны в MiMo_review.md (новый раздел в конце).
Что пропущено/не проверено: полный 35-тикерный прогон после фикса не гонялся (6 ликвидированных + 5 контрольных); paper-контур (core/main.py) интрабар-семантики не получил — осознанный scope-limit до решения по guard-метрике.
Оставшиеся риски: интрабар-wicks могут добавить ликвидации на тикерах, которые сейчас «выживают» (в выборке 11 не наблюдалось); config.json мутируется снаружи — числа валидны для sha 4B201CB5… и текущего состояния кода.


Готово. Контракт реализован: портфель ведётся от 1000 (L 50%{500} + S 50%{500} + V 0 + C 0), cash_reserve_usdt=100000 — внешняя свободная маржа аккаунта, входит только в liquidation-модель. Funding — портфельный расход (без изменений).
Что исправлено в backtest/backtest_rebalance.py:
- capital_with_reserve в бэктесте больше не склеивает резерв с портфелем: база initial_capital/val_cash/ledger_base = 1000, цели сырые (0.5/0.5/0), share_basis_scale=1.0
- account_free_margin = cash_reserve_usdt: collateral ликвидаций = TPV + 100k (hard/post-factum + интрабар-экстремумы + предиктивный guard), при full-wipe 100k обнуляется
- portfolio_default поднят до аккаунта (TPV + 100k ≤ 0) — 1000-портфель может быть в минусе по MTM, бэктест не рвётся
- F4: residual initial → VIRTUAL без исключения (CASH после build = 0), settings.account_free_margin экспортируется фактический
Валидация: 50/50 тестов (включая 2 новых и переписанный test_backtest_keeps_reserve_external_to_portfolio). Все 35 прогонов на конфиге sha ED64DFBA: accounting_ok=True, ликвидаций 0.
Результаты:
- 29 из 35 совпали с твоим списком 1:1 (включая AERO −0.68/10.71/14, BR +42.24/54.82/87, PONS −55.55/97.86/8, VVV +5.59/10.67/16) — «stale»-значения были правильными для новой семантики.
- 6 тикеров (прежде −100%) теперь выживают и имеют новые значения на 1000-базе (твои вставленные −0.37/−10.30/+1.07/… были артефактом старой склеенной семантики 101000):
Ticker	Profit	MaxDD
AGTUSDT	+54.33%	315.43%
AINUSDT	+32.60%	118.52%
APRUSDT	−85.83%	158.54%
IRYSUSDT	−8.38%	4776.80%
RLCUSDT	−31.14%	582.17%
USUSDT	−126.95%	172.58%
MaxDD>100% / P&L<−100% — честный MTM портфельной базы 1000 (IRYS: котировка 0.02→0.5 в окне, qty ~130k, unrealized до −60k; подушка 100k держит аккаунт).
Важно: paper/live-контур (core/main.py:493,1049,2411, swarm_manager, scanner/backtest_gate) до сих пор на старой склеенной семантике — строка paper «L:50.0%{+500}» не воспроизведётся, пока main.py не приведут к тому же контракту. Это live-путь — решение за тобой: синхронизировать или нет.
Что пропущено/не проверено: paper/live-контур не синхронизирован с новой семантикой (осознанно — live); 35 тикеров гонялись in-memory (CLI-эквивалентность подтверждена ранее на AERO и PONS), CLI-обёртки по 35 не запускались; свип данных IRYS/US в окне не верифицировался против биржи (внутренняя непротиворечивость: accounting_ok на каждом баре).
Оставшиеся риски: отрицательный портфельный P&L (US −126.95%) не зажат в −100% — если нужен clamp на «выживаемость аккаунта», это доменное решение; config.json мутируется снаружи — таблица валидна для sha ED64DFBA; paper-контур дивергировал от бэктеста.