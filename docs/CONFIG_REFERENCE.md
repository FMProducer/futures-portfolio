# Справочник параметров config.json (futures_portfolio)

## Назначение документа

Этот справочник описывает **все** параметры файла `config.json` бота-ребалансировщика фьючерсного портфеля: где каждый параметр читается в коде, какое значение подставляется по умолчанию, как он преобразуется (тип/единицы), в каких режимах работает и чем может быть переопределён. Документ написан по фактическому исходному коду, а не по названиям переменных.

- **Исходный commit:** `35f7ed8fe7a7f5e3d4ab50367b2132c19f854adb` (ветка `qwen-code-894be5dd-d4cd-474c-8e0a-86626329c29b`; remote `origin` в контейнере не настроен — проверить принадлежность FMProducer/futures-portfolio по remote не удалось).
- **Использованный config.json:** `/workspace/config.json` (корень проекта), прочитан полностью на момент HEAD.
- **Оговорка про диапазоны:** «допустимый диапазон» в этом документе означает ограничение, **реально проверяемое кодом**. Если явной проверки нет, написано «Диапазон в коде не проверяется». Значения из модулей `optimization/*` — это области поиска Optuna, а **не** валидация рантайма; они помечены отдельно (категория Г — рекомендация автора). Ничего «безопасного торгового диапазона» здесь не выдумано.

Все ссылки вида `файл:строка` соответствуют указанному commit.

---

## 1. Как устроена конфигурация

`config.json` состоит из трёх логических частей:

1. **Глобальные параметры** (`testnet`, `paper_mode`, лимиты ордеров, trailing stop, ротация роя, Telegram и т.д.) — читаются ботом (`core/main.py`), супервайзером (`supervisor/swarm_manager.py`), агрегатором (`supervisor/aggregator.py`), сканером (`scanner/rank_tickers.py`) и бэктестером (`backtest/backtest_rebalance.py`).
2. **Массив `portfolios[]`** — список портфелей. **Важно: код во всех режимах использует только `config["portfolios"][0]`** (первый элемент): `core/main.py:287`, `supervisor/swarm_manager.py:303`, `supervisor/aggregator.py:174`, `backtest/backtest_rebalance.py:261`. Второй и последующие элементы массива **никогда не читаются**. Внутри портфеля: цели ног (`targets`), защитники (`safety_guards`), пороги ребалансировки и per-ticker переопределения (`ticker_thresholds`).
3. **Роевые/служебные поля** (`live_swarm`, `tickers`, `black_list`, `toxic_blacklist_*`, `probation_*` и др.) — частично **изменяются самим супервайзером**: `swarm_manager.py` является единственным мутатором `config.json` (строка 1053: `await safe_save_json(CONFIG_PATH, config)`). Ручное редактирование этих полей будет перезаписано следующим циклом супервайзера.

### Порядок загрузки и переопределения

- **Бот (live/paper):** запускается через PM2 с аргументами `--config config.json --ticker <TICKER> --paper|--real` (`swarm_manager.py:389`, функция `start_bot`). Приоритеты:
  - тикер: `--ticker` > `base_ticker` из конфига > `"BTCUSDT"` (`core/main.py:284`);
  - режим: `--real`/`--paper` > `paper_mode` из конфига > `False` (`core/main.py:1617-1623`);
  - API-ключи: переменные окружения `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` > ключи `api_key`/`secret_key` в конфиге (в текущем конфиге их нет) (`core/main.py:1662-1663`);
  - `MOCK_MODE=1` в окружении подменяет коннектор моком (`core/main.py:1669`).
- **Горячая перезагрузка:** основной цикл бота сравнивает mtime конфига и перечитывает его на каждом шаге (`core/main.py:535-580`). Не все параметры обновляются динамически (см. таблицы).
- **Супервайзер:** читает конфиг в начале цикла, мутирует роевые поля и сохраняет обратно (`swarm_manager.py:564-1053`).
- **Бэктест:** `--config`, `--ticker`, `--days`, `--capital`, `--threshold-surplus`, `--threshold-deficit`, `--since`, `--until`, `--live` (`backtest_rebalance.py:936-948`). CLI-оверрайды имеют приоритет над конфигом для капитала и порогов (`backtest_rebalance.py:321, 331-336`).
- **Сканер:** берёт из конфига только `scanner_period_days` и `black_list` (`scanner/rank_tickers.py:150, 217`); порог `rebalance_threshold=0.005` захардкожен (`rank_tickers.py:216`).

---

## 2. Соглашения по единицам (критично!)

| Запись | Что это на самом деле |
|---|---|
| `share: 0.33` | **доля** (0…1), не проценты. Сравнивается с долей TPV (`calculator.py:176`) |
| `rebalance_threshold_surplus/deficit: 0.01` | **доля** (0…1) = 1% отклонения; пороги применяются к `diff_share` (`calculator.py:175-184`) |
| `max_price_velocity_pct: 2.0`, `trend_min_move_pct`, `net_move_block_pct`, `max_spread_pct` | **проценты**, код делит на 100 (`main.py:499-501, 572-576`) |
| `equity_trailing_stop_pct`, `equity_trailing_stop_activation_pct`, `max_drawdown_limit`, `siphoning_threshold_pct`, `paper_min_free_margin_pct`, `liquidation_distance_*_pct`, `replacement_efficiency_threshold_pct` | **проценты** (не делятся на 100 там, где используются как %; см. отдельные пункты) |
| `*_sec` | секунды; `*_days` | дни, перемножаются на 86400 (сутки = 24 ч) |
| `*_bars` | количество минутных свечей (в live — фактически не используется, см. ниже) |

---

## 3. Глобальные параметры

### `testnet`
- **Текущее значение:** `false` | **Тип:** bool
- **Назначение:** выбор песочницы Binance Futures вместо реальной сети.
- **Чтение:** `core/main.py:1680` — `cfg.get("testnet", True)` при создании `BinanceConnector`; `core/connector.py:65-88` (`verify_connection`: testnet=True → тестовые URL, иначе `api1.binance.com`/`fapi.binance.com`). Также `supervisor/swarm_manager.py:582` (default **True**) и `supervisor/aggregator.py:160` (default **False**).
- **Единицы:** нет. **Преобразование типа:** нет, передается как есть.
- **Поведение:** `true` — торговля только на testnet; `false` + `paper_mode=false` — реальная торговля реальными деньгами. Реальная торговля дополнительно требует Hedge Mode на бирже (`main.py:461-466`) и установленного CROSSED margin type (`main.py:478-486`), иначе бот не стартует.
- **Диапазон:** булев; значений вне true/false код не ожидает. **Default不一致 (замечено противоречие):** default в `main.py` и `swarm_manager.py` — `True`, в `aggregator.py` — `False`. При отсутствии ключа бот и супервайзер уйдут на testnet, а агрегатор будет читать реальный баланс.
- **Режимы:** live, paper (для симуляции на testnet), supervisor, aggregator. В backtest не используется (бэктест ходит только на публичный `fapi.binance.com`, `rank_tickers.py:97`).
- **CLI:** не переопределяется флагами.

