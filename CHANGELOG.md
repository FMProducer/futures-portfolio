CHANGELOG — Prosperous BOT Futures Portfolio

[26 Oct 2026] — Universal Optuna optimizer: persistence, independent configuration, reports and historical dataset selection

- Added `optimization/optimize_universal.py`: one candidate is replayed offline on all eligible local USDT tickers; objectives are maximum median Net from final equity and minimum worst-ticker MaxDD. Candidates require valid accounting, MaxDD <= 50% on every ticker, zero modeled liquidations and no portfolio default. LONG/SHORT share one integer leverage from 3 to 10; shares remain 25/25/50, VIRTUAL leverage 1, and ticker-specific thresholds are cleared only in candidate copies. Capital and minimum notional remain fixed. Independent LONG/SHORT leverage results from early reports are not applicable to this policy.
- Added `optimizer_config.json` with per-parameter `enabled`, fixed `value`, numeric `low`/`high`, optional validated `step`, and boolean FUSE `choices`. Disabled parameters use explicit values. Initial top-eight selection used PED-ANOVA Net importance from the 200-trial one-day/160-ticker report: leverage, share-trail distance, net-move window, trend lookback, surplus threshold, max-drawdown limit, toxic cooldown and trailing activation; together 88.22% of Net importance and 89.68% of MaxDD importance. Users may change the enabled set; importance is sample-local association, not causal evidence.
- Added independent `optimizer_base_config.json` and `--base-config`: initialize a missing snapshot from project `config.json` once, then load the snapshot into memory. Report checkpoints no longer reread or compare the project's config. Changing or deleting the working config cannot abort the search; configuration edits during a run take effect only on the next invocation. Project `config.json` is not written by the optimizer.
- SQLite defaults to `optimization/universal_optuna.db`; compatible restarts add `--n-trials` new trials. `--storage` and `--study-name` are supported. Fingerprints include the base snapshot, parameter settings, historical files/windows/hashes, objectives, constraints and source code. Incompatible explicit studies are rejected; changed automatic fingerprints create separate studies. Resumed samplers are reseeded to avoid repeating seed-42 startup candidates; exact random-generator continuation is not promised. SQLAlchemy sessions/connections are disposed on Windows. Old JSON results are not automatically imported into new studies.
- Added `--data-dir` with recursive USDT Feather discovery restricted to the selected historical dataset, without fallback to project `data/` or exchange downloads. Without the argument, legacy top-level `data/` discovery remains. Duplicate ticker histories select the largest file with deterministic path tie-breaking. Short histories are excluded with reasons; an empty eligible set stops before optimization. OHLCV-only files are supported with source mtime preserved and unknown calendar alignment explicitly recorded; timestamped histories retain minute-continuity validation.
- Reports: `optimization/universal_report_<UTC>.json` plus readable `.md`; full `best_netdd_config_<UTC>.json` in the project root; nine self-contained HTML plots in `optimization/optuna_visuals_<UTC>/` (history, importance, parallel coordinates and contours per objective, plus Pareto with recommendation marker). Recommendation maximizes median Net, then minimizes worst MaxDD, then selects the earliest trial. Nonpositive best Net is flagged; no feasible completed trial means no recommended config. Interrupted runs export available results. Plot failures are recorded without losing core artifacts.
- Historical evidence remains scoped: the equal-leverage 200-trial report `universal_report_20261025T195121232427Z.json` had 182 feasible trials and best median Net -0.1622%, not a profitable universal result. One-day in-sample search, unaligned or undated histories, simulated execution/funding and missing supervisor/swarm dynamics remain limitations. Later liquidation-switch corrections are documented separately below; this optimizer entry does not claim a new margin-model validation.
- Validation by implementation stage: 12 targeted tests after SQLite/artifact work; 13 after parameter configuration; 14 after independent snapshots; latest `python -m unittest tests.test_optimize_universal -q` passed 16 tests, exit 0, including nested dataset isolation with a decoy in project `data/`, SQLite resume, external config mutation/deletion and artifact generation. CLI `--help` and affected-file `git diff --check` passed. Tests use synthetic backtests; no new 1,000-trial historical search, live action, restart, commit or push was performed for this implementation/documentation.
- Current dataset command: `python -m optimization.optimize_universal --days 1 --n-trials 1000 --data-dir "C:\Python\Prosperous_Bot\futures_portfolio\reports\backtest_gate\25753c1d7bf01032000e"`. One day means 1,440 minute bars; exactly 1,500 bars uses `--days 1.0416666666666667`. Documentation: `optimization/README_universal.md`; Obsidian: `Projects/futures-portfolio-optuna-optimizer-2026-10-26.md`, indexed from `Docs/changelog.md`. Documentation recorded against master HEAD `fe10f8330ec149245dd20ad1a84a339953e0765d`; unrelated working-tree changes retained.

[26 Oct 2026] — Liquidation configuration, terminal backtest results and PAPER/LIVE reconciliation

- Investigated the predictive CROSS trigger despite zero thresholds: distance is modeled `(TPV - maintenance_margin) / TPV * 100`, not exchange liquidation-price distance. Historical Obsidian documentation labeled zero as disabled, while the implementation kept an active zero threshold and ignored the post-factum switch in backtest. The specific bar-937 state was not replayed or certified correct.
- Backtest and PAPER now honor independent existing fields in `portfolios[0]`: `liquidation_distance_warn_pct: 0.0` disables warnings, `liquidation_distance_crit_pct: 0.0` disables predictive closing, and `post_factum_liquidation_check: false` disables modeled HARD liquidation. Missing fields retain enabled defaults 15.0/8.0/true; post-factum values must be boolean. Source `config.json` already contained 0/0/false and was not changed. Effective backtest settings are exported in the result.
- Backtest bar-boundary TPV <= 0 now returns `status: "portfolio_default"`, `stopped_bar`, actual equity/Net/MaxDD, event and curve through the stop bar, without an assertion traceback. Ending valuation and processed-window metadata stop there; `requested_bars` retains the original window length. Negative equity is preserved without invented closes. Normal status is `completed`; accounting exceptions remain errors. Universal optimizer rejects defaults as infeasible without weakening MaxDD <= 50% / zero-modeled-liquidation constraints.
- PAPER independently models HARD liquidation when enabled; non-positive raw collateral instead preserves a `portfolio_default` result, wallet/open positions and actual final metrics without replenishment or fictitious fills. Terminal state/wallet use the existing paired funding WAL boundary and require manual reconciliation before restart.
- Shared PAPER/LIVE handler treats zero warning/critical thresholds as disabled. Live CROSS retains its existing independent margin-ratio monitor and always reconciles exchange positions. Missing expected legs persist `reconciliation_required` / `startup_blocked` before further orders; they no longer cause an invented 100% loss or automatic closure of remaining positions. Startup preserves the shadow ledger when expected exchange legs disappear. Supervisor blocks automatic reset/launch/healing for terminal or unreconciled sessions and retains blocked live exposure for operator review.
- Updated `backtest/backtest_rebalance.py`, `core/main.py`, `optimization/optimize_universal.py`, `supervisor/swarm_manager.py`, their affected tests and `docs/CONFIG_REFERENCE.md`. Trailing stops, max-drawdown/funding protections, fixed MMR assumptions and exchange liquidation itself are not disabled by these switches.
- Validation recorded by stage: configuration switches 53 passed; backtest-result/optimizer/CLI checks 55 passed with 9 Optuna warnings; expanded PAPER/LIVE checks 138 passed and 8 failed (exit 1), all eight failures reproduced with original `HEAD:core/main.py` loaded only in memory; final focused PAPER/LIVE checks 27 passed / 100 deselected (exit 0). `git diff --check` passed. This is not an overall clean-suite PASS or economic/runtime validation.
- Tests used installed Python 3.11 with existing `.venv/Lib/site-packages` via process-local `PYTHONPATH` because the venv launcher could not start. No packages installed, commit/push, optimization run or paper/live deployment/restart performed in this chat. Running processes must separately load the changes. Detailed protocol and exact commands: Obsidian `Projects/futures-portfolio-liquidation-config-2026-10-26.md`, indexed from `Docs/changelog.md`. Unrelated working-tree changes were retained.

