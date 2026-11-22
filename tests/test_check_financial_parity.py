"""The public CLI must preserve dates, policy and real three-mode execution."""
import argparse
import json

import pandas as pd
import pytest

from scripts.check_financial_parity import main, prepare
from test_parity_funding_modes import inputs


def arguments(tmp_path):
    config, data, _ = inputs(tmp_path, 'disabled')
    policy = json.loads(config.read_text())
    policy['funding_scenario'] = ''
    config.write_text(json.dumps(policy))
    return argparse.Namespace(ticker='TESTUSDT', data=data, config=config, start=None,
                              end=None, funding_rates=None, output=tmp_path/'result')


def test_selects_inclusive_minutes_without_mutating_inputs(tmp_path):
    args = arguments(tmp_path)
    before = args.config.read_bytes(), args.data.read_bytes()
    args.start, args.end = '2026-01-01 00:10', '2026-01-01 00:19'
    directory = prepare(args)
    case = json.loads((directory/'case.json').read_text())
    assert case['bars'] == 10 and case['virtual_clock'] is True
    assert pd.read_feather(case['data_path']).time.iloc[0] == pd.Timestamp(args.start, tz='UTC')
    assert before == (args.config.read_bytes(), args.data.read_bytes())
    with pytest.raises(FileExistsError):
        prepare(args)


@pytest.mark.parametrize('kind', ['gap', 'outside', 'wrong_symbol', 'funding', 'capital'])
def test_rejects_ambiguous_or_incomplete_inputs(tmp_path, kind):
    args = arguments(tmp_path)
    if kind == 'gap':
        pd.read_feather(args.data).drop(index=5).reset_index(drop=True).to_feather(args.data)
    elif kind == 'outside':
        args.start = '2025-12-31'
    elif kind == 'wrong_symbol':
        args.ticker = 'BTCUSDT'
    else:
        policy = json.loads(args.config.read_text())
        if kind == 'funding':
            policy['funding_model'] = 'signed_actual'
        else:
            policy['portfolios'][0]['paper_initial_capital'] = 2000
        args.config.write_text(json.dumps(policy))
    with pytest.raises(ValueError):
        prepare(args)
    assert not args.output.exists()


def test_cli_executes_actual_engines_and_writes_exit_verdict(tmp_path):
    args = arguments(tmp_path)
    code = main(['TESTUSDT', '--data', str(args.data), '--config', str(args.config),
                 '--start', '2026-01-01 00:05', '--end', '2026-01-01 00:24',
                 '--output', str(args.output)])
    assert code == 0
    summary = json.loads((args.output/'summary.json').read_text())
    assert summary['financial_parity'] == summary['overall'] == 'PASS'
    for mode in ('paper', 'real'):
        clock = json.loads((args.output/f'{mode}_clock.json').read_text())
        assert clock['margin_ms'] == 5000
        assert clock['premature'] == clock['valid'] == clock['processed_bars'] == 20


def test_financial_pass_does_not_hide_negative_cash(tmp_path):
    from scripts.check_financial_parity import summarize

    (tmp_path/'case.json').write_text(json.dumps({'initial_capital': 1000}))
    row = dict(final_tpv=1001, profit_pct=.1, max_dd_pct=0, commission=1, cycles=1, market_fills=1)
    (tmp_path/'comparison.json').write_text(json.dumps(dict(status='FAIL',
        failures=['real: negative CASH or VIRTUAL'], modes={k: row for k in ('backtest', 'paper', 'real')})))
    assert summarize(tmp_path) == 2
    assert json.loads((tmp_path/'summary.json').read_text())['overall'] == 'FAIL'
    row['final_tpv'] = float('nan')
    (tmp_path/'comparison.json').write_text(json.dumps(dict(status='PASS', failures=[],
        modes={k: row for k in ('backtest', 'paper', 'real')})))
    assert summarize(tmp_path) == 1
    assert json.loads((tmp_path/'summary.json').read_text())['overall'] == 'FAIL'


def test_signed_actual_interval_can_have_no_funding_events(tmp_path):
    args = arguments(tmp_path)
    policy = json.loads(args.config.read_text())
    policy['funding_model'] = 'signed_actual'
    args.config.write_text(json.dumps(policy))
    args.funding_rates = tmp_path/'funding.json'
    args.funding_rates.write_text(json.dumps([dict(symbol='TESTUSDT', fundingTime=1767254400000,
                                                 fundingRate='0.001', markPrice='100')]))
    directory = prepare(args)
    assert json.loads((directory/'rates.json').read_text()) == []
