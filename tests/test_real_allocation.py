import asyncio
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch
from pathlib import Path
import sqlite3
from filelock import FileLock, Timeout

import pytest

from core import real_allocation as allocation


def setup(tmp_path):
    (tmp_path/'core').mkdir(exist_ok=True)
    connector = SimpleNamespace(api_key='test-account-key', testnet=True,
        get_usdt_account=AsyncMock(return_value={'wallet': '6000', 'available': '6000'}),
        get_positions=AsyncMock(return_value={}), get_open_orders=AsyncMock(return_value=[]))
    config = {'max_bots': 9, 'paper_mode_bots': 8, 'portfolios': [{
        'initial_capital': 1000, 'cash_reserve_usdt': 5000, 'targets': {'VIRTUAL': {'share': .5}}}]}
    return connector, config


@pytest.mark.asyncio
async def test_real_book_excludes_shared_reserve_and_does_not_change_paper_budget(tmp_path):
    connector, config = setup(tmp_path)
    paper = tmp_path/'core/paper_budget.sqlite'
    paper.write_bytes(b'untouched-paper-pool')
    state, wallet = await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)
    assert state['initial_tpv'] == wallet['balance'] == 1000
    assert state['virt_debt'] == 500
    assert state['live_funding']['opening_wallet'] == '6000'
    assert state['live_funding']['allocation']['reserve'] == '5000'
    assert not any(wallet['positions'].values())
    allocation.reconcile(tmp_path, state['live_funding'], state, wallet, '6000', allocation.account_key(connector))
    assert json.loads((tmp_path/'core/shadow_state_ZECUSDT.json').read_text())['balance'] == 1000
    assert paper.read_bytes() == b'untouched-paper-pool'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['wallet', 'available', 'position', 'order', 'changing_wallet'])
async def test_no_allocation_or_snapshot_when_account_proof_fails(tmp_path, failure):
    connector, config = setup(tmp_path)
    if failure == 'wallet':
        connector.get_usdt_account.return_value = {'wallet': '5999.99', 'available': '6000'}
    elif failure == 'available':
        connector.get_usdt_account.return_value = {'wallet': '6000', 'available': '5999.99'}
    elif failure == 'position':
        connector.get_positions.return_value = {'OTHERUSDT_LONG': {'qty': 1}}
    elif failure == 'order':
        connector.get_open_orders.return_value = [{'symbol': 'ZECUSDT', 'orderId': 1}]
    else:
        connector.get_usdt_account.side_effect = [{'wallet': '6000', 'available': '6000'}, {'wallet': '6001', 'available': '6001'}]
    with pytest.raises(ValueError):
        await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)
    assert not (tmp_path/'core/real_state_ZECUSDT.json').exists()
    assert not (tmp_path/'core/real_budget.sqlite').exists()


@pytest.mark.asyncio
async def test_second_owner_and_missing_snapshot_recreation_are_refused(tmp_path):
    connector, config = setup(tmp_path)
    await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)
    with pytest.raises(ValueError, match='confirmed terminal close'):
        await allocation.prepare(connector, tmp_path, 'AGTUSDT', config)
    with pytest.raises(ValueError, match='overwrite'):
        await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)


@pytest.mark.asyncio
async def test_actual_pnl_funding_and_reserve_are_reconciled_without_balance_delta_income(tmp_path):
    connector, config = setup(tmp_path)
    state, wallet = await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)
    key = allocation.account_key(connector)
    wallet['balance'] = 900
    allocation.reconcile(tmp_path, state['live_funding'], state, wallet, 5900, key)
    before = deepcopy((state, wallet))
    with pytest.raises(ValueError, match='unexplained'):
        allocation.reconcile(tmp_path, state['live_funding'], state, wallet, 5901, key)
    assert (state, wallet) == before
    state['siphoning_reserve'], wallet['balance'] = 50, 850
    allocation.reconcile(tmp_path, state['live_funding'], state, wallet, 5900, key)
    forged = deepcopy(state['live_funding']['allocation'])
    forged['capital'] = '2000'
    with pytest.raises(ValueError, match='active confirmed owner'):
        allocation.validate(tmp_path, forged, 'ZECUSDT', key)
    with pytest.raises(ValueError, match='identity'):
        allocation.validate(tmp_path, state['live_funding']['allocation'], 'ZECUSDT', 'other-account')


def test_concurrent_reservations_have_exactly_one_real_owner(tmp_path):
    setup(tmp_path)
    def reserve(ticker):
        try:
            return allocation.claim(tmp_path, 'account', ticker, 1000, 5000, 6000, 6000, 1)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(reserve, ['ZECUSDT', 'AGTUSDT']))
    assert sum(result is not None for result in results) == 1
    with sqlite3.connect(tmp_path/'core/real_budget.sqlite') as db:
        assert db.execute('SELECT count(*) FROM allocations WHERE active=1').fetchone()[0] == 1


