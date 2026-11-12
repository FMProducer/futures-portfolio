"""Publish completed parity artifacts as detailed local JSON and Markdown."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def report(directory):
    inputs=json.loads((directory/'inputs.json').read_text())
    run=json.loads((directory/'run_status.json').read_text(encoding='utf-8-sig'))
    frozen=json.loads((directory/'source_hashes.json').read_text(encoding='utf-8-sig'))
    changed=[name for name,digest in frozen.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest().upper()!=digest]
    cases=[]
    for info in inputs['cases']:
        case=directory/f'{info["ticker"]}_{info["mode"]}'
        path=case/'comparison.json'
        if path.exists():
            value=json.loads(path.read_text())
        else:
            name=f'{info["ticker"]}_{info["mode"]}'
            pending=name not in run.get('exit_codes',{}) and run.get('status')=='RUNNING'
            value=dict(status='RUNNING' if pending else 'FAILED_TO_COMPLETE',ticker=info['ticker'],scenario=info['mode'],
                       failures=['Replay not yet completed' if pending else 'Case worker did not produce a comparison'],modes={})
        if changed:
            value['status']='SOURCE_CHANGED_NOT_ACCEPTED'
            value.setdefault('failures',[]).append('Source files changed during the run')
        cash_failures=[item for item in value.get('failures',[]) if 'negative CASH or VIRTUAL' in item]
        other_failures=[item for item in value.get('failures',[]) if item not in cash_failures]
        value['financial_parity']='NOT_VERIFIED' if value['status']=='RUNNING' else 'PASS' if len(value.get('modes',{}))==3 and not other_failures else 'FAIL'
        value['nonnegative_cash_virtual']='FAIL' if cash_failures else 'PASS' if len(value.get('modes',{}))==3 else 'NOT_VERIFIED'
        if value.get('modes'):
            path_case=directory/f'{info["ticker"]}_{info["mode"]}'
            value['cash_virtual_minimum']={}
            for engine in ('paper','real'):
                data_path=path_case/f'{engine}.json'
                if data_path.exists():
                    actual=json.loads(data_path.read_text())
                    value['cash_virtual_minimum'][engine]={key:actual.get(key) for key in ('min_cash','min_virtual')}
        cases.append(value)
    result=dict(base_commit=inputs['base_commit'],created_utc=datetime.now(timezone.utc).isoformat(),
        period=inputs['period'],bars=inputs['bars'],source_changed=changed,cases=cases,
        all_cases_pass=all(c['status']=='PASS' for c in cases),
        all_financial_parity_pass=all(c['financial_parity']=='PASS' for c in cases),
        scope='Annual financial/strategy replay with isolated memory JSON and SQLite; separate physical 180-bar prefixes')
    (directory/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    lines=['# Годовой паритет BACKTEST / PAPER / симулированного REAL','',
        f"База: `{inputs['base_commit']}`. Период: {inputs['period'][0]} — {inputs['period'][1]}, {inputs['bars']} минут.",'',
        f"**Финансовые сверки PASS: {sum(c['financial_parity']=='PASS' for c in cases)}/4. "
        f"Строгий итог с инвариантами PASS: {sum(c['status']=='PASS' for c in cases)}/4.**",'',
        'Каждый тикер тестируется отдельно: торговый капитал 1000 USDT, внешний резерв 5000 USDT, цели 25/25/50. '
        'Исходный рабочий конфиг сохранён; между сценариями меняются только funding-входы и служебные пути изолированного теста.','',
        '## Финансовые результаты','',
        '| Тикер | Funding | Режим | Net TPV до внешнего funding | Net после funding | MaxDD TPV | Конечный TPV, USDT | Комиссии, USDT | Funding net paid, USDT | Резерв, USDT |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for case in cases:
        for mode in ('backtest','paper','real'):
            value=case['modes'].get(mode)
            prefix=f"| {case['ticker']} | {case['scenario']} | {mode.upper()} |"
            if value:
                lines.append(prefix+' '+ ' | '.join(f'{value[key]:+.6f}%' if key.startswith('profit') else
                    f'{value[key]:.6f}%' if key=='max_dd_pct' else f'{value[key]:.8f}' for key in
                    ('profit_pct','profit_after_funding_pct','max_dd_pct','final_tpv','commission','funding_paid','reserve_end'))+' |')
            else:
                lines.append(prefix+' не завершён | — | — | — | — | — | — |')
    lines+=['','Net после funding = (конечный TPV − 1000 − net funding paid) / 1000. '
        'Отрицательный funding net paid означает чистое зачисление. Внешние 5000 USDT не входят в знаменатель этой доходности.','',
        '## Исполнения и проверка паритета','',
        '| Тикер | Funding | Строгий итог | Финансовый паритет | CASH/VIRTUAL ≥0 | Циклы B/P/R | Стопы B/P/R | Исполнения B/P/R | Ненулевых funding-событий B | Макс. ошибка TPV B–P / B–R, USDT | Native-контроль |',
        '|---|---|---|---|---|---|---|---|---:|---|---|']
    for case in cases:
        def counts(key): return ' / '.join(str(case['modes'].get(mode,{}).get(key,'—')) for mode in ('backtest','paper','real'))
        error=case.get('equity_errors',{})
        err=' / '.join(f'{error[mode]:.3e}' if mode in error else '—' for mode in ('paper','real'))
        native=case.get('native_prefix',{})
        lines.append(f"| {case['ticker']} | {case['scenario']} | **{case['status']}** | {case['financial_parity']} | {case['nonnegative_cash_virtual']} | {counts('cycles')} | {counts('stops')} | "
            f"{counts('market_fills')} | {case.get('funding_events','—')} | {err} | {native.get('status','—')} ({native.get('bars','—')} минут) |")
    for case in cases:
        lines+=['',f"### {case['ticker']} / {case['scenario']}",'']
        if case.get('failures'):
            lines += ['- '+value for value in case['failures']]
        else:
            lines += ['Сошлись все минутные TPV, параметры каждого биржевого исполнения, комиссии, realized PnL, '
                'funding-события, итоговый резерв, позиции, циклы и границы стопов/новых сессий.']
        for mode,value in case['modes'].items():
            lines.append(f"- {mode.upper()}: минимальный TPV {value['minimum_tpv']:.8f} USDT; конечные позиции `{value['positions']}`.")
        for engine,minimum in case.get('cash_virtual_minimum',{}).items():
            lines.append(f"- {engine.upper()}: диагностический минимум CASH `{minimum['min_cash']}`, VIRTUAL `{minimum['min_virtual']}` USDT. "
                         "CASH реконструируется адаптером в Decimal из сохранённых float-полей; отрицательный остаток не округляется в ноль.")
        lines.append(f"Артефакты: `{case['ticker']}_{case['scenario']}/comparison.json`, JSON кривых/сделок/funding, stdout/stderr и ошибки отдельных режимов при наличии.")
    lines+=['','## Списания и зачисления исторического funding','',
        '| Тикер | Режим | Ненулевых платежей | Списано из резерва, USDT | Зачислено в резерв, USDT | Чистый расход, USDT |',
        '|---|---|---:|---:|---:|---:|']
    for case in cases:
        if case['scenario']!='historical': continue
        folder=directory/f'{case["ticker"]}_{case["scenario"]}'
        for engine,filename in (('backtest','backtest_rates.json'),('paper','paper_funding.json'),('real','real_funding_receipts.json')):
            file=folder/filename
            if file.exists():
                amounts=[Decimal(str(row['signed_amount'])) for row in json.loads(file.read_text())]
                debit=-sum((amount for amount in amounts if amount<0),Decimal(0))
                credit=sum((amount for amount in amounts if amount>0),Decimal(0))
                lines.append(f"| {case['ticker']} | {engine.upper()} | {sum(amount!=0 for amount in amounts)} | {debit:.8f} | {credit:.8f} | {debit-credit:.8f} |")
    lines+=['','В одной расчётной точке сначала складываются платежи LONG и SHORT. Таблица показывает списания/зачисления '
        'этих чистых settlement-событий, а не валовый оборот каждой ноги по отдельности.','',
        '## Модель funding и происхождение данных','',
        '- `disabled`: ни синтетических, ни исторических платежей; резерв funding не изменяется.',
        '- `historical`: ставки и markPrice в исходное время расчёта. Платёж рассчитывается отдельно по позициям каждого режима: '
        '`(SHORT qty − LONG qty) × historical markPrice × fundingRate`. Это не готовые суммы из funding_TICKER.json.',
        '- ZEC: 1095 записей со ставками и точными markPrice получены из публичной истории Binance. Ставки и timestamps сверены '
        'с локальными событиями. Исходный локальный файл с конфликтом Git не исправлялся.',
        '- AGT: 2189 локальных исторических записей с markPrice. Один восьмичасовой интервал дополнительно подтверждён '
        'публичным API (`source/AGTUSDT_gap_check.json`); искусственное четырёхчасовое событие не добавлялось. Обычный close вместо markPrice не применяется.',
        '- Событие поступает на первый логический минутный тик не раньше fundingTime. REAL-адаптер смещает время API-квитанции '
        'на +60 секунд для часов закрытой свечи; сравнение нормализует это смещение. Это минутная модель, а не воспроизведение субминутного исполнения.',
        '- Динамический BACKTEST по ставкам дополнительно сверяется с обычным BACKTEST на уже вычисленном журнале платежей: '
        'кривые и сделки должны совпасть точно.',
        '- Исторические события подаются тестовым адаптером на вход FundingJournal; режим состояния PAPER задаётся '
        'при инициализации replay. Проверяется учёт и стратегия при одинаковых входах, а не наличие production-загрузчика '
        'исторических ставок в запускаемом PAPER-процессе.',
        'Формула и API: [Binance funding](https://www.binance.com/en/support/faq/detail/360033525031), '
        '[история funding](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History).','',
        '## Границы проверки','',
        '- Полный год: реальные state machine, калькулятор и исполнитель проекта; JSON изолирован в памяти, SQLite использует '
        'реальную схему/архив в памяти. Физические JSON/WAL/SQLite проверены на первых 180 минутах каждого сценария.',
        '- Исполнение моделируется немедленными market fills: slippage 0.0005, комиссия 0.0004, шаг количества 0.001. '
        'Цена торгового тика — минутный close. Исторический order book, внутриминутный порядок цен, задержки, '
        'реальные биржевые отказы и принудительная биржевая ликвидация не воспроизводятся.',
        '- Net/MaxDD относятся к торговому TPV. Отрицательный TPV и MaxDD больше 100% не равны ликвидации общего аккаунта '
        'при положительном внешнем резерве.',
        '- Общий multi-bot маржинальный аккаунт этим сравнением не сертифицируется. Рабочий рой и master не изменялись.',
        '- В диагностике обнаружен отдельный крайний случай BACKTEST с нулевым cooldown; в запрошенных конфигурациях cooldown положительный.',
        '- Git-коммит, push и запуск REAL на бирже не выполнялись. Хеши входов: `inputs.json`; хеши кода: `source_hashes.json`.']
    lines+=['','## Тесты адаптеров и общая регрессия','']
    for filename,label in (('adapter_tests_accepted.log','Адаптеры funding'),('case_runner_tests.log','Сквозной тест формирования сравнения'),('full_tests.log','Полный набор')):
        path=directory/filename
        if path.exists():
            text=path.read_text(encoding='utf-8',errors='replace')
            passed=re.findall(r'(\d+) passed',text)
            failed=re.findall(r'(\d+) failed',text)
            lines.append(f"- {label}: {passed[-1] if passed else '—'} passed, {failed[-1] if failed else '0'} failed; `{filename}`.")
    if (directory/'monitoring_failures_isolated.log').exists():
        lines+=['- Два сбоя мониторинга воспроизведены отдельным запуском: `test_monitor_sends_request` '
            '(`KeyError: req`) и `test_monitoring_dynamic_project_dir` (проверка отвергает путь на диске C: даже при корректном PROJECT_DIR). '
            'Мониторинговые модули и эти тесты не менялись. Общая регрессия не объявляется PASS; '
            'лог: `monitoring_failures_isolated.log`.']
    if changed: lines+=['','**Исходный код изменился во время запуска:** '+', '.join(changed)]
    (directory/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(all_cases_pass=result['all_cases_pass'],cases=[dict(ticker=c['ticker'],scenario=c['scenario'],status=c['status']) for c in cases]),indent=2))


if __name__=='__main__':
    report(Path(sys.argv[1]).resolve())
