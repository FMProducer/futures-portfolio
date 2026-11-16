"""Coverage for config merge helpers, the ledger evidence runner and remaining CLIs."""
import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from core import storage
from rebalance import public_paper, reconcile

ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════════
#  core/storage.py
# ═══════════════════════════════════════════════════════════════════════════

def _config(**portfolio):
    return {'portfolios': [dict(portfolio)]}


def test_merge_funding_receipts_adds_a_new_siphon_credit():
    merged = storage.merge_funding_receipts(
        _config(cash_reserve_usdt=5), {'siphoning_credits': {'real:abc': 10}})
    assert merged['portfolios'][0]['cash_reserve_usdt'] == 15
    assert merged['siphoning_credits'] == {'real:abc': 10}


def test_merge_funding_receipts_keeps_an_identical_credit():
    merged = storage.merge_funding_receipts(
        _config(cash_reserve_usdt=5), {'siphoning_credits': {'real:abc': 10}})
    again = storage.merge_funding_receipts(
        merged, {'siphoning_credits': {'real:abc': 10}})
    assert again['portfolios'][0]['cash_reserve_usdt'] == 15


def test_merge_funding_receipts_rejects_a_changed_credit():
    data = _config(cash_reserve_usdt=5)
    data['siphoning_credits'] = {'real:abc': 9}
    with pytest.raises(ValueError, match='Siphon credit receipt changed'):
        storage.merge_funding_receipts(data, {'siphoning_credits': {'real:abc': 11}})


def test_merge_funding_receipts_rejects_an_invalid_credit_mode():
    with pytest.raises(ValueError, match='Invalid siphon credit receipt'):
        storage.merge_funding_receipts(
            _config(cash_reserve_usdt=5), {'siphoning_credits': {'other:abc': 10}})


def test_merge_funding_receipts_applies_reserve_totals():
    merged = storage.merge_funding_receipts(
        _config(cash_reserve_usdt=5), {'funding_reserve_totals': {'cash_reserve_usdt': 10}})
    assert merged['portfolios'][0]['cash_reserve_usdt'] == 15
    assert merged['funding_reserve_totals'] == {'cash_reserve_usdt': 10}


def test_merge_funding_receipts_carries_the_checkpoint():
    latest = {'funding_reserve_totals': {'cash_reserve_usdt': 10},
              'funding_reserve_checkpoint': {'at': 1}}
    merged = storage.merge_funding_receipts(_config(cash_reserve_usdt=5), latest)
    assert merged['funding_reserve_checkpoint'] == {'at': 1}


def test_merge_funding_receipts_rejects_a_stale_reserve_total():
    data = _config(cash_reserve_usdt=0)
    data['funding_reserve_receipts'] = {'r1': {'settled': '50', 'reserve_key': 'cash_reserve_usdt'}}
    with pytest.raises(ValueError, match='Stale config cannot reconcile funding reserve'):
        storage.merge_funding_receipts(data, {'funding_reserve_totals': {'cash_reserve_usdt': 10}})


def test_merge_funding_receipts_applies_a_reserve_receipt():
    merged = storage.merge_funding_receipts(
        _config(cash_reserve_usdt=5),
        {'funding_reserve_receipts': {'r1': {'settled': '10', 'reserve_key': 'cash_reserve_usdt'}}})
    assert merged['portfolios'][0]['cash_reserve_usdt'] == 15
    assert merged['funding_reserve_receipts']['r1']['settled'] == '10'


def test_merge_funding_receipts_rejects_a_changed_receipt():
    data = _config(cash_reserve_usdt=5)
    data['funding_reserve_receipts'] = {'r1': {'settled': '10', 'reserve_key': 'cash_reserve_usdt'}}
    with pytest.raises(ValueError, match='Funding reserve receipt changed'):
        storage.merge_funding_receipts(
            data, {'funding_reserve_receipts': {'r1': {'settled': '11',
                                                      'reserve_key': 'cash_reserve_usdt'}}})


def test_merge_funding_receipts_rejects_an_invalid_reserve_key():
    with pytest.raises(ValueError, match='Invalid funding reserve key'):
        storage.merge_funding_receipts(
            _config(cash_reserve_usdt=5),
            {'funding_reserve_receipts': {'r1': {'settled': '10', 'reserve_key': 'other'}}})