[26 Oct 2026] — Isolated PAPER terminal allocation return candidate

- Revalidated against current master `c98cac10a420c54013bf5c9fdeffe895b5cf5390` in `codex/paper-capital-return-current`, preserving the newer liquidation/manual-reconciliation guards: 128 pytest checks and 2 closing-child checks passed. Current patch, hashes, read-only snapshots and application plan: `reports/paper_capital_return_fix/current_head_validation/REPORT.md`. Primary runtime sources and budget remain unchanged; runtime acceptance is OPEN.

- Reconcile confirmed terminal allocations independently of PM2 presence before candidate selection; reuse the existing transactional budget return of final equity. Validate session/capital, flat positions, terminal archive/receipts, equity/Net and pending WAL; retain ambiguous allocations with diagnostics.
- Prevent closed session identity resurrection in supervisor start/stop and worker CLI; retain existing cooldown/probation and archive history before a fresh allocation/session.
- Offline validation and exact version evidence: `reports/paper_capital_return_fix/REPORT.md`. Candidate only in `codex/paper-capital-return`; no runtime application or budget mutation. Runtime blocker remains OPEN. CAP reopened its old identity after the recorded close and now conflicts with the terminal archive; no automatic refund/recovery is permitted.
- Final candidate: 121 pytest checks plus 2 closing-child checks passed. An independent later read-only snapshot found an external CAP return of 991.0078 and a new 942.1298 allocation/session; these are not runtime validation of this undeployed candidate. Both versions of the overwritten old terminal archive are retained in evidence.

[26 Oct 2026] — PAPER capacity and stale declaration fix

- Existing incubator derives from actual PM2/pool reservations; declarations no longer bypass admission gate. Persist selected incubator rather than raw scanner fallback; reject excess protected sessions without arbitrary liquidation.
- 59 tests passed; automatic cycles produce10rather than13. Authorized restart retained4active wallets/positions/session identities and budget. Six prior collateral STOP quarantines still reserve capital (cash0): separate OPEN lifecycle-return blocker, no balances reset or quarantine bypassed. Evidence: `reports/paper_capacity_fix_20261025_231604/REPORT.md`.

[25 Oct 2026] — PAPER closing accounting, capital retention and monitoring

- PAPER emergency/trailing/collateral closes now record idempotent PnL/fee receipts and paired state through existing WAL; resume retains wallet/session/opening/ATH, prevents stale terminal markers and respects probation. 145 affected tests passed before the final supervisor safeguard; 53 supervisor tests passed after it.
- User-authorized six-worker PAPER and supervisor restart backed up in `reports/paper_lifecycle_repair/deploy_20261025_141940/`. All session/opening identities retained; TUSDT cycle18 movement reconciled to its new receipt. REAL stays disabled.
- `swarm-observer` captures snapshots/log tails/reported cumulative Net and coverage gaps. Hourly monitoring resumes for24h through26Oct14:25UTC. Initial observer name collided with supervisor PAPER orphan cleanup; renamed and outage retained as evidence. Historical verified Net is still unknown; TUSDT hypothetical closing replay is explicitly separate.
- Future pilot parameters agreed:68USDT,1slot,7x cross,MaxDD50%; exchange/execution/economic/forward gates and separate launch permission remain. Full status: `reports/paper_lifecycle_repair/REPORT.md`.

[25 Oct 2026] — PAPER-only swarm launch and restored swarm dashboard

Пользователь разрешил запуск существующего paper-роя и дашборда; работоспособность интерфейса затем подтверждена пользователем. Это операционная диагностика, не принятие доходности, REAL или forward-gate исследования.

### История и параметры
- Просканированы Obsidian и документы проекта: 348 MD/TXT/HTML/PDF с вложенным checkout, 269 без `.claude`-копии. Найден прямой протокол PM2 от 14 июля в `docs/log.md`; старые схемы разных версий сопоставлены с июльскими записями исправлений. Документы `docs/coordination/` другого checkout не приписаны этому рою.
- Текущий запуск: `paper_mode=true`, `max_bots=10`, `paper_mode_bots=10`; REAL slots=0. `max_bots` — вместимость PAPER, не число REAL. `live_swarm=[]`, whitelist REAL пуст, Telegram выключен.
- `tickers.txt` ограничивает scanner 20 символами; volume>=20M USDT, окно3ч, >=10 proxy cycles при пороге0.5%. Получены4 кандидата: ENAUSDT/ZECUSDT/1000PEPEUSDT/WLDUSDT. Фильтры не расширялись ради заполнения10слотов.
- Каждый бот имеет1000 виртуальных USDT; это не единый ограниченный капитал роя. Пауза supervisor432с плюс длительность цикла.

### Supervisor: изоляция PAPER-only
- Блокер: ноль REAL-слотов не отключал private exchange reconciliation, HEAL/REAL launch, закрытие позиций и Invariant Gate.
- `supervisor/swarm_manager.py`: при PAPER и нуле REAL-слотов исключены exchange connector/reconciliation и REAL process management; конфликтующие REAL процессы/allocations отклоняют цикл до записи. REAL-флаги сохраняются; REAL amnesty не сбрасывает state. Scanner/scoring/PAPER rotation не менялись.
- Проверка: `python -m pytest tests/test_supervisor.py tests/test_supervisor_new.py tests/test_supervisor_service.py tests/test_supervisor_rotation_whitelist.py tests/test_supervisor_whitelist.py --ignore-safety -q --tb=short` — exit0,76 PASS,40.10s. Четыре новых случая проверяют отсутствие exchange/REAL действий и три вида REAL-конфликта. Network/PM2/state paths изолированы mocks/fixtures.

### Фактический запуск
- Пользователь остановил прежний `paper-zec`; после подтверждения в PM2 был только stopped `collect-zec-depth`. Старые ZEC JSON в `core/` уже отсутствовали: backup прежней сессии не создан, причина отсутствия не установлена. Сохранён предзапусковой config; ZEC стартовал новой сессией1000USDT.
- PM2 CLI с явным project `.venv/Scripts/python.exe`, cwd проекта, PYTHONPATH и PYTHONUNBUFFERED. Запущен `supervisor-service`; ecosystem не использован. Aggregator/Telegram sender не запускались.
- Первый цикл завершён25.10.2026 01:46:18 Europe/Kaliningrad (24.10.2026 23:46:18UTC): PAPER ONLY, REAL Swarm=[]. Четыре `paper-*` online с `--paper`, ноль рестартов на первичной проверке. Initial build каждого: cycle1, TPV~998.604 из1000; -1.396USDT — стартовая комиссия, не последующие ребалансировки.
- Supervisor сам заполнил `config.tickers` и `base_ticker` через fallback; live_swarm остался пуст. Ручной настройки экономики не было. Тестовые mock-сообщения до01:46 в общем supervisor.log не считать реальными событиями.
- Evidence: `reports/paper_swarm_launch_20261025_014431/` — REPORT, before/after config, hashes, diff, безопасный PM2 snapshot, state snapshots и startup logs. Начальная PREPARED-фаза отчёта дополнена runtime acceptance.