@pytest.mark.asyncio
async def test_handover_reconciles_actual_funding_and_never_copies_old_profit_into_new_capital(tmp_path):
    connector, config = setup(tmp_path)
    with patch('core.real_allocation.time.time', return_value=100):
        old, wallet = await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)
    old.update(trailing_stop_triggered=True, live_close={'status': 'complete'})
    wallet['balance'] = 1100
    sp, wp = tmp_path/'core/real_state_ZECUSDT.json', tmp_path/'core/shadow_state_ZECUSDT.json'
    sp.write_text(json.dumps(old)); wp.write_text(json.dumps(wallet))
    connector.get_usdt_account.return_value = {'wallet': '6099.75', 'available': '6099.75'}
    connector.get_funding_income = AsyncMock(return_value=[{'symbol': 'ZECUSDT', 'asset': 'USDT',
        'incomeType': 'FUNDING_FEE', 'tranId': 7, 'time': 100001, 'income': '-.25'}])
    with patch('core.real_allocation.time.time', return_value=101):
        new, current = await allocation.prepare(connector, tmp_path, 'AGTUSDT', config)
    assert current['balance'] == 1000 and new['initial_tpv'] == 1000
    assert new['live_funding']['opening_wallet'] == '6099.75'
    closed, final_wallet = json.loads(sp.read_text()), json.loads(wp.read_text())
    assert final_wallet['balance'] == 1099.75 and closed['funding_paid'] == .25
    assert closed['funding_journal'][0]['status'] == 'applied'
    with pytest.raises(ValueError, match='active confirmed owner'):
        allocation.validate(tmp_path, old['live_funding']['allocation'], 'ZECUSDT', allocation.account_key(connector))


@pytest.mark.asyncio
async def test_late_funding_quarantine_survives_failed_handover(tmp_path):
    connector, config = setup(tmp_path)
    with patch('core.real_allocation.time.time', return_value=100):
        old, wallet = await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)
    old.update(trailing_stop_triggered=True, live_close={'status': 'complete'})
    old['live_funding']['boundary_ms'] = 100050
    wallet['balance'] = 1100
    sp, wp = tmp_path/'core/real_state_ZECUSDT.json', tmp_path/'core/shadow_state_ZECUSDT.json'
    sp.write_text(json.dumps(old)); wp.write_text(json.dumps(wallet))
    connector.get_usdt_account.return_value = {'wallet': '6099', 'available': '6099'}
    connector.get_funding_income = AsyncMock(return_value=[{'symbol': 'ZECUSDT', 'asset': 'USDT',
        'incomeType': 'FUNDING_FEE', 'tranId': 7, 'time': 100040, 'income': '-1'}])
    with patch('core.real_allocation.time.time', return_value=101), pytest.raises(ValueError, match='Late funding'):
        await allocation.prepare(connector, tmp_path, 'AGTUSDT', config)
    failed = json.loads(sp.read_text())
    assert failed['startup_blocked'] and failed['funding_journal'][0]['status'] == 'quarantined'
    assert json.loads(wp.read_text())['balance'] == 1100
    assert not (tmp_path/'core/real_state_AGTUSDT.json').exists()


def test_real_cli_account_lock_excludes_second_worker_without_locking_paper(tmp_path):
    source = Path(__file__).resolve().parents[1]/'core/main.py'
    node = next(n for n in ast.walk(ast.parse(source.read_text(encoding='utf-8'))) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'execution_guard' for t in n.targets))
    connector, _ = setup(tmp_path)
    names = dict(is_paper_instance=False, FileLock=FileLock, Path=Path, gettempdir=lambda: str(tmp_path),
                 account_key=allocation.account_key, connector=connector, nullcontext=nullcontext)
    code = compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec')
    exec(code, names); first = names['execution_guard']
    exec(code, names); second = names['execution_guard']
    with first:
        with pytest.raises(Timeout), second:
            pass
        names['is_paper_instance'] = True
        exec(code, names)
        with names['execution_guard']:
            assert first.is_locked
    with second:
        assert second.is_locked


@pytest.mark.asyncio
async def test_operational_config_retains_eight_paper_slots_with_one_real_slot(tmp_path):
    from unittest.mock import Mock
    from core.paper_budget import initialize, read_budget
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/'config.json').read_text())
    assert config['max_bots']-config['paper_mode_bots'] == 1
    assert config['paper_mode_bots'] == 8
    source = root/'supervisor/swarm_manager.py'
    function = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'selective_merge_incubator')
    names = {'logger': Mock()}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), names)
    selected = await names['selective_merge_incubator']([], [{'symbol': f'T{i}USDT'} for i in range(20)], config, {})
    assert len(selected) == 8
    (tmp_path/'core').mkdir(exist_ok=True)
    initialize(tmp_path, config['paper_mode_bots'], config['portfolios'][0]['paper_initial_capital'], {})
    assert read_budget(tmp_path)['initial'] == 8000


@pytest.mark.asyncio
async def test_failed_snapshot_commit_keeps_owner_quarantined(tmp_path):
    connector, config = setup(tmp_path)
    with patch('core.funding.save_funding_boundary', side_effect=OSError('disk failure')):
        with pytest.raises(OSError, match='disk failure'):
            await allocation.prepare(connector, tmp_path, 'ZECUSDT', config)
    with sqlite3.connect(tmp_path/'core/real_budget.sqlite') as db:
        proof, = db.execute('SELECT proof FROM allocations WHERE active=1').fetchone()
        assert json.loads(proof)['ticker'] == 'ZECUSDT'
    with pytest.raises(ValueError, match='snapshot missing'):
        await allocation.prepare(connector, tmp_path, 'AGTUSDT', config)
    assert not (tmp_path/'core/real_state_AGTUSDT.json').exists()
