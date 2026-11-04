"""Siphoning closes the session without losing SAFE or accounting identity."""

import ast
import asyncio
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import textwrap
from unittest.mock import AsyncMock, Mock

import pytest

from backtest.backtest_rebalance import BacktestState
from core import profit_budget, siphoning
from core.calculator import PortfolioCalculator


ROOT = Path(__file__).resolve().parents[1]


def source_block(path, start, end):
    source = (ROOT/path).read_text(encoding="utf-8")
    return textwrap.dedent(source[source.index(start):source.index(end, source.index(start))])


@pytest.mark.parametrize("threshold,share", [(30,.334),(33.4,.334),(0,0)])
def test_valid_threshold(threshold, share):
    siphoning.validate_config(threshold,share)


@pytest.mark.parametrize("equity", [1000, 1300, 1000000000])
def test_zero_threshold_disables_siphoning(equity):
    siphoning.validate_config(0, .334)
    assert siphoning.calculate_amount(equity, 1000, 1000000000, 0) == D(0)


@pytest.mark.parametrize("threshold,share", [(30,.2),(33.4001,.334),(-1,.334),(float('nan'),.334),(float('inf'),.334),(1,0)])
def test_invalid_threshold(threshold, share):
    with pytest.raises(ValueError, match="siphoning_threshold_pct"):
        siphoning.validate_config(threshold,share)


@pytest.mark.parametrize("equity,virtual,expected", [(1299.99,400,0),(1300,400,300),
                                                    (1318.3,400,318.3),(1300,200,200),
                                                    (1300,0,0),(1300,-1,0)])
def test_threshold_is_inclusive_and_take_is_capped_by_virtual(equity, virtual, expected):
    assert siphoning.calculate_amount(equity,1000,virtual,30) == D(str(expected))


def test_backtest_siphon_closes_positions_and_starts_clean_after_cooldown():
    state=BacktestState(initial_capital=D(1270),base_ticker="TESTUSDT",val_cash=D(700),
                        virt_debt=D(400),pos_long=D(3),pos_short=D(3),long_entry_price=D(100),
                        short_entry_price=D(100),l_lev=D(3),s_lev=D(3),ledger_base=D(1000),
                        realized_total=D(300),realized_ath=1300)
    profit_budget.ledger(state.profit_book)["balance"]="300"
    sim=Mock()
    sim.simulate_market_execution.side_effect=lambda side,qty,price: (price, qty*price*D('.0004'))
    ns=dict(state=state,mid_price=D(110),trading_initial_capital=1000,siphoning_threshold_pct=30,
            reinvestment_ratio=0,Decimal=D,siphoning=siphoning,profit_budget=profit_budget,logger=Mock(),i=7,
            sim=sim,cooldown_bars_duration=10,share_trail=Mock(),portfolio_cfg={})
    code=source_block("backtest/backtest_rebalance.py","            # --- 4. SAFE Siphoning ---",
                      "            # --- 4c. Trailing Stop ---")
    exec("for _ in [0]:\n"+textwrap.indent(code,"    "),ns)
    assert state.pos_long==state.pos_short==state.long_entry_price==state.short_entry_price==0
    assert state.get_tpv(D(110))==D('999.736')
    assert state.siphoning_reserve==300 and state.virt_debt==100
    assert state.account_free_margin==300 and ns["portfolio_cfg"]["cash_reserve_usdt"]==300
    assert state.cooldown_until_bar==17 and state.trailing_stop_triggered
    assert [fill["trigger"] for fill in state.fills]==["siphon_stop","siphon_stop"]
    assert state.commission_paid==D('.264')
    assert state.siphoning_log[-1]["active_pnl"]==0
    assert state.siphoning_log[-1]["initial_capital"]==1000
    assert state.initial_capital==1000
    assert state.accounting_residual(D(110))==0
    ns.update(i=17,df=[0]*30,targets={"VIRTUAL":{"share":.334}})
    cooldown=source_block("backtest/backtest_rebalance.py",
        "            if i == state.cooldown_until_bar and state.cooldown_until_bar > 0:",
        "            # --- 2. Portfolio Calculation ---")
    exec("for _ in [0]:\n"+textwrap.indent(cooldown,"    "),ns)
    assert state.siphoning_reserve==300 and state.get_tpv(D(110))==D('999.736')
    assert state.tpv_ath==999.736 and state.initial_capital==D('999.736')
    assert state.trailing_stop_violation_start==0 and not state.trailing_stop_triggered
    assert state.profit_book=={} and state.initial_build_active
    assert state.accounting_residual(D(110))==0