### Dashboard
- Восстановлен `dashboard/dashboard.py`; в project `.venv` установлены Flask/bcrypt/psutil/PyYAML/waitress. Pip сообщал ошибки metadata части существующих пакетов; общая пересборка среды/pip check не выполнялись.
- Исправлены `core/` state paths, paper/real shadow names, `last_tpv/initial_tpv/last_profit`, `core/logs`, PM2 ticker name, отсутствующий `_read_log_lines` заменён `get_log_tail`. Карточка ticker поддерживает актуальные метрики и числовые позиции. Общие карточки уточнены как REAL TPV/REAL PnL.
- Изменены `dashboard/config.yaml`, `data_collector.py`, `static/ticker.js`, `templates/dashboard.html`; добавлен `tests/test_swarm_dashboard.py`:2 PASS,exit0. `node --check dashboard/static/ticker.js` PASS. Первый тестовый кандидат выявил отсутствующий helper; после исправления повтор прошёл.
- PM2 `swarm-dashboard`, waitress, `--production --host 127.0.0.1 --port 8080`; HTTP http://127.0.0.1:8080 . Успешный запуск через Python subprocess→node→PM2 обошёл потерю разделителя аргументов в PowerShell wrapper. Один намеренный restart только dashboard применил подписи; рой не перезапускался.
- HTTP200 проверены login, authenticated overview, ZEC details/logs, health, static JS; API: supervisor online,4 PAPER,0 REAL, свежие heartbeat. После финальной правки повторены login/overview; пользователь подтвердил UI. Управляющие кнопки не нажимались, screenshot-QA не проводилась. Настройки входа сохранены; секреты сюда не копируются.

### Provenance и границы
- Подготовка supervisor при HEAD `5d98260200997bd940f8b3c772aa0ae8380af2ce`; dashboard при `d43bb74b8fd51908b7a352f0d809629742cada95`. Изменение HEAD не приписывается этим действиям. Чужие изменения не откатывались, новых commit/push не было.
- Обновлён `docs/INTRADAY_RESEARCH_PLAN.md`: старт наблюдения не закрывает этапы2–5; общая аллокация/риск и критерии forward остаются открытыми. Следующая работа — отдельный read-only разбор retention/rotation и сохранности капитала. Scanner бэктестером пока не заменён.
- Полный протокол Obsidian: `D:/Obsidian/MyVault/Hermes/Docs/futures-portfolio-paper-swarm-dashboard-2026-10-25.md`.

[20 Oct 2026 — 15:58] — ZECUSDT Paper Trade: запуск и bugfix сборщика стакана

### paper-zec: запуск
- `.venv` пересоздан (старый был удалён) — системный Python 3.11 + `pip install -r requirements.txt`
- `paper-zec` запущен через PM2 с `--ticker ZECUSDT --paper --config config.json`
- VIRTUAL работает как чистый USDT-кэш (правки из сессии [[2026-10-20-zec-paper-virtual-cash]])

### collect-zec-depth: bugfix
- **Баг**: Binance WebSocket depth stream (`@depth20@100ms`) использует ключи `b`/`a`/`u`, код искал `bids`/`asks`/`lastUpdateId`
- Все снапшоты были пустые (0 bids, N/A spread) несмотря на успешный WebSocket handshake
- **Фикс** (`scripts/collect_depth_zec.py`): `data.get("bids")` → `data.get("b")`, `data.get("asks")` → `data.get("a")`, `data.get("lastUpdateId")` → `data.get("u")`
- Добавлен None-guard для `best_bid`/`best_ask` (первый снапшот может быть до получения данных)

### Команды запуска (зафиксированы)
```powershell
$env:PYTHONPATH = "C:\Python\Prosperous_Bot\futures_portfolio"
# paper-zec
pm2 start core/main.py --name paper-zec --cwd "..." --update-env --interpreter ".venv\Scripts\python.exe" --instances 1 -- --config config.json --ticker ZECUSDT --paper
# collect-zec-depth
pm2 start scripts/collect_depth_zec.py --name collect-zec-depth --no-autorestart --update-env --interpreter ".venv\Scripts\python.exe" --instances 1
```

### Состояние PM2
| id | name | status | uptime | restarts |
|----|------|--------|--------|----------|
| 0 | paper-zec | online | ~25m | 0 |
| 1 | collect-zec-depth | online | ~5m | 2 |

[16 Jul 2026 — 20:30] — Telemetry Pipeline: Bug Fixes & Cleanup (Session 2)
Продолжение отладки pm2_log_parser.py и очистка дубликатов.

### Исправления

1. **cursor.rowcount вместо conn.total_changes** (save_to_db)
   - `conn.total_changes` cumulative — после первого INSERT всегда >0, считал все строки
   - `cursor.rowcount` возвращает 1 для реального INSERT, 0 для IGNOREd дубликата
   - Теперь "Inserted: N" показывает точное количество

2. **Нормализация тикера при фильтрации** (scan_pm2_logs)
   - PM2 файлы используют короткие имена: `aave`, `1000bonk`, `zec`
   - Пользователь передаёт полные имена: `AAVEUSDT`, `1000BONKUSDT`
   - Фикс: `ticker.upper().replace("USDT", "")` перед сравнением
   - Пример: `--ticker AAVEUSDT` теперь находит `real-aave-out-18.log`

### Очистка

- Удалены дубликаты из `D:\Hermes-USB-Portable-main\data\scripts\`:
  - `archive_to_sqlite.py` (копия из futures_portfolio/scripts/)
  - `parse_logs_to_sqlite.py` (obsolete, заменён на pm2_log_parser.py)
- Остальные 5 скриптов (gateway_watchdog, morning_check, nightly_stats, weekly_summary, generate_daily_report) оставлены — cron на паузе

### Verifikation

- pm2_log_parser.py --scan-all: 6726 файлов → 1.27M heartbeats, 86K trades, 55K rebalances, 100K guards
- pm2_log_parser.py --scan-all --ticker 1000BONKUSDT --mode paper: 44 файла
- pm2_log_parser.py --scan-all --ticker AAVEUSDT --mode real: 3 файла
- save_to_db: cursor.rowcount корректно считает INSERT vs IGNORE

### sqlite-telemetry skill обновлён

- Добавлены оба баг-фикса в раздел Pitfalls
- Обновлён статус: "Pipeline fully operational"
- Cron: b0ff3538f9df daily 23:00 — подтверждён

---

[16 Jul 2026 — 16:30] — PM2 Log Parser: Intermediate State Telemetry
Реанимация парсинга PM2 логов для промежуточных данных (heartbeats, trades, rebalances).

### Проблема
archive_to_sqlite.py заполнял только bot_sessions и daily_stats (из history/*.json).
Отсутствовали промежуточные данные: TPV/PnL каждые 1-2 мин, сделки с ценами, номера циклов rebalance.
Для анализа drawdown, trailing stop, velocity guard необходимы промежуточные точки.

### Расследование
PM2 логи (C:\Users\svsma\.pm2\logs\) содержат:
- real-{ticker}-out-{pid}.log —活ные боты (ZECUSDT и др.)
- paper-{ticker}-out-{pid}.log — paper боты

Формат heartbeat: `Heartbeat: TPV=16.77 | PnL=-3.23 | ZECUSDT=547.98 | L:47.9% [-2.1%] {+8.03$} | S:48.8% [-1.2%] {+8.19$}`
Формат trade: `📝 REAL: SELL 0.016 ZECUSDT_SHORT @ 557.124`
Формат rebalance: `Rebalance #31 complete. TPV: 18.98`

### Решение: pm2_log_parser.py
Новый скрипт (внешний, не инвазивный):
- Парсит 6726 PM2 лог-файлов (real + paper)
- Извлекает: heartbeats, rebalances, trades, guards
- Заполняет 4 новые таблицы в trading_stats.db
- CLI: --scan-all, --dry-run, --ticker, --mode
- Путь: C:\Python\Prosperous_Bot\futures_portfolio\scripts\pm2_log_parser.py

### Баг fix: group index mismatch
Проблема: TRADE_PATTERN группы 5-7 (ticker, pos_type, price) использовались неверно.
- group(5) = ticker, но код использовал как pos_type
- group(6) = pos_type, но код использовал как price → float('LONG') → ValueError
- Все 86K trades молча пропускались (try/except continue)

