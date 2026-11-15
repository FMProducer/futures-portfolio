import pytest
from core.capital import trailing_stop_active


@pytest.mark.parametrize('peak,activation,expected', [
    (1000, 0, True), (999, 0, True), (1000, 1.55, False),
    (1015.5, 1.55, False), (1016, 1.55, True),
])
def test_trailing_activation(peak, activation, expected):
    assert trailing_stop_active(peak, 1000, activation) is expected


@pytest.mark.asyncio
async def test_zero_activation_stops_below_initial_capital(tmp_path):
    import json
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest

    config = {'funding_model': 'disabled', 'equity_trailing_stop_pct': .3, 'equity_trailing_stop_activation_pct': 0,
              'max_drawdown_limit': 100, 'toxic_cooldown_days': 1,
              'portfolios': [{'initial_capital': 1000, 'paper_initial_capital': 1000,
                             'liquidation_distance_crit_pct': 0,
                             'post_factum_liquidation_check': False,
                             'targets': {'BASE_LONG': {'share': .4, 'leverage': 3},
                                         'BASE_SHORT': {'share': .1, 'leverage': 3},
                                         'VIRTUAL': {'share': .5, 'leverage': 1}}}]}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    prices = [100.] * 3 + [99.] * 7
    pd.DataFrame({key: prices for key in ['open', 'high', 'low', 'close']},
                 ).assign(volume=100.).to_feather(tmp_path / 'SOPHUSDT_live.feather')
    result = await run_backtest(str(path), str(tmp_path), ticker_override='SOPHUSDT', days=1, quiet=True)
    assert result is not None and result['accounting_ok']
    assert result['trailing_stops'] == 1
    assert result['final_tpv'] < 1000
