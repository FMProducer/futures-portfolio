"""Research-only cumulative PAPER readiness and targeted identity reservations."""
import json

TARGETS = frozenset(('ONUSDT', 'HOTUSDT'))


def cumulative_cycles(root, ticker, current):
    sessions = {}
    for path in sorted((root/'history').glob(f'paper_close_{ticker}_*.json')):
        archive = json.loads(path.read_text(encoding='utf-8'))
        state, wallet = archive['state'], archive['paper_state']
        identity = str(state['started_at'])
        count = state['rebalance_cycles']
        assert state['base_ticker'] == ticker and state['paper_close_complete']
        assert wallet['base_ticker'] == ticker and all(float(q) == 0 for q in wallet['positions'].values())
        assert isinstance(count, int) and count >= 0
        assert archive['close_event'] in (f'stop:{identity}:{count}', f'trailing:{identity}:{count}')
        assert identity not in sessions or sessions[identity] == count
        sessions[identity] = count
    if current:
        identity, count = str(current['started_at']), current.get('rebalance_cycles', 0)
        assert isinstance(count, int) and count >= 0
        if identity in sessions:
            assert sessions[identity] == count
        sessions[identity] = count
    return sum(sessions.values())


def instrument(source):
    marker = '    active_positions = {} if paper_only else await connector.get_positions()'
    assert source.count(marker) == 1
    source = source.replace("if any(name.startswith('r_') for name in await get_running_bots_info()):", "if sum(name.startswith('r_') for name in await get_running_bots_info()) >= 2:")
    source = source.replace("sum(name.startswith('r_') for name in running) >= 1", "sum(name.startswith('r_') for name in running) >= 2")
    return source.replace(marker,
        '    research_observe(final_incubator, ready_pool, target_real_bots, perf_map)\n'
        + marker)


def observe(replay, incubator, ready, selected, performance):
    assert set(incubator) <= TARGETS and set(selected) <= TARGETS and len(selected) <= 2
    assert sum(k.startswith('r_') for k in replay.processes) <= 2
    reserved = getattr(replay, 'research_reserved', set())
    for ticker in selected:
        if 'r_'+ticker not in replay.processes:
            assert performance[ticker]['cycles'] >= 15 and performance[ticker]['profit'] > 0
        reserved.add(ticker)
    replay.research_reserved = reserved
    replay.event('selection_policy', selection=replay.selection, incubator=sorted(incubator),
        ready=sorted(ready), selected=sorted(selected), reserved=sorted(reserved),
        performance={t:performance[t] for t in sorted(performance)},
        scope='Fresh-slot decisions; reservations retain identity only, not execution capital or active positions')