### `paper_mode`
- **Текущее значение:** `true` | **Тип:** bool
- **Назначение:** режим симуляции (теневой баланс в `paper_shadow_*.json` / `paper_state_*.json`), без отправки ордеров.
- **Чтение:** `core/main.py:281` (`config.get("paper_mode", False)`), `core/main.py:1623` (default `False`).
- **Приоритет:** CLI `--paper` / `--real` перекрывают конфиг (`main.py:1617-1623`). Супервайзер всегда запускает paper-ботов с флагом `--paper`, real — с `--real` (`swarm_manager.py:377`), т.е. для процессов роя этот ключ фактически не влияет на режим.
- **Поведение:** `true` — спред-гард и проверка ликвидности на бирже пропускаются, позиции считаются по теневому балансу (`main.py:691-703, 719-729`); `false` — реальные ордера, проверка hedge/leverage/margin при старте.
- **Диапазон:** булев. **Default:** `false` — отсутствие ключа означает **разрешение реальной торговли** (важно!).
- **Режимы:** live/paper. В backtest/supervisor не читается.

### `limit_order_enabled`
- **Текущее значение:** `false` | **Тип:** bool
- **Назначение:** разрешить исполнение лимитными ордерами с fallback на маркет.
- **Чтение:** `core/executor.py:296` (`get_limit_order_params`, default `False`).
- **Фактическое использование:** **де-факто отключён принудительно.** Перед каждым пакетом ребалансировки `execute_actions` пишет `portfolio_cfg["limit_order_enabled"] = False` (`executor.py:508`) — «принудительное Market-only исполнение» (`executor.py:497-501`). Основной путь исполнения (`main.py:1171`) всегда идёт через `execute_actions`.
- **Режимы:** live (real). Даже если выставить `true`, кодом перебивается в `false` перед исполнением. Пометка: параметр присутствует, но **на поведение повлиять не может**.

### `limit_offset_pct`
- **Текущее значение:** `0.1` | **Тип:** float (процент, делится на 100 внутри механизма лимиток)
- **Чтение:** `core/executor.py:297`, default `0.2`. Используется только в `execute_limit_with_fallback` (`executor.py:~200-287`), который из основного пути недостижим (см. `limit_order_enabled`). **Влияние на поведение в текущем коде: отсутствует.**

### `limit_timeout_sec`
- **Текущее значение:** `30` | **Тип:** int, секунды
- **Чтение:** `core/executor.py:298`, default `30`. Как и выше — только для недоступного лимитного пути. **Влияние: отсутствует.**

### `min_notional_usdt`
- **Текущее значение:** `6.0` | **Тип:** float, USDT (номинал ордера)
- **Назначение:** минимальный размер ордера; отсекает «пыль».
- **Чтение и использование:**
  - `core/main.py:894-897`: `config_min = portfolio_cfg.get("min_notional_usdt", current_config.get("min_notional_usdt"))`; если оба отсутствуют — **`ValueError("min_notional_usdt must be set in config.json")`**; затем `active_min_notional = max(config_min, exchange_min * 1.02)` — берётся максимум с биржевым MIN_NOTIONAL фильтром + 2% буфер (`main.py:896-897`; внешний лимит Binance подтверждён чтением `exchange_info` `main.py:442`).
  - `core/main.py:1136`: фильтр действий `abs(diff_usdt) >= min_notional` (глобальный default `6.0`).
  - `core/executor.py:330, 383`: default `6.0`; в paper-исполнении ордер меньше notional → статус `SKIPPED` (`executor.py:~396-401`).
  - `supervisor/swarm_manager.py:275`: порог «пыли» для Invariant Gate, default `5.5` (**противоречие с default 6.0 в других местах**).
  - `backtest/backtest_rebalance.py:352`: default `6.0`.
- **Портфельный override:** ключ можно задать внутри `portfolios[0]` — имеет приоритет над глобальным (`main.py:894, 1136`). В текущем конфиге на уровне портфеля отсутствует.
- **Увеличение** — меньше мелких ордеров, ребаланс грубее; **уменьшение** — больше точности, но риск отказа биржи (Binance сам отклонит ордер ниже своего minNotional; защита — формула `max(config, exchange*1.02)`).
- **Диапазон в коде не проверяется** (кроме требования «не None» в `main.py:895-896`). Отрицательное значение формально проходит (фильтр `abs(diff) >= min` тогда почти всегда истинен).

### `max_orders_per_second`
- **Текущее значение:** `10` | **Тип:** int
- **Назначение:** rate-limit исходящих ордеров экзекутора.
- **Чтение:** `core/main.py:455` (`config.get(..., 10)`) → конструктор `PortfolioExecutor(connector, base_ticker=..., max_orders_per_second=max_ops)`; троттлинг внутри executor. Изменение применяется только при старте процесса (в hot-reload блока `535-580` не перечитывается).
- **Диапазон в коде не проверяется.** Внешний лимит Binance (подтверждённый документацией API, но не проверяемый этим кодом): 2400 weights/мин — код его не учитывает.

### `min_cycles_for_rank`
- **Текущее значение:** `15` | **Тип:** int (число ребаланс-циклов)
- **Назначение:** минимум циклов для рейтингования/замены ботов и знаменатель-стабилизатор эффективности.
- **Чтение (несколько мест, разные defaults — противоречие):**
  - `supervisor/aggregator.py:115` default 20 — efficiency = profit / max(cycles, min_cycles) для сортировки сводки;
  - `supervisor/swarm_manager.py:440` default 10 — порог «сырой» статистики в `_calc_rotation_score` (cycles < min и profit ≤ 0 → score −∞, `swarm_manager.py:529-531`);
  - `swarm_manager.py:548` default 10 — `get_bot_efficiency`;
  - `swarm_manager.py:804` default 10 — `calculate_bot_score`: paper-кандидат с cycles < min отклоняется (−∞) (`swarm_manager.py:406-408`);
  - `swarm_manager.py:913` default 20 — условие испытательного срока REAL-бота;
  - `supervisor/swarm_analyzer.py:19` default 20.
- **Режимы:** supervisor, aggregator, analyzer. В live-боте не используется.
- **Увеличение** — дольше защита новичков/дольше допуск в REAL; **уменьшение** — раньше ротация по шумной статистике. Диапазон в коде не проверяется (0 тоже «работает»: деление через `max(cycles, min)`).

### `min_cycles_for_rotation`
- **Текущее значение:** `6` | **Тип:** int (циклы)
- **Чтение:** `supervisor/swarm_manager.py:441` (default 6) — незрелые paper-боты (cycles < значения) защищены от замены в инкубаторе (`swarm_manager.py:473-479`). Только supervisor/ротация. Диапазон в коде не проверяется.

### `max_bots`
- **Текущее значение:** `10` | **Тип:** int (кол-во процессов)
- **Чтение:** `swarm_manager.py:438` (default 20) — размер инкубатора; `:766` (default 20) — limit_bots; `:853` (default 10) — слоты REAL: `max_real_slots = max(0, max_bots − paper_mode_bots)`; `:1044` (default 20) — fallback при пустом `tickers`; `swarm_analyzer.py:18` (default 10) — рабочий капитал.
- **Зависимость:** `max_real_slots = max(0, max_bots - paper_mode_bots)` (`swarm_manager.py:853`). При текущих 10−10=0 **боевых REAL-слотов нет** — вся торговля идёт только в paper. Уменьшение `paper_mode_bots` или увеличение `max_bots` открывает REAL-слоты.
- **Диапазон в коде не проверяется;** отрицательный результат разницы защищён `max(0, …)`. Режим: supervisor.