@pytest.mark.parametrize("active", [0,1000])
def test_cooldown_never_reinvests_safe(active):
    state=BacktestState(initial_capital=D(1000),base_ticker="TESTUSDT",val_cash=D(active),
                        siphoning_reserve=D(300),ledger_base=D(1000),realized_total=D(active-700),
                        dormant_capital=D(active+300),cooldown_until_bar=10)
    ns=dict(state=state,i=10,df=[0]*20,mid_price=D(100),logger=Mock(),Decimal=D,
            targets={"VIRTUAL":{"share":.334}})
    code=source_block("backtest/backtest_rebalance.py",
                      "            if i == state.cooldown_until_bar and state.cooldown_until_bar > 0:",
                      "            # --- 2. Portfolio Calculation ---")
    exec("for _ in [0]:\n"+textwrap.indent(code,"    "),ns)
    assert state.siphoning_reserve==300
    assert state.get_tpv(D(100))==active
    assert state.accounting_residual(D(100))==0
    if active:
        assert state.initial_capital==1000 and state.virt_debt==334
        assert state.events[0]["reserve_retained"]==300
        assert state.tpv_ath==1000
    else:
        assert state.cooldown_until_bar==21


@pytest.mark.asyncio
@pytest.mark.parametrize("close_fails", [False, True])
async def test_runtime_siphon_stops_only_after_persisted_close(monkeypatch, close_fails):
    targets={"BASE_LONG":{"share":.333,"leverage":3},"BASE_SHORT":{"share":.333,"leverage":3},
             "VIRTUAL":{"share":.334,"leverage":1}}
    positions={"TESTUSDT_LONG":3,"TESTUSDT_SHORT":3}
    state={"virt_debt":400,"siphoning_reserve":0,"initial_tpv":1270,"reference_tpv":1270}
    wallet={"balance":1300,"positions":deepcopy(positions),"long_entry_price":100,"short_entry_price":100}
    before=deepcopy(wallet)
    kwargs=dict(positions=positions,spot_price=110,real_equity=900,virt_debt=400,virt_qty=0,
                base_ticker="TESTUSDT",siphoning_reserve=0,targets=targets,initial_capital=1270,
                long_entry_price=100,short_entry_price=100,last_rebalance_price=110)
    calc=PortfolioCalculator(**kwargs)
    notifier=Mock(send_message=AsyncMock())
    events=[]
    monkeypatch.setattr("core.funding.load_funding_state", lambda state: ({},{}))
    persist=Mock(side_effect=lambda *args: events.append("persist"))
    monkeypatch.setattr("core.funding.save_funding_boundary",persist)
    async def close(*args,**kwargs):
        events.append("close")
        if close_fails:
            raise RuntimeError("Unverified close")
    stop=AsyncMock(side_effect=close)
    kill=AsyncMock(side_effect=lambda *args: events.append("kill"))
    signal=Mock(side_effect=lambda *args: events.append("signal"))
    ns=dict(state=state,paper_state=wallet,safe_calc=calc,safe_calc_kwargs=kwargs,
            target_initial_cap=1000,initial_tpv=1270,reference_tpv=1270,siphoning_reserve=0,
            siphoning_threshold_pct=30,reinvestment_ratio=None,Decimal=D,PortfolioCalculator=PortfolioCalculator,
            siphoning=siphoning,profit_budget=profit_budget,logger=Mock(),notifier=notifier,
            asyncio=asyncio,targets=targets,time=Mock(time=Mock(return_value=100)),
            current_config={"toxic_cooldown_days":.1},state_file_path="state",paper_state_file_path="wallet",
            config_path="config",base_ticker="TESTUSDT",paper_mode=True,connector=Mock(),
            emergency_stop=stop,self_kill_pm2=kill,emit_signal=signal,safe_calc_res=None,cycles=3)
    code=source_block("core/main.py","                amount = siphoning.calculate_amount(safe_calc.tpv, target_initial_cap,",
                      "                # Update reporting value in summary")
    exec("async def run():\n    global "+",".join(ns)+"\n"+textwrap.indent(code,"    "),ns)
    if close_fails:
        with pytest.raises(RuntimeError,match="Unverified close"):
            await ns["run"]()
        assert events==["persist","close"]
        kill.assert_not_awaited()
        signal.assert_not_called()
    else:
        await ns["run"]()
        assert events==["persist","close","signal","kill"]
        signal.assert_called_once_with("siphon","TESTUSDT",True)
    stop.assert_awaited_once_with(ns["connector"],"config","state","wallet",ns["logger"],
                                 ticker_override="TESTUSDT",paper_mode=True,close_only=True)
    assert state["restart_not_before"]==8740 and state["stop_reason"]=="siphon"
    assert state["trailing_stop_triggered"]
    await asyncio.sleep(0)
    assert wallet["positions"]==before["positions"]
    assert wallet["long_entry_price"]==wallet["short_entry_price"]==100
    assert wallet["balance"]==1000 and state["virt_debt"]==100 and state["siphoning_reserve"]==300
    assert ns["safe_calc"].tpv==1000 and ns["safe_calc"].total_tpv==1300
    assert ns["safe_calc_res"]["total_pnl"]==0
    assert ns["safe_calc_res"]["val_cash"]==700
    assert state["initial_tpv"]==1000 and state["reference_tpv"]==1000
    assert state["last_tpv"]==1300 and state["last_profit"]==300
    notifier.send_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("signal_type,days", [("siphon",.1),("exit",.001)])
