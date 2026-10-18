# Исследовательский допуск — рабочий проект, не утверждённый профиль

Дата2026-10-18. Независимая подготовка во время суточного paper-forward. Источник существующих требований: ACCOUNTING_SPEC_V3.md §4.1/Б.7. Этот документ не меняет торговую политику и не создаёт разрешения на реальную торговлю.

## Что можно делать до завершения paper

- Подготовить каталог происхождения исторических данных:источник,символ,интервал,time bounds,скачано UTC,SHA,пропуски/дубликаты,историю funding и биржевые filters.
- Зафиксировать интерфейс исследования:одна версия kernel/config,одинаковые окна и издержки для candidate и исходного hold; при внесении капитала отделять неторговый поток от результата.
- Подготовить реалистичную модель исполнения и fault fixtures отдельно от текущего frozen worker. Цены,latency,partial fills,комиссии,funding и терминальные расходы должны быть явно представлены; приближённый candle replay не объявлять execution proof.
- Подготовить отчёт исследовательских попыток,включая неудачные; один использованный период не называть новым OOS после retune.

## Требования договора, уже существующие

§4.1 требует24месяца истории,последние6месяцев закрытой OOS,предварительный freeze кода/параметров/издержек/критериев. Full PF>1.5,MaxDD<7%;OOS Net>0,PF>1.2,MaxDD<7%;удвоенное проскальзывание не должно давать отрицательный OOS Net. Это ссылки на действующие требования,не новые выбранные параметры и не утверждение,что они уже выполнены.

§Б.7 требует конкретный strategy_admission_profile.json,утверждённый User до первой реальной сделки. Пустые поля/примеры/defaults не создают допуск. Profile freeze должен предшествовать открытию OOS. Неизвестные параметры не заполнять удобными числами ради запуска.

## Открытые предметы для согласования до OOS/реального пилота

| Предмет | Что требуется | Текущее состояние |
|---|---|---|
| Dataset windows |Точные24/6месячные границы и доказуемо неиспользованный OOS|Не выбраны;закрытые данные не открыты этим этапом|
| Цикл и PF |Недвусмысленная экономическая группировка ребалансировок,fees/funding и открытого inventory|Не утверждено;отдельный fill не считать циклом по умолчанию|
| Terminal equity |Расходы закрытия обеих ног,сверка mark vs executable closeout|Текущий OPEN_MTM не заменяет final closeout|
| Execution model |Depth/latency/partial/cancel/UNKNOWN,market filters и risk brackets|Неполная;paper full fills недостаточны|
| Реальный risk/capital profile |User-approved числовые лимиты и действия остановки для общей futures margin|Не утверждён;research leverage7/virtual10000 не считаются live-параметрами|
| Hold control |Те же initial positions/срок/fees/funding/terminal exit,без ребалансировок|Требуется реализация и воспроизводимое сравнение|

Спорные определения выше — проект работы,не принятые решения. До их согласования можно разрабатывать изолированные инструменты и fixtures,но нельзя выдавать новые экономические метрики за согласованный admission gate. Суточный technical PASS,если будет получен,не решит эти вопросы автоматически.

## Реализованная независимая часть: каталог данных

Добавлен offline rebalance.dataset_catalog: byte SHA и размер,CSV time bounds/1m continuity/OHLC,пропуски/дубликаты/порядок,описание источника и acquisition UTC,отдельные funding/filter artifacts. SHA и анализ строятся по одним captured bytes. Отсутствующий funding не означает ноль; даже валидные строки не доказывают покрытие;архив JSON filters не доказывает историческую применимость.

Известный BTCUSDT September2026 помечен previously_used/ALREADY_USED;это не независимый OOS. Источник из прежнего market_data_manifest — declared evidence,точное время получения не восстановлено и не выдумывается. Поля admission_ready/trading_authorized всегда false. Никаких24месячных/OOS данных на этом этапе не открывали и не скачивали.

Отчёт реализации: IMPLEMENTATION_DATASET_CATALOG.md. Это завершает инструмент инвентаризации;не утверждает определения PF/циклов/terminal closeout и не заменяет strategy_admission_profile.

## Реализованный исследовательский контроль hold

rebalance.comparison сопоставляет принятый historical.replay с удержанием первоначальных LONG/SHORT без ребалансировок. Проверяются идентичность первых fills и общей timeline;капитал,fee,spread/slippage и funding-rate inputs одинаковы. Funding cash каждой стороны зависит от её фактического simulated inventory. Нет внешних потоков;интерфейс не объявляет их поддержанными.

Текущая реализация — только matched OPEN_MTM:закрывающих fills и terminal exit costs нет у обеих сторон. Поэтому пункт terminal equity остаётся открытым. PF/cycle не вычисляются,admission_ready=false. Расходы на вход и последующие сделки уже включены в Net;повторно fees не вычитаются. Delta Net к hold — диагностический результат на использованных данных,не доказательство alpha/OOS/реальной исполнимости.

Отчёт: IMPLEMENTATION_MATCHED_HOLD.md. Исполняемые production-модули и работающий24h snapshot не менялись.

## Реестр исследовательских попыток