Fix: group(6) → pos_type, group(7) → price

### Результаты заполнения
- heartbeats: 1,270,841 rows (2026-05-18 → 2026-07-16)
- rebalances: 55,192 rows
- pm2_trades: 84,138 rows (paper: 77,085, real: 7,053 across 184 tickers)
- guards: 100,526 rows

### Cron: ежедневный pipeline
Job ID: b0ff3538f9df
Расписание: 23:00 ежедневно
Что делает:
1. archive_to_sqlite.py --scan-all (сессии из archive JSON)
2. pm2_log_parser.py --scan-all (heartbeats, trades, rebalances из PM2 логов)
3. Сводка по таблицам

### Структура БД (итого)
| Таблица | Rows | Источник |
|---------|------|----------|
| daily_stats | 79 | archive_to_sqlite.py |
| bot_sessions | 196 | archive_to_sqlite.py |
| trades | 5,509 | parse_logs_to_sqlite.py (старый, мёртвый) |
| heartbeats | 1,270,841 | pm2_log_parser.py |
| rebalances | 55,192 | pm2_log_parser.py |
| pm2_trades | 84,138 | pm2_log_parser.py |
| guards | 100,526 | pm2_log_parser.py |

### Файлы
- scripts/pm2_log_parser.py — новый скрипт (11.8 KB)
- scripts/archive_to_sqlite.py — ранее создан (10.5 KB)
- trading_stats.db — SQLite база (обновлена)

### Запуск
```bash
cd C:\Python\Prosperous_Bot\futures_portfolio
python scripts/pm2_log_parser.py --scan-all
python scripts/pm2_log_parser.py --scan-all --dry-run --ticker ZECUSDT --mode real
```

### Статус
- Система не затронута, боты работают
- Скрипт работает автономно, без зависимости от ботов
- Старая таблица trades (5509 rows) сохранена (stale данные из мёртвого парсера)

---


[16 Jul 2026 — 15:30] — SQLite Telemetry: External Analytics Pipeline
Расследование и восстановление системы телеметрии для анализа торговой истории.

### Проблема
Skill sqlite-telemetry ссылался на trading_stats.db (5509 trades, 79 daily_stats), но:
- Скрипт parse_logs_to_sqlite.py (D:\Hermes-USB-Portable-main\data\scripts\) был мёртв
- Cron job "Nightly Stats" на паузе с 11 июня 2026
- Формат логов изменился (real_*.log → aggregator.log/supervisor.log)
- Парсер依赖 от regex для BUY/SELL строк, которых больше нет в новых логах
- Данные в БД: только 2 дня (3-4 июня), stale 42 дня

### Расследование: откуда 5509 записей
- Источник: parse_logs_to_sqlite.py парсил PM2 логи (real_*.log, paper_*.log)
- Cron: nightly_stats.py → parse_logs_to_sqlite.py → generate_daily_report.py
- Расписание: 0 22 * * * (22:00 ежедневно)
- Последний запуск: 10 июня 2026
- Причина мёртвости: формат логов изменился, парсер依赖 от старого формата