### `paper_mode_bots`
- **Текущее значение:** `10` | **Тип:** int
- **Чтение:** только `supervisor/swarm_manager.py:853` (default 9) — сколько из `max_bots` зарезервировано под paper-инкубатор при расчёте REAL-слотов. Никакого другого влияния на «paper-режим» не имеет (режим задаётся флагом `--paper`). Режим: supervisor. Диапазон в коде не проверяется.

### `max_replace_per_cycle`
- **Текущее значение:** `10` | **Тип:** int
- **Чтение:** `swarm_manager.py:439` (default `max_bots // 2`) — в `selective_merge_incubator` переменная присваивается, **но далее в функции не используется** (фактического ограничения замен в теле функции нет — замечено противоречие); `swarm_manager.py:886` (default 1) — реальный лимит числа замен REAL-ботов за цикл (`replacements_count >= max_replace → break`, `swarm_manager.py:939-941`). Режим: supervisor. Диапазон в коде не проверяется.

### `probation_period_days`
- **Текущее значение:** `0.01` (= 14,4 минуты) | **Тип:** float, дни
- **Чтение:**
  - `core/main.py:945, 1032` (default 0.041 ≈ 59 мин) — таймаут «пробации» после Emergency Stop/Trailing Stop: `state["trailing_stop_paper_timeout_end"] = now + days*86400`;
  - `swarm_manager.py:594` (default 0.041) — длительность probation-кулдауна при сигнале `exit`: `probation_cooldown_sec = days * 86400` (`:634-638` — запись в `probation_paper/real`);
  - `swarm_manager.py:909` (default 0.01) — возраст REAL-бота в часах для hysteresis-защиты от замены (`age_hours < days*24 and cycles < min_cycles_for_rank`, `:913`).
- **Режимы:** live/paper (бот) и supervisor. Единицы: сутки (умножение на 86400 сек). Диапазон в коде не проверяется; 0 → мгновенное снятие пробации.

### `backtest_period_days`
- **Текущее значение:** `0.125` (= 3 часа) | **Тип:** float, дни
- **Чтение:** только оптимизаторы: `optimization/optimize_stops.py:79`, `optimize_trailing_stop.py:76`, `optimize_takeprofit_stop.py:237` (везде default `2.0`) — длина окна бэктеста при Optuna-прогонах. **В live/paper/supervisor/backtest CLI не используется** (там длина окна задаётся `--days`). Режим: optimization. Диапазон в коде не проверяется.

### `supervisor_interval_days`
- **Текущее значение:** `0.005` (= 7,2 минуты) | **Тип:** float, дни
- **Чтение:** `supervisor/supervisor_service.py:23-26` (default 0.125): `interval = max(300, int(days*86400))` — **единственная в конфиге явная нижняя граница диапазона, проверяемая кодом: минимум 300 секунд (5 минут), включительно.** Ошибки чтения → fallback 3600 с (`:28`). Режим: supervisor. Уменьшение — чаще циклы ротации; увеличение — реже.