Добавлен offline rebalance.trial_registry с отдельной SQLite:registration snapshot code/config/data SHA иmetadata,режим PROSPECTIVE/RETROSPECTIVE,один неизменяемый terminal outcome иполный отчёт без фильтрацииfailed/aborted/incomplete. COMPLETED означает завершение заявленного запуска,нечисловойPASS/допуск. Несовпавшие/изменённые входы не разрешается завершать какCOMPLETED;неудачу можно записать FAILED с причиной. Идемпотентные повторы не дублируют попытку.

Исторические evidence импортируются только как RETROSPECTIVE без подмены времени регистрации. Повторное использование data hash показывается,но semantic overlap временных окон/OOS freshness не устанавливаются. PROSPECTIVE — declaration,не доказательство выполнения из зафиксированного executable snapshot. Неинтегрированные/незарегистрированные запуски реестр не обнаруживает;автоматическое подключение к runner — отдельная работа. Все admission flags остаютсяfalse. Отчёт IMPLEMENTATION_TRIAL_REGISTRY.md.

## Подключение к offline runner

Добавлен rebalance.research_runner:отдельный explicit запуск сравнения с копией исходников/входов,PROSPECTIVE registration до вычисления иchild Python из snapshot. COMPLETED записывается после evidence export,FAILED при расчётной ошибке;crash supervisor может оставить видимыйINCOMPLETE. Один trial/output не запускается повторно. Это подключение только нового offline entry point:ручные historical/comparison и public-paper не объявляются автоматически зарегистрированными. Нет обхода OOS/admission требований,оптимизации или автоматического выбора победителя.

Отчёт реализации: IMPLEMENTATION_REGISTERED_RESEARCH_RUNNER.md.

## Проверка сохранённого исследования

Принятый registered runner опубликован в commit69d528d. Добавлен отдельный read-only rebalance.research_verify: читает SQLite через mode=ro, сверяет конкретную попытку с экспортом, snapshot и восемь evidence файлов по SHA/размеру/ролям/путям; проверяет регистрацию/хронологию, funding mode и settings, совпадение summary с arm results, Net/equity, fees, timeline и initial fills hold. Архивный код не импортируется и backtest не запускается. PASS означает только согласованность сохранённых evidence; локальные файлы/реестр не имеют независимой аутентификации, OOS/исполнение/допуск не подтверждены. Отчёт IMPLEMENTATION_RESEARCH_EVIDENCE_VERIFIER.md.

## Предложение терминальной оценки

Evidence verifier принят User. Методика TERMINAL_VALUATION_PROPOSAL.md утверждена User2026-10-18 только для offline research. Реализован opt-in --terminal-closeout в registered runner: отдельные synthetic exits обеих ног на копии Portfolio, OPEN_MTM baseline сохранён, funding внутри окна начисляется до выхода. Новый fixed registered replay и 51targeted tests PASS; отчёт IMPLEMENTATION_SYNTHETIC_CLOSEOUT.md. PF/cycle, реальное исполнение и live profile остаются открытыми. Старый verifier проверяет новые terminal evidence по SHA; смысловые проверки terminal выполняет отдельный verify.py данного этапа.

Реализация closeout принята User. Следующий независимый этап добавил terminal semantic checks в существующий research_verify: mode/цены/полные exit fills/fees/equity bridge/flat state/funding/drawdown/delta. 39targeted tests PASS, сохранённый fixed closeout experience проверен без replay; подробности IMPLEMENTATION_TERMINAL_EVIDENCE_CHECKS.md. Это проверка согласованности артефактов, не независимая аутентификация или новый торговый допуск.

Terminal evidence checks приняты User. Подготовлен CYCLE_PF_DECISION_PROPOSAL.md: сравнение единиц PF/цикла и конкретные предлагаемые правила полного портфельного эпизода. Read-only анализ сохранённого candidate:71fills,33REDUCE,ни одного natural flat доsynthetic terminal. Поэтому69rebalance не объявляются69циклами. Правила PF/цикла не утверждены и не реализованы; требуется решение User, ограничения существующих gates сохранены.

Решение User2026-10-18: утверждён «цикл от flat до flat всего портфеля, с учётом всех комиссий и funding». Определение зафиксировано в CYCLE_PF_DECISION_PROPOSAL.md; код ещё не менялся. Остальные детали PF и включения synthetic episodes не объявлены принятыми этим узким решением; допуск и договорные пороги не менялись.

Следующим явным решением User утвердил четыре правилаPF из чата. Реализован cycles в comparison с восстановлением ledger через существующий Portfolio, natural simulated/synthetic/zero/open counters, PF/status без автоматического допуска; verifier сверяет cycle report с исполнениям/funding. Fixed registered BTCUSDT сохранил Net/всеfills/curves/closeout; candidate1synthetic winner PF=null/NO_LOSS, hold1synthetic loss PF=0/NO_PROFIT, real_quarantine_cycles0. 67targeted tests PASS. Отчёт IMPLEMENTATION_PORTFOLIO_CYCLE_PF.md; закрытаяOOS/live profile остаются неутверждёнными.

РеализацияPFпринятаUser. На существующемrunner выполнен один зарегистрированный diagnostic double-slippage replay использованного September2026,с прежними code/data/settings кромеslippage1→2bps. Net_flatcandidate7.810135940568→6.394504564840USDT,PFnull/NO_LOSS;OOSstressнеподтверждён. Productionкод/параметрынеизменены. RESEARCH_DOUBLE_SLIPPAGE.md и reports/slippage_stress_2026-10-18 содержатточныевходы,preflightSHA/evidenceиverification.
