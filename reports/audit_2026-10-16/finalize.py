"""Build audit inventory and verify evidence without rerunning economic trials."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
BASE = "44db0bb9d1b1675868ef351de24422ed6c7ab817"
notes = {
    ".hermes/plans/2026-07-11_010000-min-notional-optimization.md": "Исторический план per-ticker minimums и global 5.1. Текущий global 5.3; план не заменяет текущие фильтры и не даёт разрешения на смену параметров. Фиксированный шаг backtest 0.001 не воспроизводит этот план.",
    ".hermes/plans/2026-07-12_193800-frax-ustc-analysis.md": "Архивный отказ FRAX/USTC при другом пороге. Короткая выборка 500 минут, текущие входы не сохранены здесь; не доказательство допуска или непригодности сегодня.",
    ".hermes/plans/2026-07-12_194350-frax-ustc-low-threshold.md": "Архивная противоположная рекомендация FRAX после смены порога. Авторы сами отмечают рост комиссий. Текущий аудит не менял пороги и не выполнял эту рекомендацию о live_swarm.",
    ".hermes/plans/2026-07-13_test-coverage-90-plan.md": "План покрытия с tier-исключениями и изменением знаменателя. Нынешние 92.19% относятся к настроенному набору; не охватывают dashboard/backtest и включают тесты accounting.",
    "CHANGELOG.md": "Исторические заявления о telemetry, исправлениях и тестах, в том числе внешних Hermes-скриптах. Точные текущие результаты получены заново; архивный PASS не перенесён. Упоминания внешних 1.27M heartbeats не проверены по исходной БД.",
    "CLAUDE.md": "Legacy Futures-инструкции, плоские пути, несколько исторических состояний clock-offset и риска. Не целевой spot-контракт; старые проценты/команды не выполнялись как инструкции аудита.",
    "MOC.md": "Навигация на 17 июля с несуществующими плоскими модулями, тестами и внешними Obsidian-заметками. Статус REAL/INJUSDT — исторический, не проверка работающей системы.",
    "README.md": "Текущий onboarding Futures. Путь core/main.py актуален, но pip install/импорт пакета не воспроизводятся. Пример распределения отличается от current config и v3; реальные команды не запускались.",
    "ROADMAP.md": "Legacy цель >=5%/день и архивная статистика 344 инструментов конфликтуют с целевыми правилами допуска/риска v3. Нынешние engine-пробы опровергают доверие к движку, но сами по себе не пересчитывают конкретный старый архив.",
    "accounting/README.md": "Честно задаёт офлайн-границы: условный GAS, trust fixed evidence, неизвестная частичная отправка, отсутствие durable PENDING_DATA и historical rebuild. Эти ограничения не считать реализованной интеграцией.",
    "dashboard/README.md": "Заявление read-only противоречит описанным PM2-кнопкам. Установка отдельных dashboard-зависимостей описана, но не включена в project requirements. Рекомендацию отключать firewall аудит не выполнял.",
    "docs/AUTOMATION_LOGIC.md": "Протокол 24h и короткий backtest-gate; не соответствует 24m/6m OOS v3. Перенос ATH между сессиями не заменяет account-wide HWM и корректировки внешних потоков.",
    "docs/CHANGES_SUMMARY.md": "История configurable ticker. Описывает старые имена main/calculator и пример targets с ticker-ключами вместо нынешних BASE_LONG/BASE_SHORT. Не использовать пример как точный current config.",
    "docs/Code Review бота ребалансировщика.md": "Общие предложения о Decimal, асинхронности, CCXT, Zen3 и патчи несуществующих math_engine/manager. Это предложения, не проверенная реализация и не аппаратный аудит нынешнего компьютера.",
    "docs/Dialog.md": "Промоциональные выводы по paper ROI и предложения расширить плечо/слоты. Нет воспроизводимого доказательства устойчивости; не трактуются как решение User для нового проекта.",
    "docs/Dialog_2.md": "Гипотезы о zombie, SAFE и комиссиях; завершён вопросом о согласии. Нельзя выдавать предложенный Zombie Detector за подтверждённое внедрение.",
    "docs/Market-Neutral Futures Portfolio Rebalancer_1.0.pdf": "20-страничный меморандум от 23 мая. Сам требует независимой проверки и OOS, рассматривает Futures 29/36/35 и смешивает реализованное с roadmap. Его архивные таблицы не являются нынешним Net или допуском.",
    "docs/PROMPT_trailing_stop_rotation_bug.md": "Историческая постановка проблем exit/HEAL/config race. Проверенный текущий код уже содержит тесты сигналов/изоляции blacklist; старые line numbers не использовать как доказательство текущего дефекта.",
    "docs/RESTRUCTURING.md": "Правильно описывает переезд в core/supervisor, но заявление об удалении import-hacks неполно: backtest всё ещё добавляет sys.path; package discovery не настроено. Карта модулей полезна, готовность установки не подтверждается.",
    "docs/TICKER_MIGRATION.md": "Legacy миграция ticker. Пример с API keys в config противоречит другим рекомендациям про env; примеры не копировались в рабочую конфигурацию. Текущая поддержка тикеров не означает верные шаги/минимумы симулятора.",
    "docs/Ticker Rotation Algorithm.md": "Диаграммы ротации и guards, архивные параметры 20/19 slots, TS 5.7 и старые unresolved bugs. Сверены с текущим кодом/конфигом; не норматив для v3.",
    "docs/coordination/ACCOUNTING_SPEC_V3.md": "Целевая спецификация: spot, Decimal, независимые liabilities/advance, согласованные Equity/потоки, 24m/6m OOS, риск 10% account/3% strategy. Совпадает с локальной proposed-v3 по SHA256; последние решения User выше документа.",
    "docs/coordination/IMPLEMENTATION_ACCOUNTING_STAGE1.md": "Узкая передача офлайн-модуля с 35 сценариями. Хеши и фактический тестовый результат сохранены; прежний статус без commit относится к моменту подготовки. Обозначенные интеграционные ограничения остаются открыты.",
    "docs/log.md": "Фрагменты PM2 за 14 июля подтверждают старые core/state и core/logs пути. Это архив operational-log, не состояние настоящего дня и не независимая банковская/биржевая сверка.",
    "docs/ticker_rotation_algorithm.md": "Второе описание ротации, сильно пересекающееся с вариантом в Title Case, с теми же устаревшими параметрами и bug-list. Нет единой закреплённой текущей версии документа.",
    "docs/Бэктесты _10_дней.md": "Архивные консольные результаты по пяти инструментам и пути в другом checkout. Нет закреплённых сырых входов/коммита для воспроизведения этих чисел; в аудит как PASS не перенесены.",
    "docs/Данные с Binance.md": "Справочный каталог API/WebSocket/архивов. Не реализует согласование event-time, balances, fills и flows. Наличие ссылки на WebSocket не доказывает валидность Equity.",
    "docs/Описание_логики_Супервайзера.md": "Описание paper-инкубатора/рейтинга и 1d проверок. Его promotion/замена прибыльных ботов расходится с последующими guards и v3; текущий код и тесты проверялись отдельно.",
    "docs/Рекомендации по документации.md": "Список недостающих runbooks/метрик/артефактов. Часть файлов уже добавлена, однако provenance бэктестов и актуальная data-schema по-прежнему не сведены. Рекомендации не выполнялись автоматически.",
    "tests/README.md": "Указывает pytest-cov с импортом futures_portfolio. Это требует правильно именованной/установленной структуры; в checkout с дефисом прямой import не работает. Состав тестов проверен фактической коллекцией, не перечнем из README.",
}
docs = json.loads((OUT / "documents.json").read_text(encoding="utf-8"))
assert {d["path"] for d in docs} == set(notes)
text = "# Разбор документов проверенного HEAD\n\n30 Markdown/PDF; полный инвентарь и SHA256 — documents.json. Внешние ссылки и недоступные архивы отдельно не верифицированы.\n\n| Документ | Вывод для autonomous-asset-management |\n|---|---|\n"
for d in docs:
    text += f"| {d['path']} | {notes[d['path']]} |\n"
(OUT / "DOCUMENT_REVIEW.md").write_text(text, encoding="utf-8")
original = ET.parse(OUT / "unit_results.xml")
retry = ET.parse(OUT / "unit_results_isolated.xml")
tests = list(original.iter("testcase"))
assert len(tests) == 363
assert len([t for t in tests if t.find("failure") is not None]) == 3
assert len(list(retry.iter("testcase"))) == 3
assert not list(retry.iter("failure")) and not list(retry.iter("error"))
backtests = json.loads((OUT / "backtest_results.json").read_text())
assert len(backtests) == 9 and sum(b["result"] is None for b in backtests) == 4
dashboard = json.loads((OUT / "dashboard_results.json").read_text())
assert dashboard["page_status"]["/api/ticker/real/BTCUSDT"] == 500
assert dashboard["source_default_key_forged_cookie_status"] == 200
assert len(json.loads((OUT / "execution_results.json").read_text())["limit_ack_loss"]["accepted_order_calls"]) == 2
source_hashes = {}
for name in filter(None, subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "-z", BASE], cwd=ROOT).decode().split("\0")):
    body = subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
    source_hashes[name] = hashlib.sha256(body).hexdigest()
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
manifest = dict(source_commit=BASE, observed_head=head, finalized_utc=datetime.now(timezone.utc).isoformat(),
                source_sha256=source_hashes, unit_initial=dict(total=363, passed=360, blocked_by_pm2_guard=3),
                unit_with_explicit_save_mock=dict(selected=3, passed=3), backtest_attempts=9, engine_errors=4,
                accepted_economic_backtests=0, reviewed_documents=30)
paths = [p for p in OUT.iterdir() if p.is_file() and p.name != "audit_manifest.json"]
paths += list((OUT / "screenshots").glob("*"))
manifest["artifact_sha256"] = {p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
(OUT / "audit_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: manifest[k] for k in ("source_commit", "observed_head", "unit_initial", "backtest_attempts", "reviewed_documents")}))
