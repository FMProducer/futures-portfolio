"""Parity input modes must preserve disabled funding and use historical marks."""
from decimal import Decimal as D
import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.backtest_rebalance import run_backtest
from scripts.parity_funding import HistoricalFunding, backtest_funding_bridge
from scripts.verify_runtime_parity import replay as paper_replay
from scripts.verify_real_runtime_parity import replay as real_replay


def inputs(tmp_path, mode):
    config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
    for key in ('funding_reserve_checkpoint','funding_reserve_receipts','funding_reserve_totals'):
        config.pop(key, None)
    config.update(funding_model=mode, funding_scenario=str(tmp_path/'events.json'))
    config['portfolios'][0].update(cash_reserve_usdt=5000, paper_account_free_margin=5000)
    for key, share in (('BASE_LONG', .4), ('BASE_SHORT', .1), ('VIRTUAL', .5)):
        config['portfolios'][0]['targets'][key]['share'] = share
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    (tmp_path/'events.json').write_text('[]')
    times = pd.date_range('2026-01-01', periods=40, freq='min', tz='UTC')
    feather = tmp_path/'TESTUSDT_live.feather'
    pd.DataFrame(dict(time=times, open=100., high=100., low=100., close=100., volume=1.)).to_feather(feather)
    rows = [dict(symbol='TESTUSDT',fundingTime=times[i].value//1000000+offset,
                 fundingRate=rate,markPrice='110') for i,offset,rate in ((10,4,'.001'),(30,0,'-.002'))]
    return path, feather, rows


def test_history_marks_sign_and_unique_event_boundary():
    rows = [dict(symbol='TESTUSDT',fundingTime=1004,fundingRate='.001',markPrice='110')]
    schedule = HistoricalFunding('TESTUSDT', rows)
    assert schedule.due(1000) == [] and schedule.due(2000) == rows and schedule.due(2000) == []
    assert schedule.amount(rows[0], 12, 3) == D('-.990')
    assert schedule.amount(rows[0], 3, 12) == D('.990')
    with pytest.raises(ValueError):
        HistoricalFunding('TESTUSDT', rows+rows)
    with pytest.raises(ValueError):
        HistoricalFunding('TESTUSDT', [{**rows[0], 'markPrice':'0'}])


@pytest.mark.asyncio
@pytest.mark.parametrize('native',[False,True])
async def test_disabled_has_no_income_and_same_three_mode_curve(tmp_path,native):
    path, feather, _ = inputs(tmp_path,'disabled')
    b = await run_backtest(str(path),str(tmp_path),ticker_override='TESTUSDT',days=40/1440)
    p = await paper_replay(path,feather,40,memory_reserve_store=not native,native_persistence=native)
    r = await real_replay(path,feather,40,archive_applied=True,memory_reserve_store=not native,native_persistence=native)
    assert p['_curve'] == pytest.approx(b['equity_curve'],abs=1e-8)
    assert r['_curve'] == pytest.approx(b['equity_curve'],abs=1e-8)
    assert p['funding'] == r['funding'] == b['funding_paid'] == 0
    assert not p['_funding'] and not r['_funding'] and not r['_funding_receipts']


@pytest.mark.asyncio
@pytest.mark.parametrize('native',[False,True])
async def test_historical_marks_three_modes_and_unmodified_fixed_journal_control(tmp_path,native):
    path, feather, rows = inputs(tmp_path,'signed_actual')
    schedule = HistoricalFunding('TESTUSDT', rows)
    with backtest_funding_bridge(schedule):
        b = await run_backtest(str(path),str(tmp_path),ticker_override='TESTUSDT',days=40/1440)
    p = await paper_replay(path,feather,40,memory_reserve_store=not native,native_persistence=native,
                           funding_rates=HistoricalFunding('TESTUSDT',rows))
    r = await real_replay(path,feather,40,archive_applied=True,memory_reserve_store=not native,native_persistence=native,
                          funding_rates=HistoricalFunding('TESTUSDT',rows))
    assert p['_curve'] == pytest.approx(b['equity_curve'],abs=1e-8)
    assert r['_curve'] == pytest.approx(b['equity_curve'],abs=1e-8)
    assert len(schedule.observations) == p['funding_events'] == r['funding_events'] == 2
    assert p['funding'] == pytest.approx(b['funding_paid'],abs=1e-12)
    assert r['funding'] == pytest.approx(b['funding_paid'],abs=1e-12)
    assert any(D(row['signed_amount']) < 0 for row in schedule.observations)
    assert any(D(row['signed_amount']) > 0 for row in schedule.observations)
    events = [dict(event_id=f"rates:TESTUSDT:{row['fundingTime']}", signed_amount=row['signed_amount'],
                   event_time=row['fundingTime']/1000,symbol='TESTUSDT',status='received')
              for row in schedule.observations]
    (tmp_path/'events.json').write_text(json.dumps(events))
    control = await run_backtest(str(path),str(tmp_path),ticker_override='TESTUSDT',days=40/1440)
    assert control['equity_curve'] == b['equity_curve'] and control['fills'] == b['fills']


@pytest.mark.asyncio
async def test_historical_funding_survives_confirmed_close_and_restart(tmp_path):
    path, feather, _ = inputs(tmp_path,'signed_actual')
    config = json.loads(path.read_text())
    config.update(equity_trailing_stop_activation_pct=.1,toxic_cooldown_days=.001)
    for key,share in (('BASE_LONG',.5),('BASE_SHORT',.1),('VIRTUAL',.4)):
        config['portfolios'][0]['targets'][key]['share']=share
    path.write_text(json.dumps(config))
    prices=[100.,105.,103.,102.,102.,102.]
    times=pd.date_range('2026-01-01',periods=6,freq='min',tz='UTC')
    pd.DataFrame(dict(time=times,open=prices,high=prices,low=prices,close=prices,volume=1.)).to_feather(feather)
    rows=[dict(symbol='TESTUSDT',fundingTime=times[i].value//1000000,fundingRate='.001',markPrice='100') for i in (1,4)]
    book=HistoricalFunding('TESTUSDT',rows)
    with backtest_funding_bridge(book):
        b=await run_backtest(str(path),str(tmp_path),ticker_override='TESTUSDT',days=6/1440)
    p=await paper_replay(path,feather,6,memory_reserve_store=True,funding_rates=HistoricalFunding('TESTUSDT',rows))
    r=await real_replay(path,feather,6,archive_applied=True,memory_reserve_store=True,funding_rates=HistoricalFunding('TESTUSDT',rows))
    assert b['trailing_stops']==p['stops']==r['stops']==1
    assert p['_curve']==pytest.approx(b['equity_curve'],abs=1e-8)
    assert r['_curve']==pytest.approx(b['equity_curve'],abs=1e-8)
