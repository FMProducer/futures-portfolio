"""Coverage for the rebalance offline CLI entry points (no exchange, no network)."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from rebalance import binance_readonly, comparison, paper, public_paper, public_session, research_runner
from rebalance.scenarios.test_public_paper import sample

ROOT = Path(__file__).resolve().parent.parent

SETTINGS = dict(capital='10000', spread_bps='2', slippage_bps='1',
                strategy=dict(step='0.001', min_notional='5.3', leverage='7', fee_rate='0.0004',
                              surplus_pp='0.7', deficit_pp='0.7', trail_pp='0.09'))

FILTERS = {key: dict(minQty='0.001', maxQty='100', stepSize='0.001')
           for key in ('LOT_SIZE', 'MARKET_LOT_SIZE')}
FILTERS['MIN_NOTIONAL'] = dict(notional='5')

EXCHANGE_INFO = dict(symbols=[dict(symbol='BTCUSDT', status='TRADING', contractType='PERPETUAL',
                                    baseAsset='BTC', quoteAsset='USDT', marginAsset='USDT', filters=[
                                        dict(filterType='LOT_SIZE', stepSize='0.001', minQty='0.001', maxQty='100'),
                                        dict(filterType='MARKET_LOT_SIZE', stepSize='0.001', minQty='0.001', maxQty='100'),
                                        dict(filterType='MIN_NOTIONAL', notional='5')])])


def write_candles(path, count=10):
    path.write_text('open_time,open,high,low,close\n' + ''.join(
        f'{index * 60000},100,101,99,100\n' for index in range(count)), encoding='utf-8')
    return path


def write_config(path, settings=None):
    path.write_text(json.dumps(settings or SETTINGS), encoding='utf-8')
    return path


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/comparison.py
# ═══════════════════════════════════════════════════════════════════════════

def test_comparison_cli_writes_summary_and_arms(tmp_path, monkeypatch, capsys):
    candles = write_candles(tmp_path / 'bars.csv')
    config = write_config(tmp_path / 'config.json')
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['comparison', '--candles', str(candles), '--config', str(config),
                                     '--output', str(output), '--funding-zero'])
    comparison.main()
    assert (output / 'comparison.json').exists()
    assert (output / 'manifest.json').exists()
    assert (output / 'candidate_result.json').exists()
    assert (output / 'hold_equity.json').exists()
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['funding_mode'] == 'ASSUMED_ZERO'
    assert json.loads(capsys.readouterr().out)['limitations']


def test_comparison_cli_refuses_an_existing_output(tmp_path, monkeypatch):
    candles = write_candles(tmp_path / 'bars.csv')
    config = write_config(tmp_path / 'config.json')
    output = tmp_path / 'out'
    output.mkdir()
    monkeypatch.setattr('sys.argv', ['comparison', '--candles', str(candles), '--config', str(config),
                                     '--output', str(output), '--funding-zero'])
    with pytest.raises(SystemExit):
        comparison.main()


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/paper.py
# ═══════════════════════════════════════════════════════════════════════════

def test_paper_cli_writes_result_and_manifest(tmp_path, monkeypatch, capsys):
    candles = write_candles(tmp_path / 'bars.csv')
    config = write_config(tmp_path / 'config.json')
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['paper', '--candles', str(candles), '--config', str(config),
                                     '--database', str(tmp_path / 'paper.sqlite'),
                                     '--output', str(output), '--funding-zero'])
    paper.main()
    assert (output / 'result.json').exists()
    assert (output / 'trades.json').exists()
    assert (output / 'equity.json').exists()
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['trading_authorized'] is False
    assert json.loads(capsys.readouterr().out)['mode'] == 'PAPER'


def test_paper_cli_refuses_to_overwrite_a_report(tmp_path, monkeypatch):
    candles = write_candles(tmp_path / 'bars.csv')
    config = write_config(tmp_path / 'config.json')
    output = tmp_path / 'out'
    output.mkdir()
    monkeypatch.setattr('sys.argv', ['paper', '--candles', str(candles), '--config', str(config),
                                     '--database', str(tmp_path / 'paper.sqlite'),
                                     '--output', str(output), '--funding-zero'])
    with pytest.raises(SystemExit):
        paper.main()


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/binance_readonly.py
# ═══════════════════════════════════════════════════════════════════════════

class FakeObserver:
    def __init__(self):
        self.calls = []

    def synchronize(self):
        return dict(server_time=1000, roundtrip_ms=1)

    def get(self, path, **params):
        self.calls.append(path)
        if path == '/fapi/v1/positionSide/dual':
            return dict(dualSidePosition=True)
        if path == '/fapi/v1/multiAssetsMargin':
            return dict(multiAssetsMargin=False)
        if path == '/fapi/v3/account':
            return dict(assets=[dict(asset='USDT', walletBalance='100', unrealizedProfit='0',
                                     marginBalance='100', availableBalance='100',
                                     initialMargin='0', maintMargin='0')])
        if path in ('/fapi/v3/positionRisk', '/fapi/v1/openOrders'):
            return []
        if path == '/fapi/v1/exchangeInfo':
            return EXCHANGE_INFO
        raise AssertionError(path)


def test_binance_readonly_cli_writes_an_observation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(binance_readonly.Reader, 'from_env', staticmethod(lambda path: FakeObserver()))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['binance_readonly', '--env-file', str(tmp_path / '.env'),
                                     '--output', str(output), '--symbol', 'BTCUSDT'])
    binance_readonly.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'READ_ONLY_CAPTURED'
    assert payload['trading_authorized'] is False
    assert (output / 'observation.json').exists()
    assert (output / '.gitignore').read_text(encoding='utf-8') == '*\n!.gitignore\n'


def test_binance_readonly_cli_reports_a_blocked_capture(tmp_path, monkeypatch, capsys):
    class Broken(FakeObserver):
        def get(self, path, **params):
            raise binance_readonly.ReadError('http_error', status=403, code=-2015)

    monkeypatch.setattr(binance_readonly.Reader, 'from_env', staticmethod(lambda path: Broken()))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['binance_readonly', '--env-file', str(tmp_path / '.env'),
                                     '--output', str(output), '--symbol', 'BTCUSDT'])
    with pytest.raises(SystemExit) as exc:
        binance_readonly.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'BLOCKED'
    assert json.loads((output / 'error.json').read_text(encoding='utf-8'))['http_status'] == 403


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/public_paper.py
# ═══════════════════════════════════════════════════════════════════════════

class FakePublicReader:
    def __init__(self):
        self.opener = None
        self.stamp = 600000

    def get(self, path, symbol=None):
        if path == '/fapi/v1/exchangeInfo':
            return EXCHANGE_INFO
        if path == '/fapi/v1/time':
            return dict(serverTime=self.stamp)
        raise AssertionError(path)

    def capture(self, symbol):
        self.stamp += 10
        return sample(self.stamp)


def test_public_paper_cli_writes_samples_and_results(tmp_path, monkeypatch, capsys):
    settings = dict(capital='1000', slippage_bps='1',
                    strategy=SETTINGS['strategy'])
    config = write_config(tmp_path / 'config.json', settings)
    monkeypatch.setattr(public_paper, 'PublicReader', FakePublicReader)
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['public_paper', '--config', str(config),
                                     '--database', str(tmp_path / 'paper.sqlite'),
                                     '--output', str(output), '--samples', '1'])
    public_paper.main()
    assert (output / 'sample_0.json').exists()
    assert (output / 'result_0.json').exists()
    assert (output / 'binding.json').exists()
    assert json.loads(capsys.readouterr().out)['trading_authorized'] is False


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/public_session.py
# ═══════════════════════════════════════════════════════════════════════════

class FakeSessionReader(FakePublicReader):
    def funding(self, symbol, timestamp):
        raise public_session.ReadError('funding_not_published')


def test_public_session_cli_runs_and_exports(tmp_path, monkeypatch, capsys):
    settings = dict(capital='1000', slippage_bps='1', strategy=SETTINGS['strategy'])
    config = write_config(tmp_path / 'config.json', settings)
    monkeypatch.setattr(public_session, 'SessionReader', FakeSessionReader)
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['public_session', '--config', str(config),
                                     '--database', str(tmp_path / 'paper.sqlite'),
                                     '--output', str(output), '--duration', '1'])
    public_session.main()
    assert (output / 'session.json').exists()
    body = json.loads((output / 'session.json').read_text(encoding='utf-8'))
    assert body['trading_authorized'] is False
    assert body['status'] in ('STOPPED', 'FROZEN')
    assert json.loads(capsys.readouterr().out)['trading_authorized'] is False


def test_public_session_cli_refuses_an_existing_output(tmp_path, monkeypatch):
    settings = dict(capital='1000', slippage_bps='1', strategy=SETTINGS['strategy'])
    config = write_config(tmp_path / 'config.json', settings)
    monkeypatch.setattr(public_session, 'SessionReader', FakeSessionReader)
    output = tmp_path / 'out'
    output.mkdir()
    monkeypatch.setattr('sys.argv', ['public_session', '--config', str(config),
                                     '--database', str(tmp_path / 'paper.sqlite'),
                                     '--output', str(output), '--duration', '1'])
    with pytest.raises(SystemExit):
        public_session.main()


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/research_runner.py
# ═══════════════════════════════════════════════════════════════════════════

def _register_study(tmp_path, monkeypatch):
    import importlib.util
    from rebalance.trial_registry import Registry
    candles = write_candles(tmp_path / 'candles.csv')
    config = write_config(tmp_path / 'config.json')
    database = tmp_path / 'registry.sqlite'
    output = (tmp_path / 'output').resolve()
    output.mkdir()
    code = output / 'frozen/code/rebalance'
    code.mkdir(parents=True)
    shutil.copyfile(Path(research_runner.__file__), code / 'research_runner.py')
    registry = Registry(database)
    files = {'code': Path(research_runner.__file__), 'data': candles, 'config': config}
    metadata = dict(output=str(output), original_paths={}, source_head=None,
                    engine='comparison', funding_mode='ASSUMED_ZERO', terminal_closeout=False,
                    data_usage='NOT_ESTABLISHED; caller must disclose prior use', admission_ready=False)
    registry.start('fixture', files, metadata, mode='PROSPECTIVE')
    # The worker only accepts an isolated snapshot import, so load the frozen copy.
    spec = importlib.util.spec_from_file_location('rebalance.research_runner', code / 'research_runner.py')
    frozen = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'rebalance.research_runner', frozen)
    spec.loader.exec_module(frozen)
    return database, output, frozen


def test_research_runner_worker_computes_a_registered_trial(tmp_path, monkeypatch):
    database, output, frozen = _register_study(tmp_path, monkeypatch)
    code = frozen.worker(database, 'fixture', str(output))
    assert code == 0
    assert (output / 'comparison.json').exists()
    assert (output / 'candidate_result.json').exists()
    assert (output / 'execution_manifest.json').exists()
    from rebalance.trial_registry import Registry
    report = Registry(database).report()
    assert report['trials'][0]['outcome']['status'] == 'COMPLETED'


def test_research_runner_worker_requires_an_isolated_snapshot(tmp_path, monkeypatch):
    database, output, frozen = _register_study(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='isolated snapshot'):
        frozen.worker(database, 'fixture', str(tmp_path))


def test_research_runner_worker_requires_a_registered_incomplete_trial(tmp_path, monkeypatch):
    database, output, frozen = _register_study(tmp_path, monkeypatch)
    from rebalance.trial_registry import Registry
    Registry(database).finish('fixture', 'FAILED', reason='already finished')
    with pytest.raises(ValueError, match='registered incomplete trial'):
        frozen.worker(database, 'fixture', str(output))


def test_research_runner_cli_dispatches_the_worker(tmp_path, monkeypatch):
    database, output, frozen = _register_study(tmp_path, monkeypatch)
    monkeypatch.setattr('sys.argv', ['research_runner', 'worker', '--database', str(database),
                                     '--id', 'fixture', '--output', str(output)])
    with pytest.raises(SystemExit) as exc:
        frozen.main()
    assert exc.value.code == 0
    assert (output / 'worker.claim').exists()


def test_research_runner_cli_runs_a_study(tmp_path, monkeypatch, capsys):
    candles = write_candles(tmp_path / 'candles.csv')
    config = write_config(tmp_path / 'config.json')
    database = tmp_path / 'registry.sqlite'
    output = tmp_path / 'output'
    monkeypatch.setattr('sys.argv', ['research_runner', 'run', '--database', str(database),
                                     '--id', 'fixture', '--output', str(output),
                                     '--candles', str(candles), '--config', str(config),
                                     '--funding-zero'])
    research_runner.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'COMPLETED'
