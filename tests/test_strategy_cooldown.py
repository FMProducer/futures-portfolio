"""Exercise supervisor routing without importing or launching the supervisor."""
import ast
import math
from pathlib import Path
import time
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('signal_type', ['exit', 'drawdown'])
@pytest.mark.parametrize('mode', ['paper', 'real'])
@pytest.mark.parametrize('verified', [True, False])
async def test_confirmed_strategy_stop_preserves_backtest_deadline(mode, verified, tmp_path, signal_type):
    source = Path(__file__).resolve().parents[1] / 'supervisor/swarm_manager.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name)
                  and n.test.left.id == 'signal_type' and isinstance(n.test.ops[0], ast.In)
                  and isinstance(n.test.comparators[0], (ast.Tuple, ast.List))
                  and {'exit', 'siphon'} <= {e.value for e in n.test.comparators[0].elts
                                           if isinstance(e, ast.Constant)})
    module = ast.parse('async def route():\n    pass\n')
    module.body[0].body = branch.body
    state = {'stop_reason': 'equity_trailing_stop', 'trailing_stop_triggered': True,
             'paper_close_complete': verified, 'live_close': {'status': 'complete' if verified else 'pending'},
             'restart_not_before': 2100}
    wallet = {'positions': {'Q_LONG': 0, 'Q_SHORT': 0}}
    async def load(path, default):
        return state if f'{mode}_state_' in path else wallet
    paper, real = {}, {}
    names = dict(signal_type=signal_type, mode_tag=mode, ticker='Q', now_ts=2000,
                 toxic_cooldown_sec=300, probation_cooldown_sec=999,
                 prob_paper=paper, prob_real=real, config={}, CORE_PATH=tmp_path,
                 safe_load_json=load, safe_save_json=AsyncMock(), CONFIG_PATH=tmp_path/'config.json',
                 logger=Mock(), math=math, time=time)
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), names)
    if verified:
        await names['route']()
        assert names['config'][f'probation_{mode}'] == {'Q': 2100}
        assert 'black_list' not in names['config']
        names['safe_save_json'].assert_awaited_once()
        assert names['safe_save_json'].call_args.args[1][f'probation_{mode}'] == {'Q': 2100}
    else:
        with pytest.raises(ValueError, match='confirmed terminal close'):
            await names['route']()
        assert not paper and not real
        names['safe_save_json'].assert_not_awaited()