@pytest.mark.parametrize("mode", ["paper","real"])
async def test_supervisor_siphon_uses_toxic_cooldown_without_blacklisting(tmp_path, signal_type, days, mode):
    signals=tmp_path/"signals"
    signals.mkdir()
    flag=signals/f"{signal_type}_{mode}_TESTUSDT.flag"
    flag.touch()
    config={"live_swarm":["TESTUSDT"],"toxic_cooldown_days":.1,"probation_period_days":.001,
            "portfolios":[{"paper_account_free_margin":5000,"cash_reserve_usdt":5000}]}
    closed=dict(trailing_stop_triggered=True,paper_close_complete=True,stop_reason="siphon",
                siphon_amount=300,siphoning_reserve=300,started_at=1,rebalance_cycles=3)
    documents=[closed,{"positions":{"TESTUSDT_LONG":0,"TESTUSDT_SHORT":0}}] if mode=="paper" else [closed]
    load=AsyncMock(side_effect=documents)
    saved=[]
    async def save(path, document):
        saved.append(deepcopy(document))
        assert flag.exists()
    ns=dict(BASE_PATH=tmp_path,config=config,time=Mock(time=Mock(return_value=100),ctime=Mock()),
            asyncio=asyncio,paper_only=False,logger=Mock(),CORE_PATH=tmp_path,CONFIG_PATH="config",
            safe_load_json=load,safe_save_json=save,siphoning=siphoning,
            connector=Mock(get_positions=AsyncMock(return_value={})))
    code=source_block("supervisor/swarm_manager.py","    signals_dir = BASE_PATH / \"signals\"\n    now_ts",
                      "    # [CACHE] Один вызов pm2 jlist")
    exec("async def process():\n"+textwrap.indent(code,"    "),ns)
    await ns["process"]()
    assert config[f"probation_{mode}"]["TESTUSDT"]==100+days*86400
    assert not config.get("black_list") and not config.get("toxic_blacklist_paper")
    assert config["live_swarm"]==[] and not flag.exists()
    if signal_type=="siphon":
        key="paper_account_free_margin" if mode=="paper" else "cash_reserve_usdt"
        assert config["portfolios"][0][key]==5300
        assert saved[0]["portfolios"][0][key]==5300
        flag.touch()
        load.side_effect=documents
        await ns["process"]()
        assert config["portfolios"][0][key]==5300