### Решение: archive_to_sqlite.py
Новый скрипт (внешний, не инвазивный):
- Читает history/*.json (129 archive файлов: 21 real + 54 paper_state)
- Заполняет bot_sessions и daily_stats в trading_stats.db
- Idempotent (INSERT OR REPLACE, проверка дубликатов)
- CLI: --scan-all, --dry-run, --db, --history
- Путь: C:\Python\Prosperous_Bot\futures_portfolio\scripts\archive_to_sqlite.py

### Результаты заполнения
- Real: 29 сессий, total PnL: +139.75 USDT
- Paper: 167 сессий, total PnL: +86.27 USDT
- Profit Factor: 79.95
- Win Rate: 89.7% (26/29 real sessions)

### Аналитический вывод (5 запросов)

1. **Тикеры по PnL**: EPICUSDT (+85.13) — основной драйвер, DODOXUSDT (+12.93) — стабильный (7/7 wins)
2. **Trailing Stop vs Completed**: completed даёт x6 больше PnL (avg +11.66 vs +1.93)
3. **Оптимальная длительность**: 2-3 часа (100% win), после 10ч — loss
4. **Paper vs Real correlation**: 10/15 тикеров совпадают по знаку (paper предсказывает направление)
5. **Win Rate**: 89.7%, Profit Factor 79.95, Net PnL +139.75 USDT

### Перенос в проект
- archive_to_sqlite.py → C:\Python\Prosperous_Bot\futures_portfolio\scripts\
- trading_stats.db → C:\Python\Prosperous_Bot\futures_portfolio\trading_stats.db
- .gitignore обновлён: trading_stats.db, *.db-journal, *.db-wal, *.db-shm
- Старый скрипт в D:\Hermes-USB-Portable-main\data\scripts\ оставлен (не удалён)

### Файлы
- scripts/archive_to_sqlite.py — новый скрипт (10.5 KB)
- trading_stats.db — SQLite база (479 KB)
- .gitignore — обновлён

### Запуск
```bash
cd C:\Python\Prosperous_Bot\futures_portfolio
python scripts/archive_to_sqlite.py --scan-all
python scripts/archive_to_sqlite.py --scan-all --dry-run
```

### Статус
- Система не затронута, боты работают
- Cron jobs (nightly_stats) — на паузе, не возобновлялись
- Скрипт работает автономно, без зависимости от ботов

---

CHANGELOG — Prosperous BOT Futures Portfolio

[16 Jul 2026 — 12:45] — CRITICAL FIX: Revert timestamp_offset (-1021)
Проблема: реальные боты (real-tlm, real-ldo) получали APIError(code=-1021) — Timestamp ahead. Сделки прекратились.
Причина: timestamp_offset вычислялся через сырой requests.get() с системным прокси в РФ — невалидный offset.
Решение:
- Удалён timestamp_offset из connector.py
- Оставлен recvWindow=30000 (протестирован months)
- -1021 добавлен в retryable ошибки (транзиентная)
- Tests: 328/328 pass (+1 retry test)

### 16 July 2026 — Skills Stack: 6 Custom Skills Created
1. **binance-api-ref** — Binance Futures API: эндпоинты, ошибки, rate limits, clock sync
2. **pm2-workflow** — PM2: restart, logs, ecosystem, safety, диагностика
3. **sqlite-telemetry** — SQL-запросы к trading_stats.db: PnL, win rate, drawdown
4. **futures-trading-debugging** — отладка live-проблем: смерть бота, дрифт позиций
5. **optuna-optimization** — Optuna: objective function, Sortino, multi-ticker
6. **connector-patterns** — python-binance: async wrappers, retry, hedge mode, gotchas
- Все заточены под стек проекта, содержат pitfall'ы из реального опыта
- Skills.txt обновлён: 90 → 96 скиллов

[14 Jul 2026 — 23:00] — System Portability & Deployment Guide
Описание: Система самодостаточна, имеет удалённый репозиторий, пригодна для переноса на другой ПК.

Удалённый репозиторий:
- URL: https://github.com/FMProducer/futures-portfolio.git (private)
- Branch: master
- 125 файлов отслеживается

Что в git (самодостаточность):
1. core/ —.connector, executor, calculator, main, notifier, storage (+ __init__.py)
2. supervisor/ — aggregator, service, rotation, whitelist, swarm
3. monitoring/ — send_monitor_report
4. rank_tickers.py — сканер тикеров
5. backtest_rebalance.py — бэктест движок
6. tests/ — 327 unit-тестов
7. requirements.txt — 14 пакетов с == версиями
8. pyproject.toml — метаданные + зависимости
9. ecosystem.config.js — PM2 конфиг (paper/real боты + supervisor + aggregator + telegram)
10. .env.example — шаблон переменных окружения
11. config.json — конфигурация (порты, тикеры, параметры стратегии)
12. CLAUDE.md, CHANGELOG.md — документация
13. docs/log.md — история инцидентов

Что НЕ в git (.gitignore):
- .env — секреты API ключей
- core/*.json — runtime state (paper_state, real_state, shadow)
- logs/ — лог-файлы
- signals/ — runtime флаги
- .venv/ — виртуальное окружение
- history/ — архив состояний

Инструкция переноса на другой ПК:
Шаг 1 — Клонирование:
  git clone https://github.com/FMProducer/futures-portfolio.git
  cd futures_portfolio

Шаг 2 — Python окружение:
  python -m venv .venv
  .venv\Scripts\activate          (Windows)
  source .venv/bin/activate       (Linux/Mac)
  pip install -r requirements.txt

Шаг 3 — Конфигурация:
  copy .env.example .env
  # Заполнить: BINANCE_API_KEY, BINANCE_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  # При необходимости: TELEGRAM_API_BASE (для прокси к Telegram)

Шаг 4 — PM2 (Windows/Linux):
  npm install -g pm2
  pm2 start ecosystem.config.js
  pm2 save

Шаг 5 — Проверка:
  python -m pytest tests/ --ignore-safety   (327/327)
  pm2 list                                  (все сервисы online)

Зависимости (не требуют ручной установки):
- Python 3.11+ (проверено на 3.11.9)
- Node.js (для PM2)
- Binance Futures API (прямое подключение, без VPN/прокси)
- Telegram Bot API (при необходимости через TELEGRAM_API_BASE прокси)

Известные ограничения при переносе:
- config.json содержит абсолютные пути (C:\Python\Prosperous_Bot\...) — нужно поправить под новый ПК
- ecosystem.config.js указывает на .venv в текущей директории — работает если venv создан локально
- Clock drift на новом ПК может отличаться — NTP sync в connector.py компенсирует автоматически
- PM2 на Linux/macOS: команда pm2 запускается без .cmd суффикса

Изменённые файлы: CHANGELOG.md, Docs/changelog.md (Obsidian)

---

[14 Jul 2026 — 22:23] — Clock Sync & recvWindow Hardening (Binance -1021 fix)
Problem: swarm-aggregator получал APIError(code=-1021): Timestamp for this request is outside of the recvWindow. REQUEST_RECVWINDOW=15000 не покрывал clock drift (-7s стабильный, но мог скакнуть за пределы окна). Дополнительно: Client(ping=True) тратил 15s на таймаут spot ping (api1.binance.com заблокирован в РФ).

Root cause (3 проблемы):
1. REQUEST_RECVWINDOW=15000 insufficient — clock drift мог превысить 15s
2. Client(ping=True) — spot ping к api1.binance.com таймаутился 15s перед каждым созданием клиента
3. verify_connection() вызывал self.futures_client.ping() — spot endpoint, а не futures

Fix:
1. REQUEST_RECVWINDOW increased: 15000 → 30000 (covers drift up to ±30s)
2. NTP clock sync: timestamp_offset = server_time - local_time (eliminates drift entirely)
3. Client(ping=False) — bypass spot ping timeout
4. verify_connection() → futures_ping() instead of ping() (correct endpoint)
5. Tests: updated 5 assertions in test_connector.py for ping=False, futures_ping, REQUEST_RECVWINDOW=30000

Result: 327/327 tests passed. -1021 errors eliminated. Clock sync stable at ~6960ms offset.
Changed files: core/connector.py, tests/test_connector.py

---

[14 Jul 2026 — 21:50] — Environment Stabilization & Dependency Pinning
Problem: После пересоздания .venv (Python 3.11.9) — 10 из 327 тестов падали, PM2 не находил интерпретатор, ecosystem.config.js указывал на несуществующий venv.

Root cause (4 проблемы):
1. 5 тестов test_backtest_rebalance.py — ImportError: No module named 'pyarrow' (pyarrow не установлен)
2. 5 тестов test_send_monitor_report.py — TypeError: an integer is required (load_dotenv() на уровне модуля конфликтует с os.stat mock в тестах)
3. ecosystem.config.js — путь к venv указывал на '../.venv' (Prosperous_Bot/.venv), а venv пересоздан в futures_portfolio/.venv
4. requirements.txt — не зафиксированы версии, pin-to-past не работал

Fix:
1. Установлен pyarrow==23.0.1 (<24 — без feather deprecation warnings)
2. Добавлен patch("dotenv.load_dotenv") в 4 теста test_send_monitor_report.py — предотвращает вызов load_dotenv при повторном импорте модуля
3. ecosystem.config.js: path.join(__dirname, '..', '.venv') → path.join(__dirname, '.venv')
4. requirements.txt — все 14 пакетов зафиксированы с == версиями
5. pyproject.toml — dependencies и dev-dependencies зафиксированы с == версиями, pyarrow добавлен

Result: 327/327 passed, 0 warnings. PM2 запускает все 3 сервиса.

Changed files: requirements.txt, pyproject.toml, ecosystem.config.js, tests/test_send_monitor_report.py

---

[14 Jul 2026 — 21:30] — Binance API recvWindow Fix (clock drift compensation)
Problem: Aggregator получал APIError(code=-1021): Timestamp for this request is outside of the recvWindow. Clock drift -7.6s превышал default recvWindow=10000ms. connector.py передавал recvWindow через requests_params → TypeError: Session.request() got an unexpected keyword argument 'recvWindow'.

Root cause: recvWindow — параметр Binance API, а не requests.Session.request(). python-binance Client принимает recvWindow через атрибут REQUEST_RECVWINDOW, не через requests_params.

Fix:
- connector.py: добавлен client.REQUEST_RECVWINDOW = 15000 после создания Client (компенсирует clock drift до 15s)
- tests/test_connector.py: reverted requests_params assertions обратно к оригиналу

Result: -1021 ошибки ушли после рестарта PM2. 327/327 tests passed.

Changed files: core/connector.py, tests/test_connector.py

---

[14 Jul 2026] — Этап 3: Модульная структура (Multi-Repo Split)
Problem: pytest с CWD=futures_portfolio/ падал с ModuleNotFoundError: No module named 'futures_portfolio'. 22 из 327 тестов не собирались. Директория запуска влияла на результат — из родителя OK, изнутри пакета FAIL.

Root cause (две проблемы):
1. pyproject.toml: pythonpath = ["."] добавлял futures_portfolio/ в sys.path — Python видел core/, supervisor/ напрямую, но не futures_portfolio.core.*
2. __init__.py (6 файлов): _project_root = dirname(dirname(__file__)) указывал на futures_portfolio/ (сам пакет) вместо его родителя. Legacy-артефакт от плоской структуры до Этапа 3.

Fix:
- pyproject.toml: pythonpath = ["."] → pythonpath = [".."] — добавляет родительскую директорию в sys.path
- core/__init__.py, supervisor/__init__.py, backtest/__init__.py, scanner/__init__.py, monitoring/__init__.py, optimization/__init__.py: добавлен третий dirname() — _project_root теперь указывает на C:\Python\Prosperous_Bot

Result: 327/327 collected из обеих директорий. futures_portfolio автономен — можно запускать pytest из корня пакета.

Changed files: pyproject.toml, core/__init__.py, supervisor/__init__.py, backtest/__init__.py, scanner/__init__.py, monitoring/__init__.py, optimization/__init__.py

[14 Jul 2026 — 17:05] — State File Path Deduplication (SSOT for core/)
Problem: State-файлы (paper_state_*, paper_shadow_*, real_state_*, shadow_state_*) дублировались в корне futures_portfolio/ и в core/. Swarm_manager.py использовал BASE_PATH (корень) для чтения/записи, а main.py — os.path.dirname(__file__) (core/). Итого: 41 stale-файл в корне при 17 live-ботах.

Root cause: CORE_PATH не существовал. Все 10 ссылок в swarm_manager.py на state-файлы шли через BASE_PATH.

Fix:
- Добавлен CORE_PATH = BASE_PATH / "core" (swarm_manager.py:24)
- Переключены 10 ссылок: enforce_swarm_consistency, reset_bot_state_files, start_bot, get_bot_efficiency, amnesty-блок, get_running_bots_info
- 41 stale-файл перемещён из корня в history/
- Тесты: patched CORE_PATH в test_supervisor.py (1) + test_supervisor_coverage.py (6)

Result: 327/327 passed. State-файлы существуют ТОЛЬКО в core/. Источник дублирования устранён.

[14 Jul 2026 — 16:42] — Supervisor PM2 Log Spam Elimination
Problem: manage_swarm() запускает ~19 ботов за цикл. Каждый pm2 start/delete/save без stdout-редиректа выводил PM2 box-drawing таблицу в лог супервайзера. Итого ~400 строк мусора за один цикл.

Root cause: 6 точек в swarm_manager.py использовали create_subprocess_shell() без stdout=DEVNULL:
1. start_bot() — pm2 start (главный источник: 19 таблиц)
2. stop_bot() — pm2 delete
3. enforce_swarm_consistency → cmd_stop — закрытие stray-позиций
4. manage_swarm → cmd_stop #1 — Authoritative Cleanup
5. manage_swarm → cmd_stop #2 — Stopping Combat REAL
6. pm2 save — сохранение дампа

Fix: Все 6 вызовов дополнены stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL.
get_running_bots_info() и get_pm2_processes() оставлены с PIPE — они читают данные из pm2 jlist.

Result: 327/327 tests passed. Лог супервайзера теперь чистый — только бизнес-события.

[14 Jul 2026] — Этап 3: Модульная структура (Multi-Repo Split)
Problem: Плоская структура ~30 .py файлов в корне futures_portfolio/. Непрозрачная навигация, конфликт имён supervisor.py/supervisor/ на Windows, теневой src/ в .venv.

Solution: Модульная структура с поддиректориями core/, supervisor/, scanner/, optimization/, backtest/, monitoring/, tools/, scripts/.

Changes:
- supervisor.py → supervisor/swarm_manager.py (разрешение конфликта имён)
- src/ → core/ (устранение теневого src/ в .venv)
- Все import paths переведены на пакетный стиль (futures_portfolio.core.*, futures_portfolio.supervisor.*)
- Удалены sys.path.insert хаки из production-кода
- ecosystem.config.js: script paths обновлены (supervisor/supervisor_service.py, supervisor/aggregator.py, monitoring/telegram_sender.py)
- swarm_manager.py: start_bot запускает core/main.py (было main.py)
- dashboard/dashboard.py: PM2 команда обновлена на core/main.py
- BASE_PATH в swarm_manager.py: parent.parent (было parent)
- PROJECT_DIR в optimization/*.py: parent.parent (было parent)
- data_dir в backtest/*.py: dirname(dirname(...)) (было dirname(...))
- Все mock paths в тестах обновлены
- 24 тестовых файла, 318/320 passed

Status: Этап 3 завершён. Система готова к перезапуску с новой структурой. 327/327 passed.

[14 Jul 2026] — Баг-фиксы Этапа 3: stderr crash + 3 failing tests
Problem 1: Python 3.13 + pytest крашится на "lost sys.stderr" при сборке всех тестов.
Root cause: supervisor_service.py и aggregator.py перехватывали sys.stderr на module level.
Fix: sys.stderr/stdout перехват перенесён в if __name__ == "__main__".

Problem 2: test_generate_swarm_section — assert 0.0 == 10.0.
Root cause: pytest создаёт два отдельных module object (supervisor.aggregator и futures_portfolio.supervisor.aggregator). Mock патчит не тот.
Fix: patch paths изменены с futures_portfolio.supervisor.aggregator → supervisor.aggregator.

Problem 3: B1 тесты (test_b1_stop_bot_called, test_b1_ts_flag_reset) — stop_bot не вызывается.
Root cause: tracking_open mock перехватывал только WRITE операции. READ падал на original_builtin_open → FileNotFoundError → B1 ветка недостижима.
Fix: tracking_open перехватывает READ для real_state_* файлов, возвращает StringIO с mock данными.

Problem 4: test_supervisor_new.py — assert "pm2 start main.py" не найден.
Root cause: start_bot в swarm_manager.py обновлён на core/main.py, тест не обновлён.
Fix: assertion обновлён на "pm2 start core/main.py".

Result: 327 passed, 0 failed, 0 errors.

[14 Jul 2026] — PYTHONPATH для subprocess в swarm_manager.py
Problem: enforce_swarm_consistency вызывает core/main.py через subprocess.
Из temp-директории pytest модуль core/ не найден (ModuleNotFoundError).
В production PM2 --cwd обеспечивает правильный CWD, но subprocess脆弱ен.
Fix: все 3 subprocess-вызова теперь передают env с PYTHONPATH=BASE_PATH.
Result: 327/327, ModuleNotFoundError исчез.

[11 Jul 2026] — B6/B3 SSOT Refactor: Race Condition + Exit/Stop Isolation
Problem: main.py directly writes to config.json in _handle_liquidation_recovery and _handle_liquidation_guard (non-atomic write_text). Parallel processes (main.py + supervisor.py) cause race condition → data loss (toxic records, live_swarm changes). Additionally, supervisor.py treats exit and stop signals identically — profitable tickers get permanently blacklisted instead of probation.

Root cause: Violation of SSOT invariant (only supervisor.py should write config.json). No signal type differentiation in supervisor signal processing loop.

Solution: Three-layer refactoring:
- B6: Removed all config.json write blocks from main.py. Worker → Read-Only + Signal (emit_signal). Supervisor = single mutator.
- B3: Signal routing — stop → toxic_blacklist + black_list (toxic outcome), exit → probation_* (profitable outcome, soft cooldown).
- P1: black_list/live_swarm converted to set (O(1) lookup) on load, back to sorted list on save.
- P2: flag_file.unlink() → asyncio.to_thread (non-blocking I/O in event loop).
- P3: trailing_stop_paper_timeout_end from worker state replaced by probation_* in config.json (supervisor as single cooldown controller).

Changes:
main.py:160-179 — removed config.json write block from _handle_liquidation_recovery
main.py:267-297 — removed config.json write block from _handle_liquidation_guard
supervisor.py:565-640 — rewritten signal processing (stop→toxic+blacklist, exit→probation, async unlink)
supervisor.py:555-559 — P1: list→set conversion on config load
supervisor.py:1022-1025 — P1: set→sorted list on config save
supervisor.py:814-818 — P3: trailing_stop_paper_timeout_end → probation_* check
supervisor.py:147-157 — stop_bot: bare except:pass → specific exception handling (FileNotFoundError, OSError)
supervisor.py:232-234 — enforce_swarm_consistency: append → add (set compatibility)
supervisor.py:656-660 — probation pruning (expired entries cleanup)
config.json:110-111 — probation_paper and probation_real keys
tests/test_supervisor.py — 8 new tests + 1 updated (B3 routing, P1 set, P2 async, P3 probation)
tests/test_main.py — 2 new tests (B6 config write verification)

Test results: 30/30 passed (27 supervisor + 3 main). pre-existing timeout in test_rebalance_loop_trailing_stop (unrelated).
Status: SSOT pattern enforced. Race condition eliminated. Exit/Stop isolation restored.

[21 Jul 2026] — B1 Fix: TS Flag Persistence (zombie process kill)
Problem: After trailing stop triggers and supervisor restarts, the flag trailing_stop_triggered=True persists in real_state JSON. In enforce_swarm_consistency(), HEAL REJECTED path resets TS flags BEFORE Reaper Guard runs. Reaper Guard checks state file — flags already False — doesn't kill the zombie. Result: zombie PM2 process sleeps forever (while True: asyncio.sleep(86400)), never cleaned up.

Root cause: Call order in supervisor.py — enforce_swarm_consistency (line 559) runs BEFORE Reaper Guard (line 622+). HEAL REJECTED resets flags at line 200, Reaper Guard at line 635 can't see them anymore.

Solution: Added await stop_bot(ticker, is_paper=False) in HEAL REJECTED path (supervisor.py:197-199) — kills zombie PM2 process via pm2 delete BEFORE resetting TS flags. Atomic 1-line fix.

Changes:
supervisor.py:197-199 — +await stop_bot(ticker, is_paper=False) in HEAL REJECTED block
tests/test_supervisor.py — +2 unit tests (test_b1_stop_bot_called_before_ts_flag_reset, test_b1_ts_flag_reset_after_stop_bot)

Test results: 19/19 passed (17 existing + 2 new B1 tests)
Status: B1 Score 8.3 → ✅ ИСПРАВЛЕН. Активируется при включении TS.

[21 Jul 2026] — Documentation sync (ROADMAP, STATUS, PROJECT_INDEX)
Problem: ROADMAP.md described "1 REAL bot (INJUSDT)" — actual: 4 bots (GRASS, UNI, VVV, YFI). STATUS.md had stale tickers, PROJECT_INDEX.md had wrong config values.

Solution: Updated all three documents to match actual config.json state (SSOT).

Changes:
ROADMAP.md — Текущее состояние: 4 REAL bots, 20 USDT/bot, ~7 min interval, TS OFF
STATUS.md — Live Swarm: GRASS/UNI/VVV/YFI, TS=0.001% (OFF), guards=6 active
PROJECT_INDEX.md — Config params, tickers, changelog entries

[13 Jul 2026] — Test Coverage Phase 1: Prod Scripts ≥89%
Problem: Test coverage at 34.2% overall. 3 FAIL tests blocking progress. rank_tickers.py at 22%, telegram_sender.py at 81%, send_monitor_report.py at 0%.

Solution: Fix 3 FAIL tests, then expand coverage for all prod-critical scripts.

Phase 0 (prior session): 15 dead test files removed, capsys bugs fixed, bounded_sleep→async (6 instances), pyproject.toml coverage omit updated.

Phase 1 (this session):
- Fixed test_coverage_paper_cross_margin_check (infinite loop via bounded_sleep continue@L827)
- Fixed test_coverage_liquidation_guard_missing_long: get_positions mock returned qty as string "-1.0" instead of float -1.0 (TypeError → exception → guard never reached)
- Fixed test_coverage_emergency_stop_full: saves filter "state.json" in s[0] matched "paper_state.json" (substring) → state_save was paper_state dict without virt_qty
- telegram_sender.py: 81% → 100% (+5 tests: rate limit invalid JSON, timeout, connection error, missing credentials, empty queue, send failure, success path, API error)
- rank_tickers.py: 22% → 100% (+17 tests: retry decorator exhaustion, TickerRanker init/metrics/momentum, run_ranker_task, fetch non-200/exception, fetch_klines too few/empty, get_top_tickers blacklist/whitelist/volume/non-ascii/empty_dfs, main quiet/not-quiet/with-config)
- send_monitor_report.py: 0% → 100% (+6 tests: no state files, paper+real, guard active, old heartbeat, sends request, no alerts)

Test results: 333 collected, 331 passed, 2 pre-existing FAIL (test_supervisor B1 TS flag — unrelated).
Coverage: Prod scripts 93% (2823 stmts, 195 miss). All 14 prod scripts ≥89%.

[21 Jul 2026] — Hermes Agent upgrade 0.17.0 → 0.18.2
Portable USB venv (D:\Hermes-USB-Portable-main\data\hermes-agent\venv) upgraded.
Config migrated: v24 → v33.
Key changes: stream-stale circuit breaker, PTY session management, approval gate fixes.

[11 Jul 2026] — Per-ticker min_notional + 2% buffer
Problem: Global min_notional_usdt: 6.1 blocked GRASSUSDT (Binance min=5.03) and VVVUSDT (5.05) — they couldn't rebalance at 3-4% deviations. Config set a single threshold for all tickers, though Binance minimums differ per ticker.

Solution: Added per-ticker min_notional lookup from exchange_info with 2% buffer for dynamic Binance minimum changes.

Changes:
main.py:489 — min_notionals extraction (Binance Futures: f.get("notional"), SPOT: f.get("minNotional"))
main.py:941-945 — active_min_notional = max(config_min, exchange_min * 1.02)
config.json — min_notional_usdt: 6.1 → 5.1
tests/test_main.py — 6 unit tests (TestMinNotionalExtraction, TestEffectiveMinNotional)

Effective minimums:
GRASSUSDT: max(5.1, 5.03*1.02) = 5.13
VVVUSDT: max(5.1, 5.05*1.02) = 5.15
YFIUSDT: max(5.1, 7.00*1.02) = 7.14
UNIUSDT: max(5.1, 7.05*1.02) = 7.19

Fallback (no exchange info): config = 5.1 (no buffer)

Bug fix: KeyError 'minNotional' — Binance Futures API uses 'notional' field, not 'minNotional'. Fixed with f.get("minNotional") or f.get("notional").

[Unreleased]
Net Move Guard (NMG) — Pump/Dump Protection
Problem: During fast unidirectional price movements (pump/dump), the hedge legs move in opposite directions. LONG surplus looks like profit, SHORT deficit looks like loss. Without protection, the bot sells LONG "profit" and buys SHORT "loss" — then the price reverts and the bot locked in a loss.

Solution: Added Net Move Guard — if price moves >1.5% in one direction within 30 seconds, ALL rebalance actions are blocked. The bot waits for stabilization before resuming.

Changes:

main.py — New guard in heartbeat loop (after Trend Guard, before Spread Guard):

Tracks price history with 300s window (existing deque)
Compares current price to price 30s ago
If move > 1.5% → 🛡️ Net Move Guard + continue (skip heartbeat)
Log throttled: every 5th tick only
config.json — New parameters in safety_guards:

net_move_block_pct: 1.5 (percent, triggers block)
net_move_window_sec: 30 (lookback window)
calculator.py — force_block parameter added:

calculate_rebalance() and calculate_deviations() accept force_block: bool
When True, returns empty actions but preserves TPV/metrics
Used by NMG for clean separation (guard in loop, block in calculator)
Protection layers for pump/dump:

Velocity Guard: >1% in 60s → block (existing)
Trend Guard: >0.5% with >85% efficiency → block (existing)
Net Move Guard: >1.5% in 30s → block (NEW)
PnL Guard: hedge PnL < 0 → no surplus selling (previous)
Combined: covers both fast spikes and sustained trends
PnL Guard — Surplus Sell Block in Drawdown
Problem: When hedge portfolio is in drawdown (LONG PnL + SHORT PnL < 0), surplus selling on one side locks in profits while losses accumulate on the other side. Portfolio value melts.

Solution: Added allow_surplus_sell parameter to calculator. When hedge_pnl < 0, all surplus actions are blocked. Only deficit buying (if cash available) continues.

Changes:

calculator.py

calculate_rebalance(): new param allow_surplus_sell (default True)
calculate_deviations(): new param allow_surplus_sell (default True)
Phase 1 (surplus processing): if allow_surplus_sell=False, skip all surplus actions with 🛡️ PnL GUARD log
main.py

Before calculate_rebalance(): compute hedge_pnl = pnl_l + pnl_s from calculator instance attributes
Pass allow_surplus_sell = hedge_pnl >= 0.0 to calculator
Log active guard: 🛡️ PnL GUARD active: hedge PnL=X.XX < 0, surplus selling blocked
main.py — Anti-spam fix

When VIRTUAL share=0 (disabled), skip "Initialized Virtual: 0.0 units" spam on every heartbeat
Only log initialization when VIRTUAL share > 0
VIRTUAL OFF — Switch to Pure Long/Short Hedge
Previous changes (same session):

Problem: VIRTUAL leg (35% of TPV) caused parasitic rebalancing. During strong price movements, VIRTUAL surplus sold while LONG/SHORT deficits accumulated. VIRTUAL proceeds went to cash reserve, not helping hedge balance.

Solution: Disabled VIRTUAL entirely. Switched to 50/50 LONG/SHORT pure hedge.

Changes:

config.json

BASE_LONG.share: 0.29 → 0.50
BASE_SHORT.share: 0.36 → 0.50
VIRTUAL.share: 0.35 → 0.0
supervisor_interval_days: 0.011 → 0.001 (~90s)
toxic_blacklist: cleared (PORTALUSDT expired entry removed)
tickers: expanded from 17 → 24 (added HBAR, MEME, NEAR, ONDO, RENDER, STG, TON, VIRTUAL, WLD, XMR; removed PENDLE)
supervisor.py — Rotation logic rewrite

Old: to_keep = scored_old[:10] + to_add = new[:10] → final could be <20 when scanner overlap was high
New: unified score-driven selection from ALL candidates (old + new), new capped at max_replace, sorted by score descending
Old bots with positive score always prioritized over new (score 0)
Result: always fills to max_bots when scanner has candidates
calculator.py (previous session, already applied)

available_funds = max(Decimal('0'), self.val_cash) — bot cash reserve only, NOT exchange margin
FUSE for deficit purchases DISABLED — no wrong-side-of-threshold blocking
New parameters:

supervisor_interval_days: 0.001 → 0.02 (~30 min, was ~90s)
min_cycles_for_rank: 10 → 3 (reduced from default, allows rotation after 3 cycles)
Prevented:

config["tickers"] is no longer overwritten by supervisor.py on each cycle
config["base_ticker"] is no longer overwritten by supervisor.py
These values are now set manually in config.json only
[18 Apr 2026]
Security & Safety Update
API keys moved to env vars (BINANCE_API_KEY, BINANCE_SECRET_KEY)
Margin ratio monitoring added (warning ≤5x, critical ≤2x)
Hysteresis fix: reference_tpd as fixed baseline
Added .env.example and .gitignore
[13 Jul 2026] — Coverage Push: supervisor.py 66%→95%, main.py 65%→77%
Problem: Test coverage below 85% fail_under threshold. supervisor.py at 66%, main.py at 65%.

Root cause: integration tests for manage_swarm() not covering signal processing, rotation, scoring, and reaper logic. main.py tests not covering trailing stop closure, emergency stop function, liquidation guard, and blacklist rebase paths.

Solution: Two-phase coverage push targeting highest-impact uncovered blocks.

Phase 1 — supervisor.py (66% → 95%):
- Rewrote all manage_swarm() integration tests with proper CONFIG_PATH monkeypatch (module-level constant at line 52 loads config from disk, not from test locals)
- Fixed 5 failing tests: replaced_unprofitable (drawdown protection via inf score), real_stop_drops_bot (whitelist filter), authoritative_cleanup_stray (early return on empty scanner), safety_trim (whitelist guard)
- Added tests for: signal processing (stop→toxic, exit→probation), scoring pipeline, rotation with candidates, reaper guard, amnesty logic

Phase 2 — main.py (65% → 77%):
- Created test_main_coverage.py with 21 tests targeting: trailing stop trigger + closure (lines 978-1067), emergency stop / max drawdown (931-957), liquidation guard (1421-1460), VIRTUAL_ORDER accounting (1191-1233), stop/close logic (1525-1601), paper cross-margin check (814-827), blacklist rebase (598-625), emergency_stop function (1561-1601), _handle_liquidation_recovery (130-148)
- Key pattern: rebalance_loop() creates its own BinanceConnector internally but accepts connector param; PortfolioCalculator must be mocked to control tpv_total for trailing stop / drawdown activation

Changes:
tests/test_supervisor.py — 18 integration tests added (manage_swarm signal processing, scoring, rotation, reaper, amnesty)
tests/test_main_coverage.py — 21 tests added (trailing stop, emergency stop, liquidation guard, VIRTUAL_ORDER, emergency_stop function)
pyproject.toml — unchanged (fail_under=85, addopts with -p no:capture)
tests/test_supervisor_coverage.py — 377 lines (unit-level tests for get_pm2_processes, reconcile_swarm_state, stop_bot, enforce_invariant_gate)

Test results: 72/72 supervisor + main tests pass. Coverage: supervisor.py 95.08%, main.py 77.14%.

Remaining main.py gaps (need +8%): liquidation guard get_position_risk flow (1421-1460), trailing stop position closure real-mode path (994-1009), stop/close logic in emergency_stop function (1532-1601), blacklist rebase profit path (616-625).
[30 May 2026]
Liquidation Guard & Supervisor Auto-Restart
Per-position liquidation distance monitoring (warn ≤15%, critical ≤8%)


## Fixed PAPER system capital — 25.10.2026

По поручению пользователя внедрён постоянный бюджет10000USDT, резервирование/возврат PAPER капитала через SQLite и equity/Net%/наблюдаемый MaxDD от фиксированной базы. После архивирования и модельного закрытия12старых пар запущены10ботов:9x1000+854.3544USDT. Старые неподтверждённые положительные результаты152.93667245USDT выделены отдельно и не расходуются. REAL=0. Полная миграция, проверки и ограничения: `reports/paper_system_budget_20261025_195505/REPORT.md`. Это не PASS REAL gate.


PAPER classification repair25.10.2026: common PM2/session/terminal rules, explicit unknown;5legacy sessions reclassified interrupted with original estimates preserved. Authorized restart preserved10wallets/positions/session identities and budget; REAL0. Final9testsPASS. Evidence `reports/paper_classification_20261025_203651/REPORT.md`. Old observations unchanged.


## PAPER backtest-gate — 25.10.2026

Пользователь согласовал24закрытыхUTCчаса минутных данных, текущий конфиг, модельный выход с расходами, Net>0/MaxDD<=50%/cycles>=2. Включён `paper_backtest_gate=true`: фильтрует новыеPAPERкандидаты доmerge, текущие позиции не ликвидирует по одному replay. Из34новых кандидатов интеграционной проверки прошли12. Полные условия, тесты, provenance и ограничения: `reports/backtest_gate/REPORT.md`. Это не оптимизация и не допускREAL. Первый автономный цикл после включения проверяется отдельно.


## New PAPER experiment —25.10.2026 21:27UTC

По явному запросу пользователя выполнен чистый старт без переноса предыдущих сессий/Net. База/cash10000USDT, старая история, бюджет и наблюдения архивированы в `reports/paper_clean_slate_20261025_212711`. Текущие торговые правила и24h backtest-gate сохранены. Мониторинг новой истории отдельно от старой, прежний срок окончания26.10 14:25UTC не продлён автоматически.


## Clean PAPER restart —26.10.2026 01:22Kaliningrad

По повторному явному запросу пользователя новый эксперимент:initial10000USDT,безпереносасессий/Net/старыхreserve; прежняяисторияархивирована `reports/paper_clean_slate_20261025_232225`. СтарыеPAPERSTOPкарантиныснятыкакчастьreset;REALнетронут. Правкилимита10иgateсохранены. БлокерSTOP→releaseещёоткрыт. Мониторинготэтойграницы,исходныйсрок26.10 14:25UTCсохранён.

### 2026-10-26 — PAPER candidate Pareto ranking
- Rank newly admitted candidates by terminal Net percent and maximum drawdown percent using successive nondominated fronts; preserve scanner order within each front and existing-session positions.
- Admission thresholds unchanged; save selected ranking to reports/backtest_gate/ranking.json.
- Validation: tests/test_backtest_gate.py, 3 passed. Future profitability and additional execution-cost stress thresholds are not established.
