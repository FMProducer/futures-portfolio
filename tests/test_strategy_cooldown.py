"""Exercise supervisor routing without importing or launching the supervisor."""
import ast
import math
from pathlib import Path
import time
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['paper', 'real'])
@pytest.mark.parametrize('verified', [True, False])
async def test_confirmed_strategy_stop_preserves_backtest_deadline(mode, verified, tmp_path):
    source = Path(__file__).resolve().parents[1] / 'supervisor/swarm_manager.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "signal_type in ('exit', 'siphon')")
    module = ast.parse('async def route():\n    pass\n')
    module.body[0].body = branch.body
    state = {'stop_reason': 'equity_trailing_stop', 'trailing_stop_triggered': True,
             'paper_close_complete': verified, 'live_close': {'status': 'complete' if verified else 'pending'},
             'restart_not_before': 2100}
    wallet = {'positions': {'Q_LONG': 0, 'Q_SHORT': 0}}
    async def load(path, default):
        return state if f'{mode}_state_' in path else wallet
    paper, real = {}, {}
    names = dict(signal_type='exit', mode_tag=mode, ticker='Q', now_ts=2000,
                 toxic_cooldown_sec=300, probation_cooldown_sec=999,
                 prob_paper=paper, prob_real=real, config={}, CORE_PATH=tmp_path,
                 safe_load_json=load, logger=Mock(), math=math, time=time)
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), names)
    if verified:
        await names['route']()
        assert names['config'][f'probation_{mode}'] == {'Q': 2100}
        assert 'black_list' not in names['config']
    else:
        with pytest.raises(ValueError, match='confirmed terminal close'):
            await names['route']()
        assert not paper and not real