### `scanner_period_days`
- **Текущее значение:** `0.125` (= 3 часа) | **Тип:** float, дни
- **Чтение:** `scanner/rank_tickers.py:217` (default 0.125) → `self.scanner_period_days` (`:99`) → `limit = max(1, int(days * 1440))` минутных свечей для анализа (`:176`). Преобразование: дни → свечи (1440 свечей/сутки). Дополнительно: `swarm_manager.py:729` использует его как **fallback** для `toxic_cooldown_days`. Режим: scanner (вызывается supervisor'ом). Диапазон в коде не проверяется (низ ограничен `max(1,…)`).

### `replacement_efficiency_threshold_pct`
- **Текущее значение:** `50.0` | **Тип:** float, «процент», но применение двойственное (см.)
- **Чтение:**
  - `swarm_manager.py:803` (default 20.0) — присваивается в `replacement_threshold`, **далее в функции не используется** (мёртвая переменная — противоречие);
  - `swarm_manager.py:934` (default 25.0) — реально работает как **score cushion**: `score_cushion = value / 100`; замена REAL-бота происходит, если `cand_score > score + score_cushion` (`:935-941`).
- **Единицы:** абстрактные скор-баллы (profit/cycles·ln). Текущие 50.0 → cushion 0.5. Увеличение — реже ротация REAL; уменьшение — чаще. Режим: supervisor. Диапазон в коде не проверяется.

### `max_drawdown_limit`
- **Текущее значение:** `200.0` | **Тип:** float, % просадки от ATH
- **Чтение:** `core/main.py:446` (default 0.5!) и hot-reload `:566`. Использование: `drawdown_threshold = tpv_ath * (1 - value/100)`; если `total_tpv < threshold` → EMERGENCY STOP (`main.py:926-958`). При 200.0 порог = `tpv_ath * (1-2) = -tpv_ath` — **защита фактически отключена** (TPV не бывает отрицательным). При default 0.5 порог = 99.5% ATH — срабатывал бы почти сразу (замеченное несоответствие единиц: default задан как доля, а используется как процент).
- **Backtest:** `backtest_rebalance.py:355` default `100.0` («по умолчанию 100% = отключён»), проверка `if max_drawdown_limit_pct < 100.0` (`:761`).
- **Оптимизаторы:** область поиска 5.0–50.0 (`optimize_stops.py:54`) — рекомендация автора, не валидация.
- **Режимы:** live, paper, backtest. После emergency stop бот шлёт сигнал `stop` (если TPV < initial_capital) или `exit` (probation) (`main.py:936-944`).

### `equity_trailing_stop_pct`
- **Текущее значение:** `0.001` | **Тип:** float, % просадки от tpv_ath
- **Чтение:** `core/main.py:444` (default 0.0 = выключен). Логика (`main.py:971-1050`): активен только если `equity_trailing_stop_pct > 0` И `tpv_ath ≥ initial_tpv*(1+activation/100)`; если просадка `≥ pct` — засечь время; one-shot: восстановление просадки не отменяет уже начатый отсчёт (`:1044-1048`). Триггер → закрытие всех позиций, флаг `trailing_stop_triggered=true` (дальше Zombie Mode, `main.py:1112-1116, 1671-1677`), сигнал stop/exit.
- **Backtest:** `backtest_rebalance.py:349, 809-830` — та же логика, timeout переводится в бары.
- **Оптимизаторы (рекомендации):** 0.0001..min(act−1, 8) (`optimize_config.py:116-117`), 2.0–25.0 (`optimize_stops.py:55`), 0.3–6.0 (`optimize_trailing_stop.py:54`).
- **Режимы:** live, paper, backtest. Увеличение — стоп срабатывает позже/реже; уменьшение — раньше/чаще. Диапазон в рантайм-коде не проверяется (кроме `>0` как признака включения).

### `equity_trailing_stop_activation_pct`
- **Текущее значение:** `9000.0` | **Тип:** float, % роста над initial_tpv
- **Чтение:** `core/main.py:445` (default 0.0). Активационный порог: `activation_threshold = initial_tpv * (1 + value/100)` (`main.py:973`). При 9000.0 trailing stop требует 90-кратного роста капитала — **фактически никогда не активируется** (смысловое наблюдение, код этого не проверяет). Hot-reload НЕ обновляет этот параметр в живом цикле (в блоке `:566-568` перезагружаются только max_drawdown_limit, trailing_stop_pct и timeout) — **противоречие: activation меняется только рестартом процесса.**
- **Backtest:** `:350, 810`. Оптимизаторы: 5–15 (`optimize_config.py:110`), 0.5–10 (`optimize_stops.py:56`) — рекомендации. Режимы: live, paper, backtest.

### `equity_trailing_stop_timeout_sec`
- **Текущее значение:** `40` | **Тип:** число, секунды
- **Чтение:** `core/main.py:568` (hot-reload, default 0.0). **Первичная загрузка параметра в `main.py` отсутствует** — при старте процесса переменная не инициализируется из конфига вне ветки перезагрузки; фактическое сравнение `elapsed >= equity_trailing_stop_timeout_sec` (`main.py:987`) произойдёт только после первой перезагрузки конфига (изменение mtime). Это потенциальный NameError-путь/неоднозначность — описано как обнаруженное противоречие, код не исправлен.
- **Смысл:** нарушение порога должно продержаться ≥ timeout секунд, прежде чем trailing stop реализуется. Backtest: `int(...)` default 0, `timeout_bars = max(1, sec//60)` (`backtest_rebalance.py:351, 818`). Оптимизаторы: default 60 (`optimize_stops.py:75`). Режимы: live/paper (с оговоркой), backtest.

### `use_v2_scoring`
- **Текущее значение:** `true` | **Тип:** bool
- **Чтение:** **в исследованном коде (вне tests/) использование не найдено.** Фигурирует только в тестах (`tests/test_supervisor*.py:824`, `test_main_coverage.py:41`). Параметр не влияет на поведение.

### `scoring_drawdown_weight`
- **Текущее значение:** `2.0` | **Тип:** float
- **Чтение:** **в исследованном коде (вне tests/) использование не найдено.** Не влияет на поведение.

### `telegram_enabled`
- **Текущее значение:** `false` | **Тип:** bool
- **Чтение:** `core/notifier.py:59` (default `True`) — `TelegramNotifier._is_enabled()`; дополнительно нужен `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` в `.env` (`notifier.py:56-58`), иначе уведомления выключены независимо от конфига. `supervisor/aggregator.py:152` (default `True`) — при false сводки роя не шлются вовсе.
- **Противоречие (единицы дефолтов):** default `True` при отсутствии ключа означает «Telegram включён, если есть токены». Режимы: live/paper (алерты), supervisor-aggregator.

### `telegram_use_queue`
- **Текущее значение:** `false` | **Тип:** bool
- **Чтение:** `core/notifier.py:27` (default `True`). `true` — сообщения пишутся в очередь `signals/telegram_queue` (анти-429), `false` — прямая отправка. Часть вызовов идёт с `force_direct=True` и минует очередь (`notifier.py:75-78`, `aggregator.py:205`). Режимы: live/paper/aggregator.

### `telegram_summary_interval_min`
- **Текущее значение:** `1` | **Тип:** число, **минуты**
- **Чтение:** `supervisor/aggregator.py:209` (default 1): `await asyncio.sleep(interval_min * 60)` — период сводки Swarm Summary. Только режим aggregator. Диапазон в коде не проверяется (0 → continuous loop).

---

## 4. Портфели: `portfolios[]` (шаблонный путь; фактически используется только индекс 0)

### `portfolios[].name`
- **Текущее значение:** `"SyntheticMarketNeutral"` | **Тип:** string
- **Чтение:** **в производящем поведение коде не найдено** (только идентификатор). Пометка: «В исследованном коде использование не найдено» (метка не влияет на логику).

### `portfolios[].initial_capital`
- **Текущее значение:** `1000.0` | **Тип:** float, USDT
- **Назначение:** базовый капитал REAL-бота; база для PnL%, ATH, emergency-stop и расчёта working capital.
- **Чтение и defaults (несогласованные!):** `core/main.py:310` (REAL-ветка, default 86.0), `:563` (hot-reload, 86.0), `:839` (fallback базы TPV, default = shadow balance), `:936/:1041` (global_initial для решения stop vs exit, default 60.0), `:1466` (emergency_stop reset, default 60.0); `swarm_manager.py:310` (reset state REAL, default 80.0), `:306` (fallback в paper, default 115.0); `aggregator.py:174` (default 60.0); `swarm_analyzer.py:16` (default 39000.0); `backtest_rebalance.py:321` (default 1000.0, переопределяется CLI `--capital`).
- **Динамика:** изменение в конфиге вызывает ребазирование `initial_tpv/reference_tpv` живого бота (`main.py:615-626`).
- **Единицы:** USDT. Увеличение — все %-пороги соответствуют большим абсолютным суммам; PnL считается от новой базы. Уменьшение — наоборот. Отрицательное/0: `main.py:615` требует `target_initial > 0` для ребазирования; `total_pnl_pct` защищён `if initial_capital > 0` (`calculator.py:69`).
- **Режимы:** live, supervisor, aggregator/analyzer, backtest.

### `portfolios[].paper_initial_capital`
- **Текущее значение:** `1000.0` | **Тип:** float, USDT
- **Чтение:** `core/main.py:308, 561` (default 100.0) — база PnL в PAPER-режиме (SSOT: paper использует этот, real — `initial_capital`, комментарий `main.py:306`); `swarm_manager.py:306` (fallback на `initial_capital`, затем 115.0) — сброс paper-state; `backtest_rebalance.py:324` (default = `initial_capital`). Режимы: paper, supervisor, backtest. Диапазон в коде не проверяется.

### `portfolios[].paper_account_free_margin`
- **Текущее значение:** `1000.0` | **Тип:** float, USDT
- **Статус:** legacy, **игнорируется** после A2a. `core/main.py` и `backtest/backtest_rebalance.py` не читают параметр; он не входит в collateral, liquidation price, write-offs или сохраняемый капитал. Оставлен в `config.json` только для обратной совместимости.
- **Поведение:** изменения значения не должны менять paper/backtest guard. Не использовать для новых конфигураций.

### `portfolios[].paper_min_free_margin_pct`
- **Текущее значение:** `15.0` | **Тип:** float, %
- **Статус:** legacy, **игнорируется** после A2a; old free-margin skip удалён. Параметр не влияет на guard или исполнения.

### `portfolios[].margin_ratio_warning`
- **Текущее значение:** `3.0` | **Тип:** float, отношение (x)
- **Чтение:** `core/main.py:447` (default 5.0) — инициализация; применение `main.py:1099` (повторное чтение из свежего конфига): если `m_ratio < warning` (но ≥ critical) — предупреждение в лог/Telegram. **Только REAL-режим** (`main.py:1081`: `if not paper_mode and (margin_warning > 0 or margin_critical > 0)`), т.к. `m_info` в paper пустой (`main.py:729`). Увеличение — раньше предупреждения. 0/negative → условие `<` почти никогда не срабатывает. Диапазон в коде не проверяется.

### `portfolios[].margin_ratio_critical`
- **Текущее значение:** `1.5` | **Тип:** float, отношение (x)
- **Чтение:** `core/main.py:448` (default 2.0); применение `main.py:1085-1086` (перечитывается из конфига): `m_ratio < critical` → EMERGENCY STOP, сигнал `stop`, закрытие позиций, self-kill PM2 (`:1088-1103`). Только REAL. Зависимость: должен быть < `margin_ratio_warning`, иначе warning-зона пуста (код это не проверяет). Диапазон в коде не проверяется.

### `portfolios[].liquidation_distance_warn_pct`
- **Текущее значение:** `0.0` | **Тип:** float, % расстояния до цены ликвидации
- **Чтение:** `core/main.py:738` (default 15.0), передаётся в `_handle_liquidation_guard` (`main.py:789-797`). **Замечено:** в `_handle_liquidation_guard` (`main.py:175-220`) аргумент `liquidation_distance_warn` **нигде не используется** — warn-уровень фактически мёртвый (только crit действует). В paper-формуле расстояние до смус-ливидации (`main.py:780-787`). Backtest: `backtest_rebalance.py:662` (default 15.0) — тоже читается, но в предиктивном guard'е (`:665`) используется только crit. Текущее значение 0.0 → warn всё равно ничего не делал бы. Диапазон в коде не проверяется.

### `portfolios[].liquidation_distance_crit_pct`
- **Текущее значение:** `0.0` | **Тип:** float, %
- **Чтение:** `core/main.py:739` (default 8.0). Paper: `dist <= crit` → аварийное закрытие обеих ног по симулированной цене ликвидации + сигнал `stop` (чёрный список) (`main.py:175-…`, `:798-803`). Real-путь в main для биржевой liq пропущен (`main.py:741-745` — полагаются на margin ratio). Backtest: `:663` (default 8.0), `cross_liq_dist_pct <= crit` → predictive liquidation + cooldown (`:665-700`).
- **Поведение при 0.0:** критическое закрытие наступает только при фактической цене ликвидации (distance=0) — защита максимально «поздняя». Увеличение — раньше закрывает. Диапазон в коде не проверяется.

### `portfolios[].post_factum_liquidation_check`
- **Текущее значение:** `false` | **Тип:** bool
- **Чтение:** **в исследованном коде использование не найдено** (только в config). Post-factum проверка ликвидаций в real-режиме реализована безусловно (`main.py:1400-1435`, LIQUIDATION DETECTED по positionRisk) и от этого флага не зависит. Не влияет на поведение.

### `portfolios[].siphoning_threshold_pct`
- **Текущее значение:** `1000.0` | **Тип:** float, % от initial_tpv
- **Чтение:** `core/main.py:303, 557` (default 0.0). Логика SAFE (`main.py:1355-1372`): `siphoning_threshold_abs = initial_tpv * pct/100`; сифонить прибыль, если `total_surplus > reserve + max(0.1, threshold_abs)`. При 1000.0 нужно превысить базу в 10 раз — **резервирование прибыли фактически отключено**. 0.0 → минимальный порог 0.1 USDT (почти любое превышение уходит в SAFE). Backtest: `:347` (default 0.0). Режимы: live, paper, backtest. Диапазон в коде не проверяется.

### `portfolios[].targets.<SIDE>.share` (SIDE = BASE_LONG | BASE_SHORT | VIRTUAL)
- **Текущие значения:** BASE_LONG 0.33, BASE_SHORT 0.33, VIRTUAL 0.34 | **Тип:** float, доля (0…1)
- **Чтение:** обязательный доступ без default: `targets[key]["share"]` в `core/calculator.py:176` (при отсутствии ключа — KeyError), `core/main.py:844, 1117-1119` (VIRTUAL/BASE_LONG/BASE_SHORT напрямую), `main.py:1154` (`.get(key,{}).get("share", 0)` для логов), `backtest_rebalance.py:378, 436`.
- **Использование:** целевая доля ноги в TPV; `diff_share = current_share − target_share`; при |diff| ≥ порога генерируется действие с `diff_usdt = −diff_share * TPV * leverage` (`calculator.py:175-206`). VIRTUAL — метка свободного кэша (1x), её `share` формирует «подушку» (`main.py:844-866`).
- **Зависимости:** сумма долей ideally = 1.0 (0.33+0.33+0.34). Код **не проверяет сумму**; остаток автоматически приходится на cash-долю (`calculator.py:85-96`). share VIRTUAL ≤ 0 → виртуальная нога не инициализируется (`main.py:845-848`).
- **Единицы:** доля, НЕ проценты (0.01 = 1%). Увеличение доли ноги — больше позиции, чаще срабатывание порога именно этой ноги. Диапазон в коде не проверяется. Режимы: live, paper, backtest.

### `portfolios[].targets.BASE_LONG.leverage` / `BASE_SHORT.leverage` / `VIRTUAL.leverage`
- **Текущие значения:** 7, 7, 1 | **Тип:** int (множитель x)
- **Чтение:** `core/calculator.py:74-75` (default 5 для BASE_LONG/SHORT), `:188` (default 1) — NAV ноги = qty·entry/lev и масштаб `diff_usdt` (`:191`); `core/main.py:471` (default 7) — `set_leverage` на бирже **только в REAL** при старте; `main.py:753-754` (default 7) и `:1249` — формула бумажной цены ликвидации (1/lev); `backtest_rebalance.py:375-376` — маржинальные расчёты.
- **Внешний лимит (подтверждён кодом косвенно):** `connector.set_leverage` ограничивается биржей; конкретные лимиты Binance плеча кодом не проверяются. Комментарий в backtest: mmr=0.004 «для плеча ≤ 5x» (`:642`), при lev=7 это упрощение.
- **Поведение:** увеличение — меньше требуемой маржи на ту же долю, дальше liq-цена, больше номинал `diff_usdt` при том же diff_share; уменьшение — наоборот. VIRTUAL.leverage фактически декоративен (вирт-нога = кэш, `main.py:1190-1207`). Default: 5 (calculator) / 7 (main) / 1 (per-key generic) — противоречивые значения по умолчанию. Диапазон в коде не проверяется.

### `portfolios[].safety_guards.max_spread_pct`
- **Текущее значение:** `0.15` | **Тип:** float, % спреда bid/ask
- **Чтение:** `core/main.py:499` (default 0.15) и hot-reload `:572`, **деление на 100**. Применение: только REAL-режим (`main.py:691-703`): стакан limit=5, `spread=(ask−bid)/bid > max_spread` → блок ребаланса в этом цикле. В paper не проверяется. Backtest не использует. Увеличение — легче пропускать блокировки; уменьшение — чаще паузы. Диапазон в коде не проверяется.

### `portfolios[].safety_guards.max_price_velocity_pct`
- **Текущее значение:** `2.0` | **Тип:** float, % движения цены за окно
- **Чтение:** `main.py:500` (default 1.0) / hot-reload `:573`, деление на 100. Velocity Guard (`main.py:641-651`): |Δprice|/price за `velocity_window_sec` > порога → блокировка сделок (continue). Live+paper. Backtest: `:394` (default 2.0) + cooldown на `vg_window_bars` (`:515`). Увеличение — guard срабатывает реже. Диапазон в коде не проверяется.

### `portfolios[].safety_guards.velocity_window_sec`
- **Текущее значение:** `60` | **Тип:** int, секунды
- **Чтение:** `main.py:501` / `:574` (`int()`, default 60). Размер окна истории цен для velocity-guard; полная история хранится 300 с (`main.py:634-637`) — window больше 300 фактически ограничится 300 (математическое ограничение, кодом не проверяется). Backtest: `:395`, бары = `window//60` (`:396`). Единицы: секунды.

### `portfolios[].safety_guards.trend_min_move_pct`
- **Текущее значение:** `999990.91` | **Тип:** float, % net-move
- **Чтение:** `main.py:658` (default 0.7), **деление на 100**; читается из `guards_cfg` **без hot-reload** (после изменения mtime переменная обновляется только внутри той же ветки? — нет: строка 658 обращается к `guards_cfg`, который обновляется в блоке перезагрузки `:571`, поэтому применяется динамически). Trend Guard (`main.py:653-667`): если net_move/start > min_move И эффективность (net/path) > eff_threshold → заморозка. При 999990.91 % движение нужно ~10000x — **guard практически выключен** (похоже на артефакт оптимизатора `optimize_trend_guard.py:186`, диапазон поиска 0.49–0.91). Backtest: `:385` (default 0.5, приоритет у `trend_guard.min_move_pct` в конфиге верхнего уровня — в текущем конфиге ключа `trend_guard` нет). Live+paper+backtest. Диапазон в рантайм-коде не проверяется.

### `portfolios[].safety_guards.trend_eff_threshold`
- **Текущее значение:** `0.55` | **Тип:** float, доля (0…1) без деления на 100
- **Чтение:** `main.py:659` (default 0.35) — порог «эффективности тренда» (отношение чистого движения к пройденному пути). Backtest: `:386` (default 0.85 — **другой default!**). Смысловое ограничение: 0…1 (отношение), кодом не проверяется. Меньше — чаще считаем «тренд» и замораживаем. Live+paper+backtest.

### `portfolios[].safety_guards.net_move_block_pct`
- **Текущее значение:** `2.14` | **Тип:** float, %
- **Чтение:** `main.py:575` (default 1.5, деление на 100) — Net Move Guard: одностороннее движение > порога за `net_move_window_sec` → блок ВСЕХ действий (`main.py:670-686`). **Замечено:** первичная инициализация `net_move_block_pct`/`net_move_window` вне ветки hot-reload отсутствует (переменные появляются только в блоке `:575-576`); до первой перезагрузки конфига NMG не активен (потенциальный NameError перехвачен `except` на `:582-583` — противоречие зафиксировано, не исправляется). Backtest: `:387`. Live+paper+backtest.

### `portfolios[].safety_guards.net_move_window_sec`
- **Текущее значение:** `72` | **Тип:** int, секунды
- **Чтение:** `main.py:576` (`int()`, default 30) — окно NMG; ограничено сверху 300 с буфером истории (`main.py:636`). Backtest: `:388`, бары=`//60` (`:389`). Живёт та же проблема первичной инициализации, что и net_move_block_pct.

### `portfolios[].safety_guards.trend_lookback_bars`
- **Текущее значение:** `10` | **Тип:** int, бары (минуты)
- **Чтение:** **в рантайм-коде (main.py, backtest_rebalance.py) не читается.** Live-Trend-Guard использует фиксированный буфер 300 с и условие `len(price_history)>10` (`main.py:653`); backtest использует хардкод `tg_trend_bars = 30` (`backtest_rebalance.py:390`). Ключ потребляется только Optuna-патчером `optimization/optimize_trend_guard.py:64, 115` (замена `30` → `trend_guard_cfg.get('lookback_bars', 30)` в сгенерированной копии бэктестера). Пометка: «В исследованном рантайм-коде использование не найдено; эффект только в оптимизации».

### `portfolios[].rebalance_threshold_surplus`
- **Текущее значение:** `0.01` | **Тип:** float, доля (1%)
- **Чтение:** `main.py:289, 544` (fallback-цепочка: surplus → legacy `rebalance_threshold` → 0.02); `backtest_rebalance.py:334` (то же). Применение: `calculator.calculate_rebalance(..., threshold_surplus, ...)` → отклонение **выше** цели меньше порога игнорируется (`calculator.py:181-184`). Порог срабатывания фиксации прибыли (SELL редукции).
- **Переопределение:** `ticker_thresholds[<текущий тикер>]` (`main.py:294-300`); CLI бэктеста `--threshold-surplus` (`backtest_rebalance.py:331-333`).
- **Единицы:** доля (0.01 = 1%), НЕ проценты. Уменьшение — чаще ребалансы, больше комиссий; увеличение — реже. Отрицательное → условия `abs(diff) < neg` ложны всегда → действия генерируются постоянно (кодом не проверяется). Режимы: live, paper, backtest.

### `portfolios[].rebalance_threshold_deficit`
- **Текущее значение:** `0.04` | **Тип:** float, доля (4%)
- **Чтение:** `main.py:290, 545` (default = surplus); `backtest_rebalance.py:335`. Порог отклонения **ниже** цели (докупка). CLI `--threshold-deficit`. Смысловое соотношение с surplus обычно deficit > surplus (асимметричный гистерезис); код не проверяет. Остальное — как у surplus.

### `portfolios[].ticker_thresholds.<TICKER>.surplus` / `.deficit`
- **Текущие значения:** BTCUSDT {0.01, 0.04}, ZECUSDT {0.01, 0.045} | **Тип:** float, доли
- **Чтение:** `main.py:294-300` и hot-reload `:548-554`: поиск по **текущему base_ticker процесса**; dict → `{surplus, deficit}` с fallback на портфельные; scalar number → оба порога сразу (`isinstance(t_cfg,(float,int))`); отсутствующий тикер → портфельные значения. Backtest: `:328, 337-343` аналогично.
- **Смысл:** per-ticker переопределение порогов. Для тикеров, не указанных в словаре, действуют общие. Оптимизаторы чистят словарь при поиске общих порогов (`optimize_config.py:55-56`, `optimize_threshold.py:213-218`). Диапазоны в коде не проверяются.

---

## 5. Роевые и служебные поля (верхний уровень)

### `live_swarm`
- **Текущее значение:** `[]` | **Тип:** array[string] (тикеры REAL-бота)
- **Чтение:** `swarm_manager.py:172, 242-244, 276, 294, 576-577, 611-614, 1038` — пул боевых ботов; **мутатор — супервайзер** (`:1038 config["live_swarm"] = sorted(target_real_bots)`). `aggregator.py:175` — подсветка активных в сводке. Бот (`main.py`) не читает. Ручные правки перезаписываются. Режим: supervisor/aggregator.

### `use_real_whitelist`
- **Текущее значение:** `false` | **Тип:** bool
- **Чтение:** `swarm_manager.py:808` (default **True**): при true в REAL-пул допускаются только тикеры из `real_whitelist` (+ уже запущенные real-боты) (`:810-813`). При false — любые кандидаты инкубатора могут попасть в REAL (при наличии свободных слотов). Режим: supervisor. **Внимание:** default True = строгий режим; отсутствие ключа включает whitelist-фильтр.

### `real_whitelist`
- **Текущее значение:** `[]` | **Тип:** array[string]
- **Чтение:** `swarm_manager.py:173, 805, 959, 982, 991` — доверенные тикеры для real (защита от завершения `:991-995`, cleanup-pool `:982`, allowed-набор `:173, 959-960`); `aggregator.py:130` — значок 🛡️ в сводке. Взаимодействует с `use_real_whitelist`. Режим: supervisor/aggregator.

### `black_list`
- **Текущее значение:** `[]` | **Тип:** array[string] (перманентный бан тикеров)
- **Чтение:** `scanner/rank_tickers.py:150` — исключение из результатов скана; `swarm_manager.py:574-575` (list→set), `:625-627` — добавление при сигнале `stop` (токсичный исход), `:1050-1051` — сохранение обратно; `monitoring/health_check.py:83`. **Мутатор — супервайзер.** Режим: scanner/supervisor/monitoring.

### `toxic_cooldown_days`
- **Текущее значение:** `0.001` (= 86,4 секунды) | **Тип:** float, дни
- **Чтение:** `swarm_manager.py:593` (default 0.02) — срок жизни записи в `toxic_blacklist_*` при сигнале stop: `days*86400`; `:729` (default = `scanner_period_days`, final fallback 1.0) — кулдаун toxic-тикеров из сканера; `backtest_rebalance.py:353-354` (default 0.02) — `cooldown_bars = int(days*1440)` простоя после predictive-ликвидации/trailing-stop в бэктесте (`:694, 732, 822`). `health_check.py:84` (default 0) — информация. Режимы: supervisor, backtest. Увеличение — длиннее карантин.

### `toxic_blacklist`
- **Текущее значение:** `{}` | **Тип:** object
- **Чтение:** **в исследованном коде использование не найдено** (читаются только `toxic_blacklist_paper`/`_real`). Пустой объект-наследие, не влияет.

### `toxic_blacklist_paper` / `toxic_blacklist_real`
- **Текущие значения:** `{}` | **Тип:** object {ticker: unix_expiry_ts}
- **Чтение:** `main.py:592-596` — ребазирование initial_tpv тикера в чёрном списке (убыток → сброс на config-капитал, прибыль → сохранение last_tpv); `swarm_manager.py:461, 596-597, 620-627, 655-656, 661-677 (Amnesty), 730-740, 787, 826-829` — quarantining, исключение из инкубатора/REAL, очистка протухших записей со сбросом TS-флагов. **Мутатор — супервайзер** (бот только читает). Зависимость: сроки хранения = `toxic_cooldown_days`. Режимы: live/paper (чтение), supervisor (чтение+запись).

### `tickers`
- **Текущее значение:** `[]` | **Тип:** array[string] (состав инкубатора)
- **Чтение:** `swarm_manager.py:755` — старый инкубатор = tickers ∪ запущенные paper-процессы; `:1043-1047` — **если пусто, супервайзер восстанавливает его из результатов сканера и записывает, плюс меняет `base_ticker` на первый тикер (fallback "ALGOUSDT")**; `aggregator.py:176`, `swarm_analyzer.py:21`, `monitoring/equity_visualizer.py:31` — отображение; оптимизаторы (`optimize_stops.py:71` default ["ALGOUSDT"], `optimize_threshold.py:69` и др.). **Мутатор — супервайзер.** Режим: supervisor/aggregator/analyzer/visualization/optimization. Примечание: сканер-whitelist — отдельный файл `tickers.txt` (`rank_tickers.py:136-141`), не этот ключ.

### `base_ticker`
- **Текущее значение:** `"ZECUSDT"` | **Тип:** string (символ фьючерса)
- **Чтение:** `main.py:284` (default "BTCUSDT"; приоритет `--ticker` CLI выше), `:1473` (emergency_stop), `:1606` (CLI fallback); `backtest_rebalance.py:226, 976`; `swarm_manager.py:1046` — супервайзер **может перезаписать** при восстановлении пустого `tickers`. Определяет: какой тикер обслуживает процесс, выбор `ticker_thresholds`, имена state-файлов (`main.py:1645-1652`), сброс состояния при смене тикера (`main.py:349-360`). Смена тикера у живого бота = полный сброс virt_qty/initial_tpv/ATH. Режимы: live, paper, backtest, supervisor.

### `probation_paper` / `probation_real`
- **Текущие значения:** `{}` | **Тип:** object {ticker: unix_expiry_ts}
- **Чтение:** `swarm_manager.py:598-599, 634-638` (запись при сигнале `exit`, срок = `probation_period_days*86400`), `:682-684` (prune), `:832-836` (skip кандидата в кулдауне). Мягкий cooldown прибыльного исхода, в отличие от toxic_blacklist — без перманентного бана. **Мутатор — супервайзер.** Режим: supervisor.

---

## 6. Параметры, присутствующие в структуре, но не в текущем конфиге (используемые кодом)

Для полноты — ключи, которые код читает из `config.json`, но которых сейчас нет (применяются defaults):

| JSON-путь | Default | Где читается | Смысл |
|---|---|---|---|
| `api_key`, `secret_key` | `""` | `main.py:1662-1663`, `swarm_manager.py:580-581`, `aggregator.py:157-158` | fallback, если нет env BINANCE_API_KEY/SECRET (env приоритетнее) |
| `portfolios[].check_interval_sec` | 15 | `main.py:292, 546` | период опроса рынка (сек), hot-reload поддерживается |
| `portfolios[].reinvestment_ratio` | 0.0 | `main.py:304, 558`; `backtest_rebalance.py:348` | доля прибыли, реинвестируемая вместо ухода в SAFE (`siphon = new_profit*(1-ratio)`, `main.py:1358`) |
| `portfolios[].max_capital_usdt` | = target_initial_cap | `main.py:312, 565` | читается, но фактического применения в расчётах не найдено (полумёртвый) |
| `portfolios[].rebalance_threshold` | 0.02 | legacy-alias для surplus (`main.py:289`) |
| `trend_guard.{min_move_pct, eff_threshold, net_move_block_pct, net_move_window_sec, lookback_bars}` | fallback на safety_guards | только `backtest_rebalance.py:383-389` | top-level override защитников для бэктеста/оптимизации |
| `MOCK_MODE` (env) | — | `main.py:1669` | mock-коннектор |

---

## 7. Взаимосвязи и обнаруженные противоречия

1. **`max_bots=10` и `paper_mode_bots=10` → `max_real_slots=0`** (`swarm_manager.py:853`): боевые REAL-боты не запускаются вообще, несмотря на наличие механизмов. Пока это не изменено, все real-параметры (`margin_ratio_*`, spread-guard, testnet-live) практического эффекта не имеют.
2. **`equity_trailing_stop_activation_pct=9000`** — trailing stop недостижим (нужен рост TPV в 91 раз). Вместе с **`max_drawdown_limit=200`** (порог отрицательный) и **`siphoning_threshold_pct=1000`** и **`trend_min_move_pct≈999991`** — четыре «страховки» одновременно фактически отключены экстремальными значениями.
3. **Несогласованные defaults одного ключа в разных модулях:** `testnet` (True/False), `min_cycles_for_rank` (10/20), `initial_capital` (60/80/86/115/1000/39000), `min_notional_usdt` (5.5/6.0), `max_drawdown_limit` (0.5 в live vs 100.0 в backtest — live-default 0.5% был бы катастрофически чувствительным), `trend_eff_threshold` (0.35 vs 0.85), `use_real_whitelist` (True при отсутствии vs `false` в конфиге).
4. **Hot-reload неполный:** `equity_trailing_stop_activation_pct`, `max_orders_per_second`, `liquidation_distance_*`, `safety_guards.trend_*` не входят в блок перезагрузки `main.py:535-580` (частично: guards читаются через `guards_cfg` на месте — trend-параметры обновляются, activation — нет). `paper_min_free_margin_pct` теперь legacy и не читается.
5. **`net_move_block_pct/net_move_window` инициализируются только в ветке hot-reload** (`main.py:575-576`) — до первого изменения mtime конфига NMG не работает (NameError глушится except `main.py:582`).
6. **`equity_trailing_stop_timeout_sec` не читается при старте** (только `main.py:568`) — см. раздел параметра.
7. **`limit_*` параметры мертвы** из-за принудительного `portfolio_cfg["limit_order_enabled"]=False` (`executor.py:508`).
8. **`max_replace_per_cycle` в `selective_merge_incubator`** присваивается, но не применяется (`swarm_manager.py:439`); работает только вариант для REAL-ротации (`:886`).
9. **`replacement_threshold` (`:803`) — мёртвая переменная;** живёт только score-cushion из `:934`.
10. **`liquidation_distance_warn_pct` используется** в paper canonical guard и backtest для warning-порога; critical-порог управляет закрытием/остановкой.
11. **Супервайзер перезаписывает `config.json` целиком** (`swarm_manager.py:1053`) — ручные значения `tickers/base_ticker/live_swarm/black_list/toxic_blacklist_*/probation_*` не являются SSOT для пользователя.
12. **`portfolios[1..]` игнорируются** всеми потребителями.

---

## 8. Неиспользуемые / неоднозначные параметры (сводно)

- **Не используются вообще (в исследованном коде использования не найдено):** `use_v2_scoring`, `scoring_drawdown_weight`, `toxic_blacklist`, `portfolios[].post_factum_liquidation_check`, `portfolios[].name`, `portfolios[].safety_guards.trend_lookback_bars` (в рантайме; используется лишь Optuna-патчером).
- **Используются, но эффект подавлен кодом:** `limit_order_enabled`, `limit_offset_pct`, `limit_timeout_sec` (market-only доктрина).
- **Используются частично/неявно:** `max_replace_per_cycle` (только REAL-ротация), `max_capital_usdt` (не найден потребитель, если добавить).
- **Работают только в части режимов:** `backtest_period_days` (только optimization), `min_cycles_for_rotation`, `paper_mode_bots`, `use_real_whitelist`, `real_whitelist`, `live_swarm`, `probation_*` (только supervisor); `margin_ratio_*`, spread-guard, `post-factum` ликвидационная проверка (только real); `telegram_summary_interval_min` (только aggregator); `scanner_period_days` (scanner + fallback supervisor); `min_notional_usdt` (live/paper/backtest/supervisor-dust).
- **Legacy/игнорируются:** `paper_account_free_margin`, `paper_min_free_margin_pct` (A2a; сохранены в конфиге для совместимости).

---

## 9. Примеры изменений (без рекомендации торговой политики)

- **Разбудить trailing stop:** уменьшить `equity_trailing_stop_activation_pct` (например, до величины в процентах желаемого прироста) — учесть, что параметр применяется только после рестарта процесса (нет hot-reload).
- **Открыть REAL-слоты:** `max_bots − paper_mode_bots > 0` (например, уменьшить `paper_mode_bots`) — сверх этого потребуется `paper_mode=false` или флаг `--real` и работающий `testnet`/ключи.
- **Вернуть Trend Guard в осмысленную зону:** заменить `safety_guards.trend_min_move_pct` с `999990.91` на величину порядка долей процента (диапазон Optuna-поиска: 0.49–0.91 — это область эксперимента оптимизатора, а не валидация).
- **Включить фиксацию прибыли в SAFE:** уменьшить `portfolios[0].siphoning_threshold_pct` (0.0 = порог 0.1 USDT).
- **Сделать ликвидационный crit более ранним:** увеличить `portfolios[0].liquidation_distance_crit_pct` (сейчас 0.0 = закрытие только по факту ликвидации).
- **Изменить пороги для конкретного тикера:** добавить запись в `portfolios[0].ticker_thresholds` вида `{"ADAUSDT": {"surplus": 0.015, "deficit": 0.05}}` — применится hot-reload'ом (`main.py:548-554`) только процессу с `base_ticker=ADAUSDT`.

---

## 10. Покрытие

Документировано параметров: **45** — 27 глобальных верхнего уровня (testnet, limit_order_enabled, limit_offset_pct, limit_timeout_sec, min_notional_usdt, paper_mode, max_orders_per_second, min_cycles_for_rank, min_cycles_for_rotation, max_bots, paper_mode_bots, max_replace_per_cycle, probation_period_days, backtest_period_days, supervisor_interval_days, scanner_period_days, replacement_efficiency_threshold_pct, max_drawdown_limit, equity_trailing_stop_pct, equity_trailing_stop_activation_pct, equity_trailing_stop_timeout_sec, use_v2_scoring, scoring_drawdown_weight, telegram_enabled, telegram_use_queue, telegram_summary_interval_min) + 18 на уровне `portfolios[]` (name, initial_capital, paper_initial_capital, paper_account_free_margin, paper_min_free_margin_pct, margin_ratio_warning, margin_ratio_critical, liquidation_distance_warn_pct, liquidation_distance_crit_pct, post_factum_liquidation_check, siphoning_threshold_pct, targets.*.share ×3, targets.*.leverage ×3, safety_guards ×9, rebalance_threshold_surplus, rebalance_threshold_deficit, ticker_thresholds.*.{surplus,deficit}) + 12 роевых (live_swarm, use_real_whitelist, real_whitelist, black_list, toxic_cooldown_days, toxic_blacklist, toxic_blacklist_paper, toxic_blacklist_real, tickers, base_ticker, probation_paper, probation_real). Рекурсивный список JSON-путей текущего `config.json` сопоставлен с документом: пропусков нет; повторяющиеся структуры описаны шаблоном `[]`/`{TICKER}`/`{SIDE}`, фактические исключения перечислены (в `targets`: BASE_LONG/BASE_SHORT/VIRTUAL; в `ticker_thresholds`: BTCUSDT/ZECUSDT; в остальных словарях/массивах записи отсутствуют).