def test_merge_funding_receipts_rejects_a_stale_receipt():
    with pytest.raises(ValueError, match='Stale config cannot reconcile funding reserve'):
        storage.merge_funding_receipts(
            _config(cash_reserve_usdt=0),
            {'funding_reserve_receipts': {'r1': {'settled': '-10',
                                                  'reserve_key': 'cash_reserve_usdt'}}})


def test_safe_load_json_sync_returns_the_default_for_empty_files(tmp_path):
    path = tmp_path / 'empty.json'
    path.write_text('', encoding='utf-8')
    assert storage.safe_load_json_sync(str(path), {'a': 1}) == {'a': 1}
    assert storage.safe_load_json_sync(str(tmp_path / 'missing.json'), {'a': 1}) == {'a': 1}


@pytest.mark.asyncio
async def test_safe_load_json_returns_the_default_after_retries(tmp_path):
    path = tmp_path / 'broken.json'
    path.write_text('{not json', encoding='utf-8')
    assert await storage.safe_load_json(str(path), {'a': 1}, retries=2) == {'a': 1}


# ═══════════════════════════════════════════════════════════════════════════
#  accounting/scenarios/run.py
# ═══════════════════════════════════════════════════════════════════════════

def test_accounting_evidence_runner_records_reproducibility(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'root'
    shutil.copytree(ROOT / 'accounting', root / 'accounting')
    (root / 'docs' / 'coordination').mkdir(parents=True)
    shutil.copyfile(ROOT / 'docs' / 'coordination' / 'ACCOUNTING_SPEC_V3.md',
                    root / 'docs' / 'coordination' / 'ACCOUNTING_SPEC_V3.md')
    spec = importlib.util.spec_from_file_location('accounting_evidence_runner',
                                                   ROOT / 'accounting' / 'scenarios' / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Redirect the evidence output into the temporary tree; the module code stays measured.
    monkeypatch.setattr(module, '__file__', str(root / 'accounting' / 'scenarios' / 'run.py'))
    monkeypatch.setattr(module, 'subprocess',
                        types.SimpleNamespace(check_output=lambda *a, **k: 'deadbeef\n'))
    assert module.main() == 0
    evidence = root / 'docs/coordination/accounting_stage1_evidence'
    manifest = json.loads((evidence / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['successful'] is True
    assert manifest['tests_run'] > 0
    assert manifest['base_commit'] == 'deadbeef'
    assert manifest['scenarios'][0]['status'] == 'PASS'
    assert (evidence / 'scenarios.log').exists()
    assert (evidence / 'candidate.patch').exists()
    assert json.loads(capsys.readouterr().out)['successful'] is True


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/reconcile.py
# ═══════════════════════════════════════════════════════════════════════════

class HistoryReader:
    def __init__(self):
        self.offset = 0

    def synchronize(self):
        self.offset += 1
        return dict(server_time=1767225600000 + self.offset, roundtrip_ms=1)

    def get(self, path, **params):
        if path == '/fapi/v1/userTrades':
            return [dict(symbol='BTCUSDT', id=1, orderId=1, positionSide='LONG', side='BUY',
                         qty='.1', price='100', commission='.01', commissionAsset='USDT',
                         realizedPnl='0', time=params['startTime'])]
        if path == '/fapi/v1/income':
            return [dict(incomeType='COMMISSION', tranId=1, tradeId='1', symbol='BTCUSDT',
                         asset='USDT', income='-.01', time=params['startTime'])]
        raise AssertionError(path)


def test_reconcile_cli_writes_a_bounded_history(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reconcile.Reader, 'from_env', staticmethod(lambda path: HistoryReader()))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['reconcile', '--env-file', str(tmp_path / '.env'),
                                     '--symbol', 'BTCUSDT', '--hours', '24', '--output', str(output)])
    reconcile.main()
    payload = json.loads((output / 'history.json').read_text(encoding='utf-8'))
    assert payload['coverage'] == 'BOUNDED_REST_WINDOW_NOT_ACCOUNT_CHECKPOINT'
    assert payload['result']['trading_authorized'] is False
    assert json.loads(capsys.readouterr().out)['status'] == 'READ_ONLY_HISTORY_COLLECTED'


def test_reconcile_cli_rejects_an_unbounded_window(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['reconcile', '--env-file', str(tmp_path / '.env'),
                                     '--hours', '0', '--output', str(tmp_path / 'out')])
    with pytest.raises(SystemExit):
        reconcile.main()


def test_reconcile_cli_reports_a_blocked_read(tmp_path, monkeypatch, capsys):
    class Broken(HistoryReader):
        def get(self, path, **params):
            raise reconcile.ReadError('network_or_response_error')

    monkeypatch.setattr(reconcile.Reader, 'from_env', staticmethod(lambda path: Broken()))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['reconcile', '--env-file', str(tmp_path / '.env'),
                                     '--symbol', 'BTCUSDT', '--hours', '24', '--output', str(output)])
    with pytest.raises(SystemExit) as exc:
        reconcile.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'BLOCKED'


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/public_paper.py
# ═══════════════════════════════════════════════════════════════════════════

class FakeOpener:
    def __init__(self, payloads):
        self.payloads = payloads

    def open(self, request, timeout):
        import io
        url = request.full_url
        for fragment, payload in self.payloads.items():
            if fragment in url:
                class Response(io.BytesIO):
                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        self.close()

                return Response(json.dumps(payload).encode())
        raise AssertionError(url)


def test_public_reader_captures_a_validated_sample():
    reader = public_paper.PublicReader()
    reader.opener = FakeOpener({
        'exchangeInfo': {'symbols': []},
        '/fapi/v1/time': {'serverTime': 600000},
        'premiumIndex': {'symbol': 'BTCUSDT', 'time': 600000, 'markPrice': '100',
                         'nextFundingTime': 3600000},
        'bookTicker': {'symbol': 'BTCUSDT', 'time': 600000, 'bidPrice': '99.9',
                       'askPrice': '100.1', 'bidQty': '100', 'askQty': '100'},
    })
    captured = reader.capture('BTCUSDT')
    assert captured['symbol'] == 'BTCUSDT'
    assert captured['after'] == 600000


def test_public_reader_rejects_symbols_outside_the_allowlist():
    reader = public_paper.PublicReader()
    with pytest.raises(ValueError, match='public allowlist'):
        reader.get('/fapi/v1/private')


def test_public_reader_reports_a_failed_request():
    reader = public_paper.PublicReader()
    reader.opener = FakeOpener({})
    with pytest.raises(public_paper.ReadError, match='public_request_failed'):
        reader.get('/fapi/v1/time')


def test_rules_reject_unknown_or_non_trading_symbols():
    with pytest.raises(ValueError, match='unknown symbol'):
        public_paper.rules({'symbols': []}, 'BTCUSDT')
    info = {'symbols': [dict(symbol='BTCUSDT', status='BREAK', contractType='PERPETUAL',
                             quoteAsset='USDT', marginAsset='USDT', filters=[])]}
    with pytest.raises(ValueError, match='requires trading USDT perpetual'):
        public_paper.rules(info, 'BTCUSDT')


def test_validate_rejects_a_non_integer_capture_timestamp():
    with pytest.raises(public_paper.CaptureError, match='invalid_capture_timestamp'):
        public_paper.validate({'symbol': 'BTCUSDT', 'before': 'x', 'after': 1}, 'BTCUSDT')


def test_validate_rejects_a_non_integer_quote_timestamp():
    sample = dict(symbol='BTCUSDT', before=1, after=2,
                  mark=dict(symbol='BTCUSDT', time='x', markPrice='100', nextFundingTime=10),
                  book=dict(symbol='BTCUSDT', time=1, bidPrice='1', askPrice='2',
                            bidQty='1', askQty='1'))
    with pytest.raises(public_paper.CaptureError, match='invalid_quote_timestamp'):
        public_paper.validate(sample, 'BTCUSDT')


def test_validate_rejects_an_unknown_funding_horizon():
    sample = dict(symbol='BTCUSDT', before=1, after=2,
                  mark=dict(symbol='BTCUSDT', time=1, markPrice='100', nextFundingTime=2),
                  book=dict(symbol='BTCUSDT', time=1, bidPrice='1', askPrice='2',
                            bidQty='1', askQty='1'))
    with pytest.raises(public_paper.CaptureError, match='invalid_capture_payload'):
        public_paper.validate(sample, 'BTCUSDT')