@pytest.mark.asyncio
@pytest.mark.parametrize("paper", [False,True])
async def test_supervisor_cannot_launch_during_siphon_cooldown(tmp_path, paper):
    source=ROOT/"supervisor/swarm_manager.py"
    tree=ast.parse(source.read_text(encoding="utf-8"))
    function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="start_bot")
    ns=dict(CORE_PATH=tmp_path,time=Mock(time=Mock(return_value=100)),logger=Mock(),
            safe_load_json=AsyncMock(return_value={"restart_not_before":101}),get_running_bots_info=AsyncMock())
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),"exec"),ns)
    await ns["start_bot"]("TESTUSDT",is_paper=paper,config={"toxic_cooldown_days":.1})
    ns["safe_load_json"].assert_awaited_once()
    ns["get_running_bots_info"].assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_recovers_missing_siphon_signal_before_reset(tmp_path):
    source=ROOT/"supervisor/swarm_manager.py"
    tree=ast.parse(source.read_text(encoding="utf-8"))
    function=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="start_bot")
    ns=dict(CORE_PATH=tmp_path,BASE_PATH=tmp_path,time=Mock(time=Mock(return_value=100)),logger=Mock(),
            safe_load_json=AsyncMock(return_value=dict(stop_reason="siphon",started_at=1,rebalance_cycles=3)),
            get_running_bots_info=AsyncMock(),reset_bot_state_files=AsyncMock())
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),"exec"),ns)
    await ns["start_bot"]("TESTUSDT",is_paper=True,config={"toxic_cooldown_days":.1})
    assert (tmp_path/"signals/siphon_paper_TESTUSDT.flag").exists()
    ns["reset_bot_state_files"].assert_not_awaited()
    ns["get_running_bots_info"].assert_not_awaited()


@pytest.mark.parametrize("mode,key", [("paper","paper_account_free_margin"),("real","cash_reserve_usdt")])
def test_config_credit_is_exactly_once_and_keeps_trading_capital(mode, key):
    config={"portfolios":[{"initial_capital":1000,"paper_account_free_margin":5000,"cash_reserve_usdt":5000}]}
    original=deepcopy(config)
    closed=dict(stop_reason="siphon",siphon_amount=300,siphoning_reserve=300,started_at=1,rebalance_cycles=3)
    assert siphoning.credit_reserve(config,mode,"TESTUSDT",closed)
    assert not siphoning.credit_reserve(config,mode,"TESTUSDT",closed)
    assert config["portfolios"][0][key]==5300 and config["portfolios"][0]["initial_capital"]==1000
    assert len(config["siphoning_credits"])==1
    if mode=="real":
        assert siphoning.funded_reserve_change(original,config)
        config["portfolios"][0][key]+=1
        assert not siphoning.funded_reserve_change(original,config)


@pytest.mark.parametrize("amount", [0,-1,301,float('nan'),float('inf')])
def test_invalid_config_credit_does_not_mutate_config(amount):
    config={"portfolios":[{"cash_reserve_usdt":5000}]}
    original=deepcopy(config)
    with pytest.raises(ValueError):
        siphoning.credit_reserve(config,"real","TESTUSDT",dict(stop_reason="siphon",siphon_amount=amount,
            siphoning_reserve=300,started_at=1,rebalance_cycles=3))
    assert config==original


def test_paper_free_margin_backs_collateral_without_changing_wallet():
    from core.main import _paper_guard_metrics
    wallet={"balance":1000,"positions":{"TESTUSDT_LONG":3,"TESTUSDT_SHORT":3},
            "long_entry_price":100,"short_entry_price":100}
    original=deepcopy(wallet)
    collateral,maintenance,distance=_paper_guard_metrics(wallet,"TESTUSDT",100,D(1000),Mock(),5300)
    assert collateral==6300 and maintenance==D('2.4') and distance>99
    assert wallet==original


def test_hard_account_liquidation_consumes_safe_backing_margin():
    state=BacktestState(initial_capital=D(1000),base_ticker="TESTUSDT",val_cash=D(700),virt_debt=D(100),
        pos_short=D(6),short_entry_price=D(100),s_lev=D(3),siphoning_reserve=D(300),
        account_free_margin=D(5300),ledger_base=D(1000),realized_total=D(300))
    assert state.accounting_residual(D(100))==0
    assert state.hard_liquidation_triggered(D(2000),D('.004'))
    code=source_block("backtest/backtest_rebalance.py","                    liq_price = adverse_price",
                      "                    if state.share_trail is not None:")
    exec(code,dict(state=state,adverse_price=D(2000),Decimal=D,logger=Mock(),i=7))
    assert state.siphoning_reserve==state.account_free_margin==state.get_tpv(D(2000))==0
    assert state.accounting_residual(D(2000))==0
