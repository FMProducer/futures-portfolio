import asyncio
import math
import sys
import json
import logging
import os
import time
import random
import shutil
from collections import deque
from typing import Dict, List, Any
from decimal import Decimal
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

# Load .env file
load_dotenv()

from core.connector import BinanceConnector
from core.calculator import PortfolioCalculator, initial_notional_after_fee
from core.executor import PortfolioExecutor, PAPER_COMMISSION_RATE, validate_paper_positions
from core.notifier import TelegramNotifier
from core.storage import safe_load_json as load_json, safe_save_json as save_json, safe_load_json_sync
from core import share_trail

from pathlib import Path

# ProcessPoolExecutor removed to reduce latency

def sync_read_json(path: str) -> Dict:
    return safe_load_json_sync(path, {})


def emit_signal(signal_type: str, ticker: str, is_paper: bool) -> None:
    """Создает пустой файл-флаг для супервайзера."""
    sig_dir = Path("signals")
    sig_dir.mkdir(exist_ok=True)
    mode_tag = "paper" if is_paper else "real"
    sig_path = sig_dir / f"{signal_type}_{mode_tag}_{ticker}.flag"
    try:
        sig_path.touch(exist_ok=True)
    except Exception as e:
        logging.error(f"Failed to emit signal {signal_type} for {ticker}: {e}")

async def self_kill_pm2(ticker: str, is_paper: bool) -> None:
    """Удаляет себя из PM2 перед выходом, чтобы предотвратить autorestart. Асинхронная версия."""
    prefix = "paper" if is_paper else "real"
    proc_name = f"{prefix}-{ticker.replace('USDT', '').lower()}"
    try:
        import sys
        cmd = f"pm2 delete {proc_name}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        logging.info(f"PM2 self-kill: deleted {proc_name}")
    except Exception as e:
        logging.warning(f"PM2 self-kill failed for {proc_name}: {e}")

def live_refusal_in_paper(base_ticker: str) -> str:
    """Single wording for the launch contract, shared by CLI and loop refusal."""
    return (
        f"🚨 Launch expects a live snapshot but effective mode is PAPER for {base_ticker}. "
        "No mode switch, migration, reset, state write or order is allowed; blocking."
    )


def _paper_collateral(paper_state: dict, base_ticker: str, price: float,
                      calc_tpv: Decimal, logger: logging.Logger) -> Decimal:
    calc_tpv = Decimal(str(calc_tpv))
    long_qty = abs(Decimal(str(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0))))
    short_qty = abs(Decimal(str(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0))))
    long_entry = Decimal(str(paper_state.get("long_entry_price", 0.0)))
    short_entry = Decimal(str(paper_state.get("short_entry_price", 0.0)))
    upnl = long_qty * (Decimal(str(price)) - long_entry)
    upnl += short_qty * (short_entry - Decimal(str(price)))
    expected = Decimal(str(paper_state.get("balance", 0.0))) + upnl
    # Calculator clamps non-positive TPV; keep the raw identity for the critical branch.
    if expected <= 0:
        return expected
    tolerance = max(Decimal('1e-8'), abs(expected) * Decimal('1e-12'))
    if abs(calc_tpv - expected) > tolerance:
        logger.error(f"Paper collateral identity mismatch: calc_tpv={calc_tpv} expected={expected}")
        return expected
    return calc_tpv


def _paper_guard_metrics(paper_state: dict, base_ticker: str, price: float,
                         calc_tpv: Decimal, logger: logging.Logger) -> tuple:
    collateral = _paper_collateral(paper_state, base_ticker, price, calc_tpv, logger)
    long_qty = abs(Decimal(str(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0))))
    short_qty = abs(Decimal(str(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0))))
    maintenance = (long_qty + short_qty) * Decimal(str(price)) * Decimal('0.004')
    if collateral <= 0:
        return collateral, maintenance, -100.0
    distance = float((collateral - maintenance) / collateral * Decimal('100'))
    return collateral, maintenance, distance


# A FUSE verdict must not rest on a book snapshot older than this
FUSE_BOOK_MAX_AGE_SEC = 5.0


async def _live_fuse_fill(connector, base_ticker: str, logger: logging.Logger):
    """Live FUSE verdict inputs: account taker rate plus a fresh stamped order book.
    Any unverified input => unconfirmed levels, so dependent reductions are blocked."""
    unconfirmed = {"bids": [], "asks": [], "taker": None}
    try:
        rates = await connector.get_commission_rates(base_ticker)
        taker = Decimal(str(rates["takerCommissionRate"]))
        if not Decimal('0') <= taker < 1:
            raise ValueError(f"taker rate {taker} out of [0, 1)")
    except Exception as e:
        logger.warning(f"FUSE commission rates unavailable for {base_ticker}: {e}. Live reductions unconfirmed.")
        return dict(unconfirmed)
    try:
        book = await connector.get_order_book(base_ticker, limit=20)
        stamp = book.get("_fetched_at")
        if stamp is None or time.time() - stamp > FUSE_BOOK_MAX_AGE_SEC:
            raise ValueError("order book snapshot is unstamped or stale")
    except Exception as e:
        logger.warning(f"FUSE order book unavailable for {base_ticker}: {e}. Live reductions unconfirmed.")
        return {**unconfirmed, "taker": taker}

    def _levels(key):
        return [(Decimal(str(p)), Decimal(str(q))) for p, q in book.get(key, [])]
    return {"bids": _levels("bids"), "asks": _levels("asks"), "taker": taker}


async def _reconcile_funding_leg(connector, halt_obj, leg: str, paper_state: dict,
                                 base_ticker: str, paper_mode: bool, logger) -> str:
    """Apply validated cumulative actuals; an unknown live outcome never authorizes retry."""
    if paper_mode:
        return "reissue"
    cs = halt_obj.close_state.get(leg)
    if cs is None:
        raise ValueError("Missing durable funding close intent")
    if cs.status == "quarantined":
        return "handled"
    side = leg.split("_")[-1]
    expected_side = "SELL" if side == "LONG" else "BUY"
    pos_key = f"{base_ticker}_{side}"
    try:
        if cs.execution_id:
            provider, symbol, order_id = cs.execution_id.split(":")
            if provider != "binance" or symbol != base_ticker:
                raise ValueError("Funding order identity does not match portfolio")
            order_id = int(order_id)
            order = await connector.get_order_status(symbol, order_id)
        elif cs.client_order_id and cs.submission_state != "ready":
            order = await connector.get_order_by_client_id(base_ticker, cs.client_order_id)
            order_id = int(order["orderId"])
        elif cs.client_order_id and cs.submission_state == "ready":
            positions = await connector.get_positions()
            actual = abs(Decimal(str(positions.get(pos_key, {}).get("qty", 0))))
            expected = abs(Decimal(str(paper_state["positions"].get(pos_key, 0))))
            if not actual.is_finite() or actual != expected or actual != cs.qty_remaining:
                raise ValueError("Prepared close does not match exchange and shadow position")
            return "reissue"
        else:
            raise ValueError("Unknown submission identity; operator reconciliation required")
        cs.reconciliation_payload = {"order": order}
        if (order.get("symbol") != base_ticker or int(order.get("orderId", -1)) != order_id
                or order.get("side") != expected_side or order.get("positionSide") != side):
            raise ValueError("Order identity, symbol or hedge side mismatch")
        if cs.client_order_id and order.get("clientOrderId") != cs.client_order_id:
            raise ValueError("Client order identity mismatch")
        filled = Decimal(str(order["executedQty"]))
        total = cs.qty_closed + cs.qty_remaining
        if not filled.is_finite() or filled < cs.qty_closed or filled > total:
            raise ValueError("Executed quantity outside durable intent")
        if Decimal(str(order["origQty"])) != total:
            raise ValueError("Order quantity does not match durable intent")
        status = order["status"]
        if status not in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"}:
            raise ValueError("Unknown order status")
        trades = await connector.get_order_trades(base_ticker, order_id)
        cs.reconciliation_payload["trades"] = trades
        trade_ids = set()
        quantity = Decimal("0")
        commission = Decimal("0")
        pnl = Decimal("0")
        obligations = {}
        for trade in trades:
            identity = trade["id"]
            if identity in trade_ids:
                raise ValueError("Duplicate trade identity")
            trade_ids.add(identity)
            if (int(trade["orderId"]) != order_id or trade["symbol"] != base_ticker
                    or trade["side"] != expected_side or trade["positionSide"] != side):
                raise ValueError("Trade identity or hedge side mismatch")
            qty = Decimal(str(trade["qty"]))
            fee = Decimal(str(trade["commission"]))
            realized = Decimal(str(trade["realizedPnl"]))
            price = Decimal(str(trade["price"]))
            if not all(v.is_finite() for v in (qty, fee, realized, price)) or qty <= 0 or price <= 0:
                raise ValueError("Invalid trade quantities or amounts")
            quantity += qty
            pnl += realized
            asset = trade["commissionAsset"]
            if asset == "USDT":
                commission += fee
            else:
                obligations[asset] = str(Decimal(obligations.get(asset, "0")) + fee)
        if quantity != filled:
            raise ValueError("Trade history incomplete relative to executed quantity")
        average = Decimal(str(order.get("avgPrice", 0)))
        if not average.is_finite() or (filled > 0 and average <= 0):
            raise ValueError("Invalid order average price")
        cs.fee_obligations = obligations
        if obligations:
            raise ValueError("Non-USDT fees require explicit valuation; facts retained")
        delta = filled - cs.qty_closed
        remaining_position = Decimal(str(paper_state["positions"].get(pos_key, 0))) - delta
        if remaining_position < 0:
            raise ValueError("Execution exceeds shadow position")
        balance = Decimal(str(paper_state["balance"])) + pnl - cs.realized_pnl - commission + cs.commission
        if not balance.is_finite() or not math.isfinite(float(balance)):
            raise ValueError("Invalid wallet accounting")
        paper_state["positions"][pos_key] = float(remaining_position)
        paper_state["balance"] = float(balance)
        if remaining_position == 0:
            paper_state[f"{side.lower()}_entry_price"] = 0.0
            paper_state[f"{side.lower()}_liquidation_price"] = 0.0
        cs.execution_id = f"binance:{base_ticker}:{order_id}"
        cs.qty_closed = filled
        cs.qty_remaining = total - filled
        cs.commission = commission
        cs.realized_pnl = pnl
        cs.fill_price = average
        cs.status = "filled" if cs.qty_remaining == 0 else "partial"
        cs.submission_state = "reconciled"
        if cs.qty_remaining > 0 and status not in ("NEW", "PARTIALLY_FILLED"):
            raise ValueError("Terminal order left an unclosed residual; no automatic resubmission")
    except Exception as error:
        cs.status = "quarantined"
        cs.reconciliation_error = str(error)
        logger.error(f"Funding reconciliation quarantined {leg}: {error}")
    return "handled"


async def _run_live_funding_closes(connector, executor, halt_obj, state, paper_state,
                                  base_ticker, state_path, wallet_path, journal,
                                  step_sizes, portfolio_cfg, logger, fresh=False):
    """Persist submission uncertainty before a single live request, then query actuals."""
    from uuid import uuid4
    from core.funding import save_funding_boundary

    def persist():
        save_funding_boundary(state_path, wallet_path, state, paper_state, journal, halt_obj)

    if fresh:
        for cs in halt_obj.close_state.values():
            if not cs.client_order_id and not cs.execution_id:
                cs.client_order_id = "fp_" + uuid4().hex
                cs.submission_state = "ready"
        persist()
    for leg, cs in halt_obj.close_state.items():
        if cs.status == "quarantined":
            continue
        side = leg.split("_")[-1]
        decision = await _reconcile_funding_leg(
            connector, halt_obj, leg, paper_state, base_ticker, False, logger)
        if decision == "reissue":
            if not cs.client_order_id or cs.submission_state != "ready" or cs.qty_remaining <= 0:
                cs.status = "quarantined"
                cs.reconciliation_error = "No durable prepared submission budget"
                persist()
                continue
            cs.submission_state = "submitting"
            persist()
            try:
                await executor.execute_market_order(
                    symbol=base_ticker, qty=cs.qty_remaining,
                    side="SELL" if side == "LONG" else "BUY", position_side=side,
                    reduce_only=True, step_size=Decimal(str(step_sizes.get(base_ticker, 0))),
                    min_notional=portfolio_cfg.get("min_notional_usdt", 6),
                    price=paper_state.get("last_price", 0), client_order_id=cs.client_order_id)
            except Exception as error:
                logger.error(f"Funding submission outcome unknown for {leg}: {error}")
            await _reconcile_funding_leg(
                connector, halt_obj, leg, paper_state, base_ticker, False, logger)
        persist()


def _apply_clean_slate(state: dict, paper_state: dict, base_ticker: str, target_cap: float, logger: logging.Logger) -> None:
    """Enforces clean slate: resets phantom balances and state to target_initial_cap."""
    paper_state['balance'] = target_cap
    paper_state['long_entry_price'] = 0.0
    paper_state['short_entry_price'] = 0.0
    paper_state['positions'] = {f"{base_ticker}_LONG": 0.0, f"{base_ticker}_SHORT": 0.0}
    state['tpv_ath'] = target_cap
    state['virt_qty'] = 0.0
    state['virt_debt'] = 0.0  # [FIX] Reset virt_debt to prevent real_equity desync
    state['rebalance_cycles'] = 0
    state['initial_tpv'] = target_cap
    state['reference_tpv'] = target_cap
    state['trailing_stop_violation_start'] = 0.0
    state['trailing_stop_paper_timeout_end'] = 0.0
    state['share_trail'] = share_trail.fresh_state()
    state['share_trail_receipts'] = []
    logger.info(f"🔄 State reset (TS flag preserved): clean start detected (no open positions)")

async def _update_final_metrics_for_exit(state: dict, state_file_path: str, total_tpv_final: Decimal, initial_tpv: Decimal, safe_calc_res: dict, cycles: int, logger: logging.Logger) -> None:
    """Updates and saves the final profit metrics in the state file before a bot exits."""
    try:
        logger.info(f"🔄 Санитизация состояния перед выходом. Финальный TPV: {float(total_tpv_final):.4f}")

        # Полное ребазирование метрик под финальное значение TPV
        # [FIX] initial_tpv НЕ должен уменьшаться при стопе — сохраняем текущее значение
        # из state, чтобы при рестарте бот стартовал с target_initial_cap из конфига.
        _current_initial_tpv = float(state.get("initial_tpv", float(total_tpv_final)))
        _current_reference_tpv = float(state.get("reference_tpv", float(total_tpv_final)))
        state.update({
            "last_tpv": float(total_tpv_final),
            "initial_tpv": _current_initial_tpv,
            "reference_tpv": _current_reference_tpv,
            "tpv_ath": max(float(total_tpv_final), _current_initial_tpv),
            "last_profit": float(total_tpv_final - initial_tpv),
            "total_pnl_pct": safe_calc_res.get("total_pnl_pct", 0.0),
            "last_update": time.time(),
            "rebalance_cycles": cycles,
            # Очистка триггеров скользящего стопа (но сохраняем сам факт срабатывания для супервайзера)
            "trailing_stop_violation_start": 0.0,
            "trailing_stop_paper_timeout_end": 0.0,
            "trailing_stop_triggered": True
        })

        await save_json(state_file_path, state)
        logger.info(f"✅ Final metrics saved before exit. Last Profit: {float(state['last_profit']):.2f}")
    except Exception as e:
        logger.error(f"Error saving final metrics before exit: {e}")

async def _handle_liquidation_recovery(connector, base_ticker, state, state_file_path,
                                        paper_state, paper_state_file_path,
                                        config_path, logger, notifier):
    """Persist a trading block; a missing leg alone does not prove liquidation."""
    state.update(startup_blocked=True, status="reconciliation_required",
                 startup_block_reason="Expected exchange position missing",
                 reconciliation_required_at=time.time())
    await save_json(state_file_path, state)
    logger.critical(f"Exchange position missing for {base_ticker}; trading blocked pending reconciliation")
    try:
        state["exchange_position_snapshot"] = await connector.get_position_risk()
        await save_json(state_file_path, state)
        await notifier.send_alert("POSITION RECONCILIATION REQUIRED",
                                  f"{base_ticker}: Expected position missing. Trading blocked; verify exchange fills.")
    except Exception as error:
        logger.error(f"Position reconciliation diagnostics failed; trading remains blocked: {error}")


async def _handle_liquidation_guard(
    pos_key: str, dist: float, liq_price: float,
    liquidation_distance_warn: float, liquidation_distance_crit: float,
    is_paper: bool, raw_positions, paper_state, connector,
    base_ticker: str, step_sizes: dict, notifier, logger,
    reason: str = "LIQUIDATION", price_label: str = "liq_price",
):
    """
    Shared liquidation guard handler for both paper and real modes.
    Paper: simulates price-based close (adjusts shadow_state balance).
    Real: sends actual market close order via Binance.
    """
    if liquidation_distance_crit != 0 and dist <= liquidation_distance_crit:
        logger.critical(
            f"🚨 {reason} CRITICAL [{'PAPER' if is_paper else 'REAL'}]: "
            f"{pos_key} distance {dist:.1f}% ({price_label}={liq_price:.8f}). Emergency close!"
        )
        try:
            await notifier.send_alert(
                f"🚨 {reason} IMMINENT",
                f"{pos_key}: distance {dist:.1f}% ({price_label}={liq_price:.8f}). Closing!"
            )
        except Exception:
            pass

        if is_paper:
            from core.paper_close import close_wallet
            closed = close_wallet(paper_state, base_ticker, liq_price,
                                  f"guard:{base_ticker}:{paper_state.get('balance')}", PAPER_COMMISSION_RATE)
            paper_state.clear()
            paper_state.update(closed)
            mode_tag = "PAPER"
        else:
            # Real mode: send actual close order for BOTH sides
            for _side in ("LONG", "SHORT"):
                _pos_key = f"{base_ticker}_{_side}"
                _close_side = "SELL" if _side == "LONG" else "BUY"
                _close_qty = abs(raw_positions.get(_pos_key, {}).get("qty", 0.0))
                if _close_qty > 0:
                    try:
                        await PortfolioExecutor(connector).execute_market_order(
                            symbol=base_ticker,
                            side=_close_side,
                            qty=_close_qty,
                            position_side=_side,
                            reduce_only=True
                        )
                        paper_state["positions"][_pos_key] = 0.0
                        logger.info(f"✅ Emergency closed {_pos_key} to avoid liquidation")
                    except Exception as close_err:
                        logger.error(f"Failed to emergency close {_pos_key}: {close_err}")
            mode_tag = "REAL"

        logger.info(
            f"✅ [{'PAPER' if is_paper else 'REAL'}] Emergency closed {pos_key} "
            f"(dist={dist:.1f}%, {price_label}={liq_price:.8f})"
        )

        # [SSOT] Delegate config mutation to supervisor via signal
        emit_signal("stop", base_ticker, is_paper=is_paper)
        logger.critical(
            f"🚫 {base_ticker} signal 'stop' emitted. "
            f"Awaiting supervisor to blacklist and remove from live_swarm."
        )

    elif liquidation_distance_warn != 0 and dist <= liquidation_distance_warn:
        logger.warning(
            f"⚠️ {reason} WARNING [{'PAPER' if is_paper else 'REAL'}]: "
            f"{pos_key} distance {dist:.1f}% ({price_label}={liq_price:.8f})"
        )


async def rebalance_loop(connector: BinanceConnector, config_path: str, state_file_path: str, paper_state_file_path: str, logger: logging.Logger, ticker_override: str = None, paper_mode_override: bool = None, expect_live_snapshot: bool = False):
    # [SSOT] Absolute Scope Safety - Initialize all variables at function start
    i = 0
    status_offset = random.randint(0, 99)
    target_initial = 0.0
    max_spread = 0.0015 # 0.15%
    max_velocity = 0.01 # 1.0%
    velocity_window = 60
    last_config_mtime = 0.0
    current_config = None
    
    # Обычное чтение конфига без блокировок
    config = safe_load_json_sync(config_path, {})
    if not config:
        logger.error(f"Aborting cycle: Failed to read config {config_path}")
        return

    if not config or "portfolios" not in config:
        logger.error(f"Aborting cycle: Invalid or missing config structure from {config_path}")
        return

    # Paper mode: override > config
    paper_mode = paper_mode_override if paper_mode_override is not None else config.get("paper_mode", False)
    
    # Приоритет тикера: override > config > default
    base_ticker = ticker_override if ticker_override else config.get("base_ticker", "BTCUSDT")

    # Launch contract: effective mode is decided before anything else, so a live
    # expectation can never silently degrade into a paper run (forgotten --real).
    # This invocation refuses itself only: managing PM2 would delete the running
    # paper session of the very ticker we were wrongly pointed at.
    if expect_live_snapshot and paper_mode:
        logger.critical(live_refusal_in_paper(base_ticker))
        while True:
            await asyncio.sleep(86400)

    # Initial parameter load
    portfolio_cfg = config["portfolios"][0]
    targets = portfolio_cfg["targets"]
    t_surplus = portfolio_cfg.get("rebalance_threshold_surplus", portfolio_cfg.get("rebalance_threshold", 0.02))
    t_deficit = portfolio_cfg.get("rebalance_threshold_deficit", t_surplus)

    check_interval = portfolio_cfg.get("check_interval_sec", 15)

    t_cfg = portfolio_cfg.get("ticker_thresholds", {}).get(base_ticker)
    if isinstance(t_cfg, dict):
        threshold_surplus = float(t_cfg.get("surplus", t_surplus))
        threshold_deficit = float(t_cfg.get("deficit", t_deficit))
    elif isinstance(t_cfg, (float, int)):
        threshold_surplus = threshold_deficit = float(t_cfg)
    else:
        threshold_surplus, threshold_deficit = float(t_surplus), float(t_deficit)

    siphoning_threshold_pct = portfolio_cfg.get("siphoning_threshold_pct", 0.0)
    reinvestment_ratio = portfolio_cfg.get("reinvestment_ratio", 0.0)
    trail_distance = float(config.get("rebalance_share_trail_distance", 0.0))
    if not math.isfinite(trail_distance) or trail_distance < 0:
        raise ValueError("rebalance_share_trail_distance must be a finite non-negative share fraction")
    
    # SSOT Capital: Use paper_initial_capital for PAPER, initial_capital for REAL
    if paper_mode:
        target_initial_cap = float(portfolio_cfg.get("paper_initial_capital", 100.0))
    else:
        target_initial_cap = float(portfolio_cfg.get("initial_capital", 86.0))
    
    max_capital_usdt = portfolio_cfg.get("max_capital_usdt", target_initial_cap)

    # Состояние синтетической доли и сейфа
    default_state = {
        "virt_qty": 0.0,
        "base_ticker": base_ticker,
        "siphoning_reserve": 0.0,
        "balance": target_initial_cap, # SSOT Balance
        "initial_tpv": 0.0,
        "reference_tpv": 0.0,  # Фиксированная база для гистерезиса
        "tpv_ath": target_initial_cap,  # FIX: start ATH at initial_capital, not 0
        "trailing_stop_violation_start": 0.0,
        "trailing_stop_paper_timeout_end": 0.0,
        "rebalance_cycles": 0,
        "last_rebalance_price": 0.0,
        "cash_invariant_violation": None,
        "initial_build_pending": None,
        "share_trail": None,
        "share_trail_spec": None,
        "share_trail_receipts": [],
        "started_at": time.time(),
        # B1 funding state
        "funding_model": "legacy_constant",
        "funding_journal": [],
        "funding_halt": {},
        "funding_paid": 0.0,
    }
    state = await load_json(state_file_path, default_state)
    if not isinstance(state, dict):
        if not expect_live_snapshot:
            # A non-object snapshot must never silently become a fresh legacy start.
            raise ValueError(f"Strategy snapshot is not a JSON object: {state_file_path}")
        # Trust boundary: the raw gate below preserves these bytes and WAL recovery
        # (which runs after this point) must restore the real pair first.
        logger.critical(f"🚨 Strategy snapshot is not a JSON object: {state_file_path}")
        state = default_state

    # Safe load of the two-sided share-trailing state: missing/corrupt rows fall back to unarmed.
    state["share_trail"] = share_trail.load_state(state.get("share_trail"))
    state.setdefault("share_trail_receipts", [])
    _startup_trail_spec = share_trail.spec_fingerprint(trail_distance, targets, threshold_surplus, threshold_deficit)
    if share_trail.spec_mismatch(state.get("share_trail_spec"), _startup_trail_spec):
        state["share_trail"] = share_trail.fresh_state()
    state["share_trail_spec"] = _startup_trail_spec

    # Persisted startup block — checked immediately after strategy state load,
    # before any migration, ticker-change reset, wallet fallback or Clean Slate.
    # Preserves all accounting facts on disk; blocks trading unconditionally.
    if not paper_mode and state.get("rebalance_inflight"):
        # An uncertain submission must survive restart without a second order or ledger reset.
        state["startup_blocked"] = True
        pending_book = await load_json(paper_state_file_path, {})
        try:
            if not isinstance(pending_book, dict):
                raise ValueError("Pending execution wallet is not a JSON object")
            share_trail.restore_checkpoint(state, pending_book)
        except ValueError as error:
            state["rebalance_inflight"]["checkpoint_error"] = str(error)
        await save_json(state_file_path, state)
    if state.get("startup_blocked") or state.get("status") in ("portfolio_default", "modeled_liquidation", "reconciliation_required"):
        _state_ticker = state.get("base_ticker", "?")
        logger.critical(
            f"🚨 [PAPER] Persisted startup_blocked for {base_ticker} "
            f"(state ticker={_state_ticker}). "
            f"Operator must verify state integrity and remove startup_blocked flag."
        )
        await self_kill_pm2(base_ticker, paper_mode)
        while True:
            await asyncio.sleep(86400)

    # Initialize paper_state for shadow balance tracking (Used in both PAPER and REAL modes for isolation)
    default_paper_state = {
        "balance": target_initial_cap,
        "positions": {f"{base_ticker}_LONG": 0.0, f"{base_ticker}_SHORT": 0.0},
        "last_price": 0.0,
        "base_ticker": base_ticker,
        "long_entry_price": 0.0,
        "short_entry_price": 0.0
    }
    _default_paper_state = {} if state.get("live_funding") is not None else default_paper_state
    paper_state = await load_json(paper_state_file_path, _default_paper_state)
    if not isinstance(paper_state, dict):
        if not expect_live_snapshot:
            raise ValueError(f"Wallet snapshot is not a JSON object: {paper_state_file_path}")
        logger.critical(f"🚨 Wallet snapshot is not a JSON object: {paper_state_file_path}")
        paper_state = _default_paper_state

    # ── B1 Phase 0: WAL file recovery BEFORE any migration/reset ───────
    from core.funding import recover_funding_boundary, \
        RECOVERY_ABSENT, RECOVERY_RECOVERED, RECOVERY_FAILED
    _recovery_status, _rs, _rw = recover_funding_boundary(
        state_file_path, paper_state_file_path)
    if _recovery_status == RECOVERY_RECOVERED:
        state.update(_rs)
        paper_state.update(_rw)
        logger.critical("Funding recovery: state restored from WAL intent.")
    elif _recovery_status == RECOVERY_FAILED:
        logger.critical(
            f"🚨 Funding intent FAILED at {state_file_path}. "
            f"Startup blocked — operator must resolve."
        )
        await self_kill_pm2(base_ticker, paper_mode)
        while True:
            await asyncio.sleep(86400)

    if paper_mode:
        try:
            validate_paper_positions(paper_state)
        except ValueError as error:
            state["startup_blocked"] = True
            state["startup_block_reason"] = str(error)
            await save_json(state_file_path, state)
            raise

    # Launch contract: after WAL recovery the expected live pair must be usable.
    # A lost, corrupt or mismatched pair blocks trading instead of degrading into
    # the fresh legacy start that the default-loading path would otherwise allow.
    if expect_live_snapshot:
        from core.funding import live_snapshot_pair_error, atomic_save_json
        _pair_error = live_snapshot_pair_error(state_file_path, paper_state_file_path, base_ticker)
        if _pair_error:
            logger.critical(
                f"🚨 Live snapshot pair unusable for {base_ticker}: {_pair_error}. "
                "Trading blocked until the operator restores the pair; migration, "
                "reset and wallet fallback are refused."
            )
            _block_doc: Dict[str, Any] = {}
            _preserve_ok = True
            if os.path.exists(state_file_path):
                try:
                    with open(state_file_path, "r", encoding="utf-8") as _fh:
                        _current = json.load(_fh)
                    if not isinstance(_current, dict):
                        raise ValueError("strategy snapshot is not a JSON object")
                    _block_doc = _current
                except Exception:
                    # Never overwrite previous corruption evidence: pick a free name,
                    # and if the bytes cannot be moved aside keep them untouched
                    # (the raw gate re-blocks on every later start) instead of
                    # replacing the only copy with the block document.
                    _preserve_ok = False
                    _free = f"{state_file_path}.corrupted"
                    _n = 0
                    while os.path.exists(_free):
                        _n += 1
                        _free = f"{state_file_path}.corrupted.{_n}"
                    try:
                        os.replace(state_file_path, _free)
                        _preserve_ok = True
                        logger.critical(f"Corrupt strategy snapshot preserved at {_free}")
                    except Exception as _io_err:
                        logger.critical(
                            f"🚨 Cannot preserve corrupt strategy snapshot {state_file_path}: "
                            f"{_io_err}. Original files kept untouched; startup stays blocked."
                        )
            if _preserve_ok:
                _block_doc.setdefault("base_ticker", base_ticker)
                _block_doc["startup_blocked"] = True
                _block_doc["startup_block_reason"] = _pair_error
                atomic_save_json(state_file_path, _block_doc)
            await self_kill_pm2(base_ticker, paper_mode)
            while True:
                await asyncio.sleep(86400)

    if paper_state.get("live_funding_snapshot") is not None:
        source = state.get("live_funding")
        if not isinstance(source, dict) or paper_state["live_funding_snapshot"] != {
                "symbol": source.get("symbol"), "snapshot_ms": source.get("snapshot_ms")}:
            raise ValueError("Live wallet requires its matching strategy snapshot")
    if state.get("live_funding") is not None:
        if (paper_mode or state["live_funding"].get("symbol") != base_ticker
                or state.get("base_ticker") != base_ticker
                or paper_state.get("base_ticker") != base_ticker
                or not {"balance", "positions", "long_entry_price", "short_entry_price"}.issubset(paper_state)
                or not math.isfinite(float(state.get("initial_tpv", 0)))
                or float(state.get("initial_tpv", 0)) <= 0):
            raise ValueError("Live funding snapshot cannot be migrated or reset")

    # Detect active funding halt BEFORE startup validation so that
    # Phase 2 recovery can complete reductions uninterrupted.
    _funding_halt_active = (
        isinstance(state.get("funding_halt"), dict)
        and state.get("funding_halt", {}).get("active", False)
    )

    # Ensure balance exists (Shadow Balance Migration Guard)
    if "balance" not in paper_state:
        fallback_bal = float(state.get("initial_tpv", target_initial_cap))
        if fallback_bal <= 0: fallback_bal = target_initial_cap
        paper_state["balance"] = fallback_bal
        logger.warning(f"⚠️ 'balance' missing in {paper_state_file_path}. Initialized to {fallback_bal}")

    # Если тикер сменился, сбрасываем количество виртуальных монет и начальный TPV
    if state.get("base_ticker") != base_ticker:
        logger.info(f"Ticker in state changed from {state.get('base_ticker')} to {base_ticker}. Resetting virt_qty, initial TPV and ATH.")
        state["virt_qty"] = 0.0
        state["virt_debt"] = 0.0
        state["initial_tpv"] = 0.0 
        state["reference_tpv"] = 0.0
        state["tpv_ath"] = 0.0
        state["trailing_stop_violation_start"] = 0.0
        state["trailing_stop_paper_timeout_end"] = 0.0
        state["base_ticker"] = base_ticker
        state["started_at"] = time.time()
        state["share_trail"] = share_trail.fresh_state()
        state["share_trail_receipts"] = []
        await save_json(state_file_path, state)

    if paper_state.get("base_ticker") != base_ticker:
        logger.info(f"Ticker in paper state changed from {paper_state.get('base_ticker')} to {base_ticker}. Resetting.")
        paper_state["last_price"] = 0.0
        paper_state["base_ticker"] = base_ticker
        paper_state["balance"] = max_capital_usdt if max_capital_usdt > 0 else 10000.0
        paper_state["positions"] = {f"{base_ticker}_LONG": 0.0, f"{base_ticker}_SHORT": 0.0}
        paper_state["long_entry_price"] = 0.0
        paper_state["short_entry_price"] = 0.0
        await save_json(paper_state_file_path, paper_state)

    if share_trail.restore_checkpoint(state, paper_state):
        await save_json(state_file_path, state)
    if "positions" not in paper_state: paper_state["positions"] = {}
    if f"{base_ticker}_LONG" not in paper_state["positions"]: paper_state["positions"][f"{base_ticker}_LONG"] = 0.0
    if f"{base_ticker}_SHORT" not in paper_state["positions"]: paper_state["positions"][f"{base_ticker}_SHORT"] = 0.0
    if "long_entry_price" not in paper_state: paper_state["long_entry_price"] = 0.0
    if "short_entry_price" not in paper_state: paper_state["short_entry_price"] = 0.0

    # [Architectural Safeguard] State Isolation Protocol
    # Если реальных позиций нет, это чистый старт (или рестарт после стопа).
    # Жестко затираем фантомные балансы, чтобы не сломать Trailing Stop.
    # При чистом старте баланс ВСЕГДА сбрасывается на target_initial_cap из конфига.
    if (not paper_mode and not _funding_halt_active and state.get("live_funding") is None
            and not any(abs(float(qty)) > 1e-10 for qty in paper_state["positions"].values())):
        try:
            raw_positions = await connector.get_positions()
            ticker_positions = {k: v["qty"] for k, v in raw_positions.items() if base_ticker in k}
            total_position_size = sum(abs(float(v)) for v in ticker_positions.values())

            if total_position_size == 0:
                _clean_balance = float(paper_state.get('balance', target_initial_cap))
                if abs(_clean_balance - target_initial_cap) > 0.1 or float(state.get('virt_qty', 0)) > 0:
                    logger.warning(f"🧹 Phantom Buffer detected for {base_ticker}. Enforcing Clean Slate for baseline!")
                    _apply_clean_slate(state, paper_state, base_ticker, min(_clean_balance, target_initial_cap), logger)
                    await save_json(paper_state_file_path, paper_state)
                    await save_json(state_file_path, state)
        except Exception as e:
            logger.error(f"State Isolation Protocol failed: {e}")
            # [FIX] Fallback: if we can't check positions, still enforce clean slate
            # when balance is out of sync with target_initial_cap
            _clean_balance = float(paper_state.get('balance', target_initial_cap))
            if abs(_clean_balance - target_initial_cap) > 0.1:
                logger.warning(f"🧹 [FALLBACK] Enforcing Clean Slate for {base_ticker} (balance desync: {_clean_balance:.2f} vs {target_initial_cap:.2f})")
                _apply_clean_slate(state, paper_state, base_ticker, min(_clean_balance, target_initial_cap), logger)
                await save_json(paper_state_file_path, paper_state)
                await save_json(state_file_path, state)
    elif paper_mode and not state.get("initial_build_pending"):
        # Pending initial fills and their fees must survive a paper restart.
        _p_l = abs(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0))
        _p_s = abs(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0))
        if _p_l < 1e-10 and _p_s < 1e-10:
            if _funding_halt_active:
                logger.info(
                    f"Funding halt active, both legs closed — preserving "
                    f"wallet={paper_state.get('balance', 0):.4f}, "
                    f"V={state.get('virt_debt', 0):.4f}. Clean Slate suppressed."
                )
            elif not (state.get("started_at") and float(state.get("initial_tpv", 0)) > 0):
                _clean_balance = float(paper_state.get('balance', target_initial_cap))
                if abs(_clean_balance - target_initial_cap) > 0.1 or float(state.get('virt_qty', 0)) > 0:
                    logger.warning(f"🧹 [PAPER] Phantom Buffer detected for {base_ticker}. Enforcing Clean Slate!")
                    _apply_clean_slate(state, paper_state, base_ticker, target_initial_cap, logger)
                    await save_json(paper_state_file_path, paper_state)
                    await save_json(state_file_path, state)
        else:
            # Both legs non-zero AND both entries > 0 → legitimate hedged session.
            # Balance drift from initial_cap is normal (realized PnL, commissions).
            _both_legs = _p_l > 1e-10 and _p_s > 1e-10
            _le = float(paper_state.get("long_entry_price", 0.0))
            _se = float(paper_state.get("short_entry_price", 0.0))
            if not (_both_legs and _le > 0 and _se > 0):
                if _funding_halt_active:
                    logger.info(
                        f"Funding halt active — trusting Phase 2 recovery; "
                        f"bypassing normal startup validation "
                        f"(L={_p_l:.4f} S={_p_s:.4f})."
                    )
                else:
                    logger.critical(
                        f"🚨 [PAPER] Ambiguous startup state for {base_ticker}: "
                        f"L={_p_l:.4f} S={_p_s:.4f} Le={_le:.4f} Se={_se:.4f}. "
                        f"All accounting facts preserved; trading blocked pending operator diagnosis."
                    )
                    state["startup_blocked"] = True
                    await save_json(paper_state_file_path, paper_state)
                    await save_json(state_file_path, state)
                    await self_kill_pm2(base_ticker, paper_mode)
                    while True:
                        await asyncio.sleep(86400)

    virt_qty = float(state.get("virt_qty", 0.0))
    siphoning_reserve = float(state.get("siphoning_reserve", 0.0))
    initial_tpv = float(state.get("initial_tpv", 0.0))
    reference_tpv = float(state.get("reference_tpv", 0.0))
    tpv_ath = float(state.get("tpv_ath", 0.0))
    cycles = state.get("rebalance_cycles", 0)

    # Инфо о бирже
    exchange_info = await connector.get_exchange_info()
    step_sizes = {s["symbol"]: float(f["stepSize"]) for s in exchange_info["symbols"] for f in s["filters"] if f["filterType"] == "LOT_SIZE"}
    min_notionals = {s["symbol"]: float(f.get("minNotional") or f.get("notional")) for s in exchange_info["symbols"] for f in s["filters"] if f["filterType"] == "MIN_NOTIONAL"}
    
    equity_trailing_stop_pct = config.get("equity_trailing_stop_pct", 0.0)
    equity_trailing_stop_activation_pct = config.get("equity_trailing_stop_activation_pct", 0.0)
    max_drawdown_limit = config.get("max_drawdown_limit", 0.5)
    margin_warning = portfolio_cfg.get("margin_ratio_warning", 5.0)
    margin_critical = portfolio_cfg.get("margin_ratio_critical", 2.0)

    # Инициализация уведомлений
    notifier = TelegramNotifier()
    config_base = os.path.splitext(os.path.basename(config_path))[0]
    
    # Инициализация экзекутора
    max_ops = config.get("max_orders_per_second", 10)
    executor = PortfolioExecutor(connector, base_ticker=base_ticker, max_orders_per_second=max_ops)

    # ── B1 Phase 2: Funding-halt close recovery (executor ready) ────────
    # Phase 1 (WAL file restore) runs before Clean Slate above.
    _halt_dict = state.get("funding_halt")
    if isinstance(_halt_dict, dict) and _halt_dict.get("active"):
        from core.funding import (
            FundingHalt, get_pending_close_legs, record_close_result,
            save_funding_boundary, FundingJournal)
        halt_obj = FundingHalt.from_dict(_halt_dict)

        # Quarantined legs are terminal — operator must resolve before trading resumes.
        _quarantined = [leg for leg, cs in halt_obj.close_state.items()
                        if cs.status == "quarantined"]
        if _quarantined:
            logger.critical(
                f"🚨 Quarantined close legs for {base_ticker}: {_quarantined}. "
                f"Operator must resolve before trading can resume."
            )
            state["startup_blocked"] = True
            await save_json(paper_state_file_path, paper_state)
            await save_json(state_file_path, state)
            await self_kill_pm2(base_ticker, paper_mode)
            while True:
                await asyncio.sleep(86400)

        if not paper_mode:
            await _run_live_funding_closes(
                connector, executor, halt_obj, state, paper_state, base_ticker,
                state_file_path, paper_state_file_path,
                FundingJournal.from_list(state.get("funding_journal")),
                step_sizes, portfolio_cfg, logger)
            await self_kill_pm2(base_ticker, paper_mode)
            while True:
                await asyncio.sleep(86400)

        pending = get_pending_close_legs(halt_obj)
        if pending:
            logger.critical(f"🚨 Funding halt: {len(pending)} legs pending close.")
            for leg in pending:
                side = leg.split("_")[-1]
                pos_key = f"{base_ticker}_{side}"
                qty = abs(paper_state["positions"].get(pos_key, 0.0))
                if qty <= 1e-10:
                    record_close_result(halt_obj, leg, "filled",
                                         Decimal('0'), Decimal('0'),
                                         Decimal('0'), Decimal('0'))
                    continue
                # A leg whose close order is identified is reconciled against the exchange,
                # never re-ordered: the same fill must be accounted exactly once.
                verdict = await _reconcile_funding_leg(
                    connector, halt_obj, leg, paper_state, base_ticker, paper_mode, logger)
                if verdict == "handled":
                    state["funding_halt"] = halt_obj.to_dict()
                    state["funding_journal"] = state.get("funding_journal", [])
                    journal = FundingJournal.from_list(state.get("funding_journal"))
                    save_funding_boundary(state_file_path, paper_state_file_path,
                                           state, paper_state, journal, halt_obj)
                    continue
                price_val = paper_state.get("last_price", 0) or 0
                close_acts = [{"type": "ORDER", "key": leg, "symbol": pos_key,
                    "base_symbol": base_ticker, "position_side": side,
                    "leverage": 7, "diff_usdt": -abs(qty * price_val),
                    "is_reduction": True}]
                cr = (await executor.execute_actions(
                    close_acts, price_val, True, portfolio_cfg,
                    step_sizes, paper_state))[0]
                cr_status = cr.get("status", "ERROR")
                if cr_status in ("SUCCESS", "SUCCESS_LIMIT", "SUCCESS_FALLBACK") and cr.get("qty"):
                    cq = Decimal(str(cr.get("qty", 0)))
                    cp = Decimal(str(cr.get("trade_pnl", 0)))
                    cc = Decimal(str(cr.get("commission", 0)))
                    old_q = Decimal(str(paper_state["positions"].get(pos_key, 0.0)))
                    is_reduce = ((cr.get("side") == "SELL" and side == "LONG")
                                 or (cr.get("side") == "BUY" and side == "SHORT"))
                    new_q = old_q - cq if is_reduce else old_q + cq
                    entry_key = f"{side.lower()}_entry_price"
                    if new_q <= 0:
                        new_q = Decimal('0')
                        paper_state[entry_key] = 0.0
                    paper_state["positions"][pos_key] = float(new_q)
                    paper_state["balance"] = float(
                        (Decimal(str(paper_state["balance"])) + cp - cc)
                        .quantize(Decimal('0.0001')))
                    leg_status = "filled" if new_q <= 0 else "partial"
                    record_close_result(halt_obj, leg, leg_status, cq,
                                         Decimal(str(cr.get("price", 0))),
                                         cc, cp, qty_remaining=new_q,
                                         execution_id=cr.get("execution_id"))
                else:
                    # Recovery close failure is terminal — quarantine, don't retry.
                    record_close_result(halt_obj, leg, "quarantined",
                                         Decimal('0'), Decimal('0'),
                                         Decimal('0'), Decimal('0'))
                # Per-leg durable save
                state["funding_halt"] = halt_obj.to_dict()
                state["funding_journal"] = state.get("funding_journal", [])
                journal = FundingJournal.from_list(state.get("funding_journal"))
                save_funding_boundary(state_file_path, paper_state_file_path,
                                       state, paper_state, journal, halt_obj)
        logger.critical(f"🚨 Funding halt for {base_ticker}. Trading blocked.")
        await self_kill_pm2(base_ticker, paper_mode)
        while True:
            await asyncio.sleep(86400)

    # Hedge Mode, Leverage and Margin Type Guard
    if not paper_mode:
        try:
            is_hedge = await connector.get_hedge_mode()
            if not is_hedge:
                msg = f"CRITICAL: Hedge Mode is DISABLED on Binance for {base_ticker}. Please enable it to start the bot."
                logger.error(msg)
                asyncio.create_task(notifier.send_alert("STARTUP ERROR", msg))
                return
            logger.info("Hedge Mode verified.")

            # Set leverage from config (targets.BASE_LONG.leverage)
            try:
                _lev = int(targets.get("BASE_LONG", {}).get("leverage", 7))
                await connector.set_leverage(base_ticker, _lev)
                logger.info(f"Leverage set to {_lev}x for {base_ticker}")
            except Exception as e:
                logger.warning(f"Could not set leverage for {base_ticker}: {e}")

            try:
                await connector.set_margin_type(base_ticker, "CROSSED")
                logger.info(f"Margin Type set to CROSS for {base_ticker}")
            except Exception as e:
                logger.error(f"Could not set margin type for {base_ticker}: {e}")
                # CRITICAL: without correct margin type, liq_price from Binance is unreliable
                # Do NOT trade if margin type cannot be set
                asyncio.create_task(notifier.send_alert("STARTUP ERROR", f"Could not set margin type for {base_ticker}: {e}. Bot will NOT start."))
                return

        except Exception as e:
            logger.error(f"Failed to verify exchange settings: {e}")
            asyncio.create_task(notifier.send_alert("STARTUP ERROR", f"Could not verify exchange settings: {e}"))
            return

    asyncio.create_task(notifier.send_message(f"🚀 <b>Bot Started</b>: <code>{config_base}</code> ({base_ticker})\nMode: {'PAPER' if paper_mode else 'REAL'}"))

    # [Safety] Absolute Scope Safety - Initialize all variables before the loop
    i = 0
    status_offset = random.randint(0, 99)
    target_initial = target_initial_cap
    velocity_cfg = portfolio_cfg.get("safety_guards", {})
    max_spread = velocity_cfg.get("max_spread_pct", 0.15) / 100
    max_velocity = velocity_cfg.get("max_price_velocity_pct", 1.0) / 100
    velocity_window = velocity_cfg.get("velocity_window_sec", 60)
    price_history = deque() # Будет хранить (timestamp, price)
    last_io_save = time.time()

    # Sanitize ATH on clean start (when supervisor has explicitly cleared the TS flag)
    if not state.get("trailing_stop_triggered", False):
        _pos_long = abs(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0))
        _pos_short = abs(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0))
        if _pos_long < 1e-10 and _pos_short < 1e-10:
            _initial = Decimal(str(state.get("initial_tpv", target_initial_cap)))
            _current_ath = Decimal(str(state.get("tpv_ath", 0.0)))
            if _current_ath > _initial:
                _new_ath = _initial
                logger.info(f"🔄 Clean start detected. Resetting tpv_ath from {_current_ath} to {_new_ath} to enable TS activation.")
                state["tpv_ath"] = float(_new_ath)
                state["trailing_stop_violation_start"] = 0.0
                # Synchronous save before entering the main loop
                await save_json(state_file_path, state)

    try:
        while True:
            state_dirty = False
            paper_state_dirty = False
            any_success = False
            try:
                if state.get("startup_blocked") or state.get("status") in ("portfolio_default", "modeled_liquidation", "reconciliation_required"):
                    logger.critical("Unreconciled execution: trading remains blocked")
                    await asyncio.sleep(86400)
                    continue
                if paper_mode:
                    validate_paper_positions(paper_state)
                # Failsafe: if restarted by PM2 with triggered state, exit immediately to trigger Reaper
                if state.get("trailing_stop_triggered", False):
                    logger.critical(f"🚨 Trailing Stop already triggered for {base_ticker}. Entering Zombie Mode.")
                    await self_kill_pm2(base_ticker, paper_mode)
                    while True:
                        await asyncio.sleep(86400)

                # Dynamic config reload
                try:
                    current_mtime = os.path.getmtime(config_path)
                    if current_mtime != last_config_mtime:
                        # Unblock the Event Loop only if file changed
                        current_config = await asyncio.to_thread(sync_read_json, config_path)
                        last_config_mtime = current_mtime
                        portfolio_cfg = current_config["portfolios"][0]
                        targets = portfolio_cfg["targets"]
                        t_surplus = portfolio_cfg.get("rebalance_threshold_surplus", portfolio_cfg.get("rebalance_threshold", 0.02))
                        t_deficit = portfolio_cfg.get("rebalance_threshold_deficit", t_surplus)
                        check_interval = portfolio_cfg.get("check_interval_sec", 15)

                        t_cfg = portfolio_cfg.get("ticker_thresholds", {}).get(base_ticker)
                        if isinstance(t_cfg, dict):
                            threshold_surplus = float(t_cfg.get("surplus", t_surplus))
                            threshold_deficit = float(t_cfg.get("deficit", t_deficit))
                        elif isinstance(t_cfg, (float, int)):
                            threshold_surplus = threshold_deficit = float(t_cfg)
                        else:
                            threshold_surplus, threshold_deficit = float(t_surplus), float(t_deficit)

                        siphoning_threshold_pct = portfolio_cfg.get("siphoning_threshold_pct", 0.0)
                        reinvestment_ratio = portfolio_cfg.get("reinvestment_ratio", 0.0)

                        if paper_mode:
                            target_initial = float(portfolio_cfg.get("paper_initial_capital", 100.0))
                        else:
                            target_initial = float(portfolio_cfg.get("initial_capital", 86.0))

                        max_capital_usdt = portfolio_cfg.get("max_capital_usdt", target_initial)
                        max_drawdown_limit = current_config.get("max_drawdown_limit", 0.5)
                        equity_trailing_stop_pct = current_config.get("equity_trailing_stop_pct", 0.0)
                        equity_trailing_stop_timeout_sec = current_config.get("equity_trailing_stop_timeout_sec", 0.0)
                        trail_distance = float(current_config.get("rebalance_share_trail_distance", 0.0))
                        if not math.isfinite(trail_distance) or trail_distance < 0:
                            raise ValueError("rebalance_share_trail_distance must be finite and non-negative")
                        _trail_spec = share_trail.spec_fingerprint(trail_distance, targets, threshold_surplus, threshold_deficit)
                        if share_trail.spec_mismatch(state.get("share_trail_spec"), _trail_spec):
                            # Incompatible observations: reset the trail, keep the rest of the state.
                            state["share_trail"] = share_trail.fresh_state()
                            state["share_trail_receipts"] = []
                            state_dirty = True
                            logger.warning("⚙️ Share-trail spec changed (distance/targets/thresholds) — trail observations reset.")
                        state["share_trail_spec"] = _trail_spec

                        # Обновляем параметры защит
                        guards_cfg = portfolio_cfg.get("safety_guards", {})
                        max_spread = float(guards_cfg.get("max_spread_pct", 0.15)) / 100.0
                        max_velocity = float(guards_cfg.get("max_price_velocity_pct", 1.0)) / 100.0
                        velocity_window = int(guards_cfg.get("velocity_window_sec", 60))
                        net_move_block_pct = float(guards_cfg.get("net_move_block_pct", 1.5)) / 100.0
                        net_move_window = int(guards_cfg.get("net_move_window_sec", 30))

                        logger.debug(f"⚙️ Config reloaded. Active Thresholds for {base_ticker}: Surplus {threshold_surplus*100:.2f}%, Deficit {threshold_deficit*100:.2f}%, Max Spread: {max_spread*100:.2f}%")

                    if i % 60 == 0:
                        logger.debug(f"Thresholds running at: Surplus {threshold_surplus*100:.2f}%, Deficit {threshold_deficit*100:.2f}%")

                except Exception as e:
                    logger.error(f"Error reloading config: {e}. Using previous values.")

                # Dynamic initial_tpv update from config
                # Для тикеров в toxic_blacklist ребазирование зависит от прибыльности:
                #   убыток (last_profit < 0) → ребаз на initial_capital из конфига
                #   прибыль (last_profit >= 0) → ребаз на last_tpv (сохраняет прибыль)
                if current_config is None:
                    current_config = await asyncio.to_thread(sync_read_json, config_path)
                _bl_key = "toxic_blacklist_paper" if paper_mode else "toxic_blacklist_real"
                _bl = current_config.get(_bl_key, {})
                _now = time.time()
                _in_blacklist = base_ticker in _bl and _bl.get(base_ticker, 0) > _now
                _rebased = False

                if state.get("live_funding") is not None:
                    source = state["live_funding"]
                    if source.get("blocked") or _in_blacklist or initial_tpv != target_initial:
                        from core.funding import save_funding_boundary, FundingHalt, FundingJournal
                        source["blocked"] = source.get("blocked") or "Snapshot/config rebase requires operator reconciliation"
                        save_funding_boundary(state_file_path, paper_state_file_path, state, paper_state,
                                              FundingJournal.from_list(state.get("funding_journal")),
                                              FundingHalt.from_dict(state.get("funding_halt")))
                        continue

                if _in_blacklist and not paper_mode:
                    _last_profit = float(state.get("last_profit", 0.0))
                    if _last_profit < 0:
                        # Убыточный бот → полный сброс на начальный капитал из конфига
                        _rebase_target = target_initial
                        logger.info(f"🔄 Blacklist rebase (LOSS): {base_ticker} initial_tpv {initial_tpv} -> {_rebase_target}")
                    else:
                        # Прибыльный бот → сохраняем накопленную прибыль
                        _rebase_target = float(state.get("last_tpv", target_initial))
                        logger.info(f"🔄 Blacklist rebase (PROFIT): {base_ticker} initial_tpv {initial_tpv} -> {_rebase_target} (last_tpv)")
                    initial_tpv = _rebase_target
                    reference_tpv = initial_tpv
                    state["initial_tpv"] = initial_tpv
                    state["reference_tpv"] = reference_tpv
                    # Sync shadow balance to match rebased initial_tpv
                    paper_state["balance"] = _rebase_target
                    paper_state_dirty = True
                    state_dirty = True
                    _rebased = True
                elif initial_tpv > target_initial and target_initial > 0 and not paper_mode:
                    logger.info(f"🔄 Initial Capital changed in config: {initial_tpv} -> {target_initial}. Updating base.")
                    initial_tpv = target_initial
                    reference_tpv = initial_tpv
                    state["initial_tpv"] = initial_tpv
                    state["reference_tpv"] = reference_tpv
                    # [FIX] Do NOT overwrite paper_state["balance"] — it tracks real
                    # simulated equity. Only update initial_tpv/reference_tpv so
                    # calculator uses correct baseline.
                    paper_state_dirty = True
                    state_dirty = True
                    _rebased = True
                # Rebase means a new economic session: incompatible trail observations.
                if _rebased:
                    state["share_trail"] = share_trail.fresh_state()
                    state["share_trail_receipts"] = []
                    state_dirty = True

                # Use Mark Price for TPV and rebalance triggers as recommended by Audit
                prices = await connector.get_mark_prices([base_ticker])
                price = prices.get(base_ticker)
                if not price: raise Exception(f"Could not fetch {base_ticker} mark price")
                
                # -------------------------------------------------------------------------
                # [SAFETY] VELOCITY & TREND GUARD
                # -------------------------------------------------------------------------
                now = time.time()
                price_history.append((now, price))
                # Увеличиваем окно истории до 300 секунд для анализа тренда
                prune_cut = max(300, int(((portfolio_cfg.get("trend_add_block") or {}).get("window_bars", 0)) * 60))
                while price_history and (now - price_history[0][0]) > prune_cut:
                    price_history.popleft()
                
                if len(price_history) > 1:
                    # 1. VELOCITY GUARD (% за окно 60с)
                    v_point = next((p for p in price_history if now - p[0] <= velocity_window), price_history[0])
                    old_t, old_p = v_point
                    velocity = abs(price - old_p) / old_p
                    if velocity > max_velocity:
                        if i % 5 == 0:
                            logger.warning(f"🚀 Velocity Guard: {base_ticker} is moving too fast ({velocity*100:.2f}% in {int(now-old_t)}s). Blocking trades.")
                        await asyncio.sleep(check_interval); i += 1; continue

                    # 2. TREND GUARD (Efficiency Filter)
                    if len(price_history) > 10:
                        p_list = [p[1] for p in price_history]
                        net_move = abs(p_list[-1] - p_list[0])
                        total_path = sum(abs(p_list[j] - p_list[j-1]) for j in range(1, len(p_list)))
                        trend_eff = (net_move / total_path) if total_path > 0 else 0
                        
                        trend_min_move = guards_cfg.get("trend_min_move_pct", 0.7) / 100.0
                        trend_eff_thresh = guards_cfg.get("trend_eff_threshold", 0.35)
                        
                        # Trend Guard: если прошли > min_move% и эффективность > threshold — это тренде
                        if (net_move / p_list[0] > trend_min_move) and trend_eff > trend_eff_thresh:
                            if i % 5 == 0:
                                logger.warning(f"🚫 Trend Guard: {base_ticker} toxic move (Eff: {trend_eff:.2f}, Move: {net_move/p_list[0]*100:.2f}%). Freezing.")
                            await asyncio.sleep(check_interval); i += 1; continue

                # -------------------------------------------------------------------------
                # [SAFETY] NET MOVE GUARD (NMG) — Pump/Dump Protection
                # -------------------------------------------------------------------------
                # If price moved > threshold in one direction within short window,
                # block ALL rebalance actions. Prevents closing positions at fake
                # profit/loss during fast unidirectional moves.
                nmg_triggered = False
                if len(price_history) > 1:
                    nmg_point = next((p for p in price_history if now - p[0] <= net_move_window), None)
                    if nmg_point:
                        nmg_old_t, nmg_old_p = nmg_point
                        net_move = abs(price - nmg_old_p) / nmg_old_p
                        if net_move_block_pct > 0 and net_move > net_move_block_pct:
                            nmg_triggered = True
                            if i % 5 == 0:
                                logger.warning(f"🛡️ Net Move Guard: {base_ticker} moved {net_move*100:.2f}% in {int(now-nmg_old_t)}s (limit {net_move_block_pct*100:.1f}%). Blocking ALL actions.")
                            await asyncio.sleep(check_interval); i += 1; continue

                # -------------------------------------------------------------------------
                # [SAFETY] SPREAD GUARD (Only for REAL mode or detailed Paper simulation)
                # -------------------------------------------------------------------------
                if not paper_mode:
                    try:
                        depth = await connector.get_order_book(base_ticker, limit=5)
                        best_bid = float(depth['bids'][0][0]) if depth['bids'] else 0
                        best_ask = float(depth['asks'][0][0]) if depth['asks'] else 0
                        if best_bid > 0 and best_ask > 0:
                            spread = (best_ask - best_bid) / best_bid
                            if spread > max_spread:
                                if i % 5 == 0:
                                    logger.warning(f"⚠️ Spread Guard: {base_ticker} spread too wide ({spread*100:.3f}% > {max_spread*100:.3f}%). Blocking trades.")
                                await asyncio.sleep(check_interval)
                                i += 1
                                continue
                    except Exception as e:
                        logger.error(f"Failed to check order book for spread: {e}")

                # Fetch current data for deviation calculation
                l_entry: float = 0.0
                s_entry: float = 0.0
                real_equity: float = 0.0
                positions: Dict[str, float] = {}

                # ALWAYS use shadow balance (paper_state) for real_equity calculation to support shared accounts
                # This ensures per-bot PnL isolation and prevents double-counting of account-wide profit
                if paper_mode:
                    paper_state["last_price"] = price
                    l_qty = abs(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0))
                    s_qty = abs(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0))
                    l_entry = paper_state.get("long_entry_price", price)
                    s_entry = paper_state.get("short_entry_price", price)
                    m_info = {}
                else:
                    # In REAL mode, we sync positions from exchange but keep balance in shadow
                    raw_positions = await connector.get_positions()
                    if any(
                        abs(float(paper_state["positions"].get(f"{base_ticker}_{side}", 0))) > 1e-10
                        and abs(float(raw_positions.get(f"{base_ticker}_{side}", {}).get("qty", 0))) < 1e-10
                        for side in ("LONG", "SHORT")
                    ):
                        await _handle_liquidation_recovery(
                            connector, base_ticker, state, state_file_path,
                            paper_state, paper_state_file_path, config_path, logger, notifier)
                        while True:
                            await asyncio.sleep(86400)
                    # Filter for this ticker only
                    ticker_positions = {k: v["qty"] for k, v in raw_positions.items() if base_ticker in k}
                    l_qty = abs(ticker_positions.get(f"{base_ticker}_LONG", 0.0))
                    s_qty = abs(ticker_positions.get(f"{base_ticker}_SHORT", 0.0))
                    l_entry = raw_positions.get(f"{base_ticker}_LONG", {}).get("entry_price", 0.0)
                    s_entry = raw_positions.get(f"{base_ticker}_SHORT", {}).get("entry_price", 0.0)
                    
                    # For logging and safety only
                    m_info = await connector.get_margin_ratio()

                # -------------------------------------------------------------------------
                # [SAFETY] GUARD #4: LIQUIDATION DISTANCE MONITOR
                # -------------------------------------------------------------------------
                # Checks how close each position is to its liquidation price.
                # If distance < threshold → emergency close that specific position.
                # Works for BOTH paper (simulated liq price) and real (exchange liq price).
                liquidation_distance_warn = portfolio_cfg.get("liquidation_distance_warn_pct", 15.0)
                liquidation_distance_crit = portfolio_cfg.get("liquidation_distance_crit_pct", 8.0)
                post_factum_liquidation_check = portfolio_cfg.get("post_factum_liquidation_check", True)
                if not isinstance(post_factum_liquidation_check, bool):
                    raise ValueError("post_factum_liquidation_check must be a boolean")

                if not paper_mode and raw_positions:
                    # REAL mode + CROSSED margin: Binance returns unreliable liq_price
                    # Skip exchange liq check — use margin ratio (m_info) instead
                    # Liquidation risk is managed by max_drawdown_limit + trailing stop
                    pass

                elif paper_mode:
                    # PAPER mode: diagnostic isolated approximation only; external collateral is zero.
                    # The canonical collateral guard below owns close/stop/blacklist decisions.
                    try:
                        _mmr_val = Decimal('0.004')
                        _lv_l = Decimal(str(targets.get("BASE_LONG", {}).get("leverage", 7)))
                        _lv_s = Decimal(str(targets.get("BASE_SHORT", {}).get("leverage", 7)))

                        for pos_side in ("LONG", "SHORT"):
                            pos_key = f"{base_ticker}_{pos_side}"
                            qty = abs(paper_state["positions"].get(pos_key, 0.0))
                            if qty == 0:
                                continue

                            entry_key = f"{pos_side.lower()}_entry_price"
                            entry = Decimal(str(paper_state.get(entry_key, price)))
                            _lv = _lv_l if pos_side == "LONG" else _lv_s

                            liq_price_key = f"{pos_side.lower()}_liquidation_price"
                            if pos_side == "LONG":
                                _iso = entry * (Decimal('1') - Decimal('1') / _lv + _mmr_val)
                            else:
                                _iso = entry * (Decimal('1') + Decimal('1') / _lv - _mmr_val)

                            paper_state[liq_price_key] = float(_iso.quantize(Decimal('1e-8')))
                            paper_state_dirty = True
                    except Exception as e:
                        logger.error(f"Paper liquidation diagnostic update failed: {e}")

                # Calculate isolated PnL and Equity
                # [SSOT] TPV = Cash + Virtual Value. Cash is paper_state["balance"].
                # In calculator.py, real_equity is treated as Wallet Balance.
                positions = paper_state["positions"] if paper_mode else {k: v for k, v in ticker_positions.items()}

                if state.get("live_funding") is None and (virt_qty == 0 or initial_tpv == 0):
                    if initial_tpv == 0:
                        # For shared accounts, we MUST use assigned initial_capital as base
                        initial_tpv = portfolio_cfg.get("initial_capital", paper_state["balance"])
                        reference_tpv = initial_tpv
                        logger.info(f"Initialized TPV base: {initial_tpv:.2f} (Isolated Shadow Balance)")

                    if virt_qty == 0:
                        target_v_share = Decimal(str(targets["VIRTUAL"]["share"]))
                        if target_v_share <= 0:
                            # VIRTUAL is disabled (share=0) — skip initialization spam
                            virt_qty = 0.0
                            state["virt_debt"] = 0.0
                        else:
                            # КОРРЕКТНАЯ ИНИЦИАЛИЗАЦИЯ:
                            # 1. Считаем сколько монет купить на целевую долю
                            initial_cap = Decimal(str(initial_tpv))
                            dec_price = Decimal(str(price))

                            virt_qty_dec = (target_v_share * initial_cap) / dec_price
                            virt_cost = float(virt_qty_dec * dec_price)

                            # 2. Фиксируем ДОЛГ виртуальной ноги (стоимость покупки)
                            # Мы НЕ вычитаем это из balance в paper_state, так как balance
                            # представляет полный Wallet Balance (как на бирже).
                            state["virt_debt"] = state.get("virt_debt", 0.0) + virt_cost
                            state_dirty = True

                            # 3. Фиксируем количество
                            virt_qty = float(virt_qty_dec)

                            logger.info(f"🚀 Initialized Virtual: {virt_qty} units (Cost: {virt_cost:.2f} USDT added to virt_debt)")

                    state.update({
                        "virt_qty": virt_qty,
                        "base_ticker": base_ticker, "siphoning_reserve": siphoning_reserve,
                        "initial_tpv": initial_tpv, "reference_tpv": reference_tpv
                    })
                    state_dirty = True

                # [SSOT RECONCILIATION]
                # In both modes, we use the shadow balance (paper_state["balance"]) for TPV calculation.
                # This ensures that adding/removing funds from the Binance wallet does not affect the bot's TPV.
                virt_debt = Decimal(str(state.get("virt_debt", 0.0)))
                real_equity = float(Decimal(str(paper_state["balance"])) - virt_debt)

                if real_equity < 0:
                    real_equity = 0.0

                if not paper_mode:
                    # In REAL mode, we still fetch wallet_balance for margin safety checks, 
                    # but we NO LONGER overwrite paper_state["balance"] with it.
                    wallet_balance = float(m_info.get("total_wallet_balance", 0.0))

                if not paper_mode and state.get("live_funding") is not None:
                    from core.funding import poll_live_funding, save_funding_boundary, FundingHalt, FundingJournal
                    try:
                        live_journal = await poll_live_funding(connector, state, base_ticker, int(time.time() * 1000))
                        save_funding_boundary(state_file_path, paper_state_file_path, state, paper_state,
                                              live_journal, FundingHalt.from_dict(state.get("funding_halt")))
                    except Exception as error:
                        state["live_funding"]["blocked"] = str(error)
                        logger.critical("Live funding reconciliation blocked: %s", error)
                        save_funding_boundary(state_file_path, paper_state_file_path, state, paper_state,
                                              FundingJournal.from_list(state.get("funding_journal")),
                                              FundingHalt.from_dict(state.get("funding_halt")))
                        continue

                # --- B1: Funding BEFORE calculator (per canonical order §3) ---
                funding_model = state.get("funding_model", "legacy_constant")
                if funding_model == "signed_actual":
                    from core.funding import FundingJournal, apply_funding_paper
                    journal = FundingJournal.from_list(state.get("funding_journal"))
                    for event in journal.get_received():
                        if event.event_time > time.time():
                            continue
                        apply_funding_paper(event.signed_amount, paper_state, state)
                        journal.mark_applied(event.event_id)
                        state["funding_journal"] = journal.to_list()
                        state_dirty = True
                        paper_state_dirty = True
                        v_now = Decimal(str(state.get("virt_debt", 0)))
                        if state.get("live_funding") is not None and v_now >= 0:
                            from core.funding import save_funding_boundary, FundingHalt
                            try:
                                save_funding_boundary(state_file_path, paper_state_file_path, state, paper_state,
                                                      journal, FundingHalt.from_dict(state.get("funding_halt")))
                            except Exception:
                                state["live_funding"]["blocked"] = "Funding commit failed; restart recovery required"
                                raise
                        if v_now < 0:
                            from core.funding import (
                                FundingHalt, commit_funding_halt_with_intents,
                                begin_close_leg, record_close_result,
                                save_funding_boundary)
                            lq = Decimal(str(abs(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0))))
                            sq = Decimal(str(abs(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0))))
                            halt_obj = commit_funding_halt_with_intents(
                                FundingHalt(), event.event_id, -v_now, lq, sq)
                            state["funding_halt"] = halt_obj.to_dict()
                            state["funding_journal"] = journal.to_list()
                            state_dirty = True
                            logger.critical(f"🚨 Funding shortfall: V={float(v_now):.4f}. Closing.")
                            # Commit halt BEFORE any close (durable boundary)
                            save_funding_boundary(
                                state_file_path, paper_state_file_path,
                                state, paper_state, journal, halt_obj)
                            if not paper_mode:
                                await _run_live_funding_closes(
                                    connector, executor, halt_obj, state, paper_state,
                                    base_ticker, state_file_path, paper_state_file_path,
                                    journal, step_sizes, portfolio_cfg, logger, fresh=True)
                                await self_kill_pm2(base_ticker, paper_mode)
                                while True:
                                    await asyncio.sleep(86400)
                            for side in ("LONG", "SHORT"):
                                pos_key = f"{base_ticker}_{side}"
                                qty = abs(paper_state["positions"].get(pos_key, 0.0))
                                if qty <= 1e-10:
                                    continue
                                # Execute close (intent already registered by commit_funding_halt_with_intents)
                                close_acts = [{"type": "ORDER", "key": f"BASE_{side}",
                                    "symbol": pos_key, "base_symbol": base_ticker,
                                    "position_side": side, "leverage": 7,
                                    "diff_usdt": -abs(qty * price), "is_reduction": True}]
                                cr = (await executor.execute_actions(
                                    close_acts, price, True, portfolio_cfg,
                                    step_sizes, paper_state))[0]
                                cr_status = cr.get("status", "ERROR")
                                if cr_status in ("SUCCESS", "SUCCESS_LIMIT", "SUCCESS_FALLBACK") and cr.get("qty"):
                                    cq = Decimal(str(cr.get("qty", 0)))
                                    cp = Decimal(str(cr.get("trade_pnl", 0)))
                                    cc = Decimal(str(cr.get("commission", 0)))
                                    old_q = Decimal(str(paper_state["positions"].get(pos_key, 0.0)))
                                    new_q = (old_q - cq) if (cr.get("side") == "SELL" and side == "LONG") or (cr.get("side") == "BUY" and side == "SHORT") else (old_q + cq)
                                    entry_key = f"{side.lower()}_entry_price"
                                    if new_q <= 0:
                                        new_q = Decimal('0')
                                        paper_state[entry_key] = 0.0
                                        paper_state[f"{side.lower()}_liquidation_price"] = 0.0
                                    paper_state["positions"][pos_key] = float(new_q.quantize(Decimal('1e-8')))
                                    paper_state["balance"] = float(
                                        (Decimal(str(paper_state["balance"])) + cp - cc)
                                        .quantize(Decimal('0.0001')))
                                    paper_state_dirty = True
                                    leg_status = "filled" if new_q <= 0 else "partial"
                                    record_close_result(halt_obj, f"BASE_{side}", leg_status,
                                                         cq, Decimal(str(cr.get("price", price))),
                                                         cc, cp,
                                                         qty_remaining=new_q,
                                                         execution_id=cr.get("execution_id"))
                                    logger.info(f"📝 Funding close: {cr.get('side')} {float(cq)} {pos_key}"
                                                f" @ {cr.get('price', price):.8g}"
                                                f" PnL={float(cp):+.4f} comm={float(cc):.4f}"
                                                f" remain={float(new_q)}")
                                else:
                                    record_close_result(halt_obj, f"BASE_{side}",
                                                         cr_status.lower(), Decimal('0'),
                                                         Decimal('0'), Decimal('0'), Decimal('0'),
                                                         qty_remaining=Decimal(str(qty)))
                                    logger.error(f"Funding close {side}: {cr_status}")
                                # Save after each leg fill
                                state["funding_halt"] = halt_obj.to_dict()
                                save_funding_boundary(
                                    state_file_path, paper_state_file_path,
                                    state, paper_state, journal, halt_obj)
                            await self_kill_pm2(base_ticker, paper_mode)
                            while True:
                                await asyncio.sleep(86400)
                if isinstance(state.get("funding_halt"), dict) and state["funding_halt"].get("active"):
                    logger.critical(f"🚨 Funding halt active for {base_ticker}.")
                    await self_kill_pm2(base_ticker, paper_mode)
                    while True:
                        await asyncio.sleep(86400)

                # Re-fetch funding-sensitive values AFTER funding block (V/wallet may have changed).
                virt_debt = Decimal(str(state.get("virt_debt", 0.0)))
                real_equity = float(Decimal(str(paper_state["balance"])) - virt_debt)
                if real_equity < 0:
                    real_equity = 0.0

                # Direct synchronous call to PortfolioCalculator to reduce latency
                initial_build_pending = state.get("initial_build_pending")
                ignore_limits = (
                    abs(positions.get(f"{base_ticker}_LONG", 0)) + abs(positions.get(f"{base_ticker}_SHORT", 0)) == 0
                    or bool(initial_build_pending)
                )
                
                # Fetch min_notional once to reuse
                config_min = portfolio_cfg.get("min_notional_usdt", current_config.get("min_notional_usdt"))
                if config_min is None:
                    raise ValueError("min_notional_usdt must be set in config.json")
                exchange_min = min_notionals.get(base_ticker, config_min)
                active_min_notional = max(config_min, exchange_min * 1.02)  # 2% buffer for dynamic Binance minimums

                fuse_fill = None
                if not paper_mode:
                    fuse_fill = await _live_fuse_fill(connector, base_ticker, logger)

                calc = PortfolioCalculator(
                    positions=positions,
                    spot_price=price,
                    real_equity=real_equity,
                    virt_qty=virt_qty,
                    virt_debt=float(virt_debt),
                    base_ticker=base_ticker,
                    siphoning_reserve=siphoning_reserve,
                    targets=targets,
                    initial_capital=initial_tpv,
                    long_entry_price=l_entry,
                    short_entry_price=s_entry,
                    last_rebalance_price=state.get("last_rebalance_price", 0.0),
                    min_notional=active_min_notional,
                    # live FUSE gate must price real closing costs, not zero
                    commission_pct=float(PAPER_COMMISSION_RATE),
                    fuse_fill=fuse_fill,
                )
                if not paper_mode:
                    # mandatory live re-gate for the limit path: a missing rate stays None so the
                    # executor may not fall back to market without a confirmed closing
                    _gate_fee = float(fuse_fill["taker"]) if fuse_fill.get("taker") is not None else None
                    portfolio_cfg["fuse_gate"] = {
                        "LONG": {"entry_price": l_entry, "taker_fee": _gate_fee},
                        "SHORT": {"entry_price": s_entry, "taker_fee": _gate_fee},
                    }
                # PnL Guard removed — in 50/50 hedge, one leg is always negative when price moves
                # Blocking surplus sell prevents profit-taking during normal hedge operation
                allow_surplus_sell = True

                trail_state = state["share_trail"] if trail_distance > 0 else None
                trail_snapshot_before = None
                if trail_state is not None:
                    trail_snapshot_before = tuple(
                        tuple(trail_state[key][field] for field in ("peak", "trough"))
                        for key in ("BASE_LONG", "BASE_SHORT"))

                calc_res = calc.calculate_rebalance(targets, threshold_surplus, threshold_deficit, calc.tpv, ignore_limits, allow_surplus_sell,
                                                    fuse_enabled=portfolio_cfg.get("fuse_enabled", True),
                                                    share_trail=trail_state, trail_distance=trail_distance)

                if trail_snapshot_before is not None:
                    trail_snapshot_after = tuple(
                        tuple(trail_state[key][field] for field in ("peak", "trough"))
                        for key in ("BASE_LONG", "BASE_SHORT"))
                    if trail_snapshot_after != trail_snapshot_before:
                        state_dirty = True

                tpv_total = calc_res["total_tpv"]
                tpv_active = calc_res["tpv"]
                actions = calc_res["actions"]

                if (paper_mode and state.get("cash_invariant_violation")
                        and calc.val_cash.is_finite() and calc.val_cash >= 0):
                    logger.warning(
                        "Cash invariant restored: CASH=%s; increases unblocked. Previous violation: %s",
                        calc.val_cash, state["cash_invariant_violation"],
                    )
                    state["cash_invariant_violation"] = None
                    state_dirty = True
                    await save_json(state_file_path, state)

                if initial_build_pending:
                    missing = set(initial_build_pending.get("missing", []))
                    actions = [
                        action for action in actions
                        if action.get("key") in missing and not action.get("is_reduction", False)
                    ]

                # Trend-add block: additions opposite a one-way window move are skipped (mirrors backtest guard).
                _t_cfg = portfolio_cfg.get("trend_add_block") or {}
                _t_pct = _t_cfg.get("pct")
                if actions and _t_pct:
                    _t_sec = int(_t_cfg.get("window_sec", int(_t_cfg.get("window_bars", 120)) * 60))  # backtest bars are minute-scale
                    _t_ref = next((pt for pt in price_history if now - pt[0] <= _t_sec), None)
                    if _t_ref and _t_ref[1]:
                        _t_move = (price - _t_ref[1]) / _t_ref[1]
                        if abs(_t_move) >= float(_t_pct):
                            _t_opposite = "BASE_SHORT" if _t_move > 0 else "BASE_LONG"
                            _t_blocked = [a for a in actions if a.get("key") == _t_opposite and not a.get("is_reduction", False)]
                            if _t_blocked:
                                logger.warning(
                                    f"Trend-add block: {base_ticker} moved {_t_move*100:+.2f}% vs {_t_sec}s window; "
                                    f"adds on {_t_opposite} blocked ({len(_t_blocked)} action(s))."
                                )
                                actions = [a for a in actions if a not in _t_blocked]

                if paper_mode:
                    collateral, maintenance, distance = _paper_guard_metrics(
                        paper_state, base_ticker, price, calc.tpv, logger
                    )
                    predictive_liquidation = liquidation_distance_crit != 0 and distance <= liquidation_distance_crit
                    hard_liquidation = (not predictive_liquidation and post_factum_liquidation_check
                                        and maintenance > 0 and collateral <= maintenance)
                    if collateral <= 0 or hard_liquidation:
                        terminal_status = "portfolio_default" if collateral <= 0 else "modeled_liquidation"
                        if hard_liquidation and collateral > 0:
                            paper_state["balance"] = 0.0
                            for side in ("LONG", "SHORT"):
                                paper_state["positions"][f"{base_ticker}_{side}"] = 0.0
                                paper_state[f"{side.lower()}_entry_price"] = 0.0
                                paper_state[f"{side.lower()}_liquidation_price"] = 0.0
                            state["liquidation_loss_total"] = float(state.get("liquidation_loss_total", 0)) + float(collateral)
                            state["virt_qty"] = 0.0
                            state["virt_debt"] = 0.0
                        final_tpv = (Decimal('0') if terminal_status == "modeled_liquidation" else collateral)
                        final_tpv += Decimal(str(state.get("siphoning_reserve", 0)))
                        initial = Decimal(str(state["initial_tpv"]))
                        state.update(status=terminal_status, startup_blocked=True,
                                     startup_block_reason=terminal_status, stopped_at=time.time(),
                                     last_tpv=float(final_tpv), last_profit=float(final_tpv - initial),
                                     total_pnl_pct=float((final_tpv / initial - 1) * 100),
                                     terminal_price=price, terminal_collateral=float(collateral),
                                     terminal_maintenance=float(maintenance))
                        from core.funding import save_funding_boundary, load_funding_state
                        journal, halt = load_funding_state(state)
                        save_funding_boundary(state_file_path, paper_state_file_path, state, paper_state, journal, halt)
                        logger.warning(f"PAPER {terminal_status}: TPV={final_tpv}; trading blocked until manual reconciliation")
                        continue
                    elif predictive_liquidation:
                        logger.critical(
                            f"🚨 PAPER COLLATERAL CRITICAL: distance={distance:.2f}% "
                            f"collateral={collateral:.4f} maintenance={maintenance:.4f}"
                        )
                        await _handle_liquidation_guard(
                            pos_key="PORTFOLIO", dist=distance, liq_price=price,
                            liquidation_distance_warn=liquidation_distance_warn,
                            liquidation_distance_crit=liquidation_distance_crit,
                            is_paper=True, raw_positions=None,
                            paper_state=paper_state, connector=None,
                            base_ticker=base_ticker, step_sizes=step_sizes,
                            notifier=notifier, logger=logger,
                            reason="PAPER COLLATERAL", price_label="price",
                        )
                        state["initial_build_pending"] = None
                        state["trailing_stop_triggered"] = True
                        state["paper_close_complete"] = True
                        state["last_tpv"] = float(paper_state["balance"]) + float(state.get("siphoning_reserve", 0))
                        state["last_profit"] = state["last_tpv"] - float(state["initial_tpv"])
                        state["share_trail"] = share_trail.fresh_state()
                        state["share_trail_receipts"] = []
                        actions = []
                        paper_state_dirty = True
                        state_dirty = True
                        from core.funding import save_funding_boundary, load_funding_state
                        journal, halt = load_funding_state(state)
                        save_funding_boundary(state_file_path, paper_state_file_path, state, paper_state, journal, halt)
                        logger.critical(
                            "PAPER COLLATERAL CRITICAL: trading halted pending supervisor stop"
                        )
                        # Skipping the rest of the cycle prevents any opening before Zombie Mode.
                        continue
                    elif liquidation_distance_warn != 0 and distance <= liquidation_distance_warn:
                        logger.warning(
                            f"⚠️ PAPER COLLATERAL WARNING: distance={distance:.2f}% "
                            f"collateral={collateral:.4f} maintenance={maintenance:.4f}"
                        )

                if ignore_limits and paper_mode:
                    # initial sizing must reserve the taker fee or CASH ends below zero
                    for action in actions:
                        if action.get("key") in ("BASE_LONG", "BASE_SHORT"):
                            lev = Decimal(str(action.get("leverage", 1)))
                            adjusted = initial_notional_after_fee(
                                abs(float(action["diff_usdt"])), float(lev), float(PAPER_COMMISSION_RATE)
                            )
                            action["diff_usdt"] = adjusted
                            action["approved_usdt"] = adjusted
                            action["diff_equity"] = adjusted / float(lev)

                now = time.time()

                # EMERGENCY STOP: If total_tpv (including SAFE) drops below max_drawdown_limit % of tpv_ath (high-water mark)
                _dd_base = tpv_ath if tpv_ath > 0 else initial_tpv
                drawdown_threshold = _dd_base * (1 - max_drawdown_limit / 100)
                if _dd_base > 0 and tpv_total < drawdown_threshold:
                    msg = f"CRITICAL: Total Equity {tpv_total:.2f} (including SAFE) is less than {drawdown_threshold:.2f} ({max_drawdown_limit}% DD from ATH={_dd_base:.2f}). EMERGENCY STOP!"
                    logger.critical(msg)
                    asyncio.create_task(notifier.send_alert("EMERGENCY STOP", msg))
                    state["share_trail"] = share_trail.fresh_state()
                    state["share_trail_receipts"] = []
                    state_dirty = True
                    
                    # Проверяем прибыль относительно глобального начального капитала
                    global_initial = initial_tpv
                    if tpv_total < global_initial:
                        emit_signal("stop", base_ticker, paper_mode)
                        logger.info(f"Sent STOP signal. Total Equity {tpv_total:.2f} < Global Initial {global_initial:.2f}. Ticker blacklisted.")
                    else:
                        emit_signal("exit", base_ticker, paper_mode)
                        logger.info(f"Sent EXIT signal. Total Equity {tpv_total:.2f} >= Global Initial {global_initial:.2f}. Ticker goes to probation.")
                        
                        # Устанавливаем таймаут пробации для прибыльного Emergency Stop
                        probation_days = current_config.get("probation_period_days", 0.041)
                        state["trailing_stop_paper_timeout_end"] = now + probation_days * 86400
                        state["trailing_stop_triggered"] = True

                    # --- CRITICAL: Update final metrics BEFORE emergency stop and exit ---
                    await _update_final_metrics_for_exit(state, state_file_path, Decimal(str(tpv_total)), Decimal(str(initial_tpv)), calc_res, cycles, logger)
                    # --------------------------------------------------------------------
                    try:
                        await emergency_stop(connector, config_path, state_file_path, paper_state_file_path, logger, ticker_override=base_ticker, paper_mode=paper_mode, close_only=True)
                    finally:
                        await self_kill_pm2(base_ticker, paper_mode)
                    while True:
                        await asyncio.sleep(86400)

                if tpv_ath == 0 or tpv_total > tpv_ath:
                    tpv_ath = tpv_total
                    # The session allocation may be smaller than the configured cap.
                    if tpv_ath < initial_tpv:
                        tpv_ath = initial_tpv
                    state["tpv_ath"] = tpv_ath
                    state_dirty = True

                if equity_trailing_stop_pct > 0 and tpv_ath > 0:
                    # Trailing stop activates only when ATH exceeds activation threshold
                    activation_threshold = initial_tpv * (1 + equity_trailing_stop_activation_pct / 100)
                    if tpv_ath < activation_threshold:
                        # Not yet activated — reset violation and skip
                        if state.get("trailing_stop_violation_start", 0.0) > 0:
                            state["trailing_stop_violation_start"] = 0.0
                            state_dirty = True
                    else:
                        drawdown_pct = (1 - tpv_total / tpv_ath) * 100
                        if drawdown_pct >= equity_trailing_stop_pct:
                            violation_start = state.get("trailing_stop_violation_start", 0.0)
                            if violation_start == 0:
                                violation_start = now
                                state["trailing_stop_violation_start"] = violation_start
                                state_dirty = True
                                logger.warning(f"Trailing Stop threshold breached ({drawdown_pct:.2f}%). Timeout: {equity_trailing_stop_timeout_sec}s")

                            elapsed = now - violation_start
                            if elapsed >= equity_trailing_stop_timeout_sec:
                                msg = f"Trailing Stop triggered: {drawdown_pct:.2f}% drop from ATH for {elapsed:.1f}s. Closing all positions for {base_ticker}."
                                logger.warning(f"!!! [STOP] {msg}")
                                asyncio.create_task(notifier.send_alert("STOP LOSS", msg))

                                # Realize PnL and close positions
                                if paper_mode:
                                    from core.paper_close import close_wallet
                                    paper_state = close_wallet(paper_state, base_ticker, price,
                                                               f"trailing:{state.get('started_at')}:{cycles}", PAPER_COMMISSION_RATE)
                                    from core.funding import save_funding_boundary, load_funding_state
                                    tpv_total = float(paper_state["balance"]) + float(state.get("siphoning_reserve", 0))
                                    state["last_tpv"] = tpv_total
                                    state["last_profit"] = tpv_total - float(state["initial_tpv"])
                                    state["paper_close_complete"] = True
                                    journal, halt = load_funding_state(state)
                                    save_funding_boundary(state_file_path, paper_state_file_path, state, paper_state, journal, halt)
                                for pos_key, qty in positions.items():
                                    if paper_mode:
                                        continue
                                    if qty == 0 or base_ticker not in pos_key: continue
                                    side = "SELL" if qty > 0 else "BUY"
                                    step_size = step_sizes.get(base_ticker, 0.0)

                                    # Calculate realized PnL for shadow balance
                                    p_qty = abs(paper_state["positions"].get(pos_key, 0.0))
                                    if p_qty > 0:
                                        if "LONG" in pos_key:
                                            pnl = p_qty * (price - paper_state.get("long_entry_price", price))
                                        else:
                                            pnl = p_qty * (paper_state.get("short_entry_price", price) - price)
                                        paper_state["balance"] += pnl
                                        paper_state["positions"][pos_key] = 0.0

                                    if not paper_mode:
                                        # In REAL mode, close real position
                                        await PortfolioExecutor(connector).execute_market_order(
                                            symbol=pos_key.split('_')[0],
                                            qty=Decimal(str(abs(qty))),
                                            side=side,
                                            step_size=Decimal(str(step_size)),
                                            reduce_only=True,
                                            position_side=pos_key.split('_')[1] if '_' in pos_key else "BOTH",
                                            min_notional=Decimal('0')
                                        )

                                # Realize state in shadow balance
                                paper_state["long_entry_price"] = 0.0
                                paper_state["short_entry_price"] = 0.0
                                await save_json(paper_state_file_path, paper_state)

                                # Жестко фиксируем смерть по трейлингу для HEAL Guard
                                state["trailing_stop_triggered"] = True
                                state["share_trail"] = share_trail.fresh_state()
                                state["share_trail_receipts"] = []
                                state_dirty = True

                                # --- CRITICAL: Update final metrics BEFORE state resets and exit ---
                                await _update_final_metrics_for_exit(state, state_file_path, Decimal(str(tpv_total)), Decimal(str(initial_tpv)), calc_res, cycles, logger)
                                # --------------------------------------------------------------------

                                # Set paper probation timeout (from probation_period_days)
                                probation_days = current_config.get("probation_period_days", 0.041)
                                timeout_end = now + probation_days * 86400
                                # Trailing stop timeout end should be set AFTER _update_final_metrics_for_exit if we want it to persist,
                                # but _update_final_metrics_for_exit clears it.
                                # Actually, requirements say: "Fully reset trailing stop tracking fields ... to 0.0/False to ensure clean state generation."
                                # So supervisor should handle the timeout if needed.
                                logger.info(f"Post-stop paper probation end: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timeout_end))}")

                                # Эмитируем сигнал остановки для супервайзера
                                global_initial = initial_tpv
                                if tpv_total < global_initial:
                                    emit_signal("stop", base_ticker, paper_mode)
                                    logger.info(f"Sent STOP signal. Total Equity {tpv_total:.2f} < Global Initial {global_initial:.2f}. Ticker blacklisted.")
                                else:
                                    # Trailing stop triggered with PROFIT
                                    # Delegate toxic_blacklist logic entirely to supervisor via IPC signal
                                    emit_signal("exit", base_ticker, paper_mode)
                                    logger.info(f"Sent EXIT signal. Total Equity {tpv_total:.2f} >= Global Initial {global_initial:.2f}.")

                                # --- CRITICAL: Update final metrics BEFORE state resets and exit ---
                                await _update_final_metrics_for_exit(state, state_file_path, Decimal(str(tpv_total)), Decimal(str(initial_tpv)), calc_res, cycles, logger)
                                # --------------------------------------------------------------------

                                # TERMINAL ACTION: Execute emergency liquidation immediately to prevent race condition with supervisor
                                logger.critical(f"Initiating synchronous emergency liquidation for {base_ticker}...")
                                # Close positions on exchange, preserve internal state for supervisor review
                                try:
                                    await emergency_stop(connector, config_path, state_file_path, paper_state_file_path, logger, ticker_override=base_ticker, paper_mode=paper_mode, close_only=True)
                                finally:
                                    await self_kill_pm2(base_ticker, paper_mode)
                                while True:
                                    await asyncio.sleep(86400)
                            else:
                                # Timeout not yet reached — still pending
                                if i % 5 == 0:
                                    logger.info(f"Trailing Stop Pending: {drawdown_pct:.2f}% (Wait {equity_trailing_stop_timeout_sec - elapsed:.1f}s more)")
                        else:
                            # Drawdown back below threshold — NO RECOVERY
                            # Trailing stop is ONE-SHOT: once triggered, never resets
                            if state.get("trailing_stop_violation_start", 0.0) > 0:
                                logger.info(f"Trailing Stop: drawdown {drawdown_pct:.2f}% but already triggered — NO recovery")

                # Check if trailing stop was previously triggered (one-shot)
                if state.get("trailing_stop_triggered", False):
                    logger.critical(f"🚨 Trailing Stop already triggered for {base_ticker}. Entering Zombie Mode.")
                    await self_kill_pm2(base_ticker, paper_mode)
                    while True:
                        await asyncio.sleep(86400)

                if not paper_mode and (margin_warning > 0 or margin_critical > 0):
                    try:
                        m_ratio = m_info.get("margin_ratio", 0.0)
                        if m_ratio > 0:
                            if m_ratio < portfolio_cfg.get("margin_ratio_critical", 2.0):
                                msg = f"Margin ratio {m_ratio:.2f} < {portfolio_cfg.get('margin_ratio_critical', 2.0)}. Emergency stop!"
                                logger.error(msg)
                                asyncio.create_task(notifier.send_alert("CRITICAL MARGIN", msg))
                                emit_signal("stop", base_ticker, paper_mode)
                                # --- CRITICAL: Update final metrics BEFORE emergency stop and exit ---
                                await _update_final_metrics_for_exit(state, state_file_path, Decimal(str(tpv_total)), Decimal(str(initial_tpv)), calc_res, cycles, logger)
                                # --------------------------------------------------------------------
                                try:
                                    await emergency_stop(connector, config_path, state_file_path, paper_state_file_path, logger, ticker_override=base_ticker, paper_mode=paper_mode, close_only=True)
                                finally:
                                    await self_kill_pm2(base_ticker, paper_mode)
                                while True:
                                    await asyncio.sleep(86400)
                            elif m_ratio < portfolio_cfg.get("margin_ratio_warning", 5.0):
                                msg = f"Low margin ratio: {m_ratio:.2f}"
                                logger.warning(msg)
                                asyncio.create_task(notifier.send_message(f"⚠️ <b>WARNING</b>: {msg} ({base_ticker})"))
                    except Exception as e:
                        logger.error(f"Failed to check margin: {e}")

                if i % 5 == 0:
                    res_str = f" | SAFE:{siphoning_reserve:.2f}" if siphoning_reserve > 0 else ""
                    def get_dev(actual, target): return (actual - target) * 100

                    l_p, s_p, v_p = calc_res['share_long_pct'], calc_res['share_short_pct'], calc_res['share_virt_pct']
                    c_p = calc_res['share_cash_pct']
                    
                    # Values for absolute breakdown
                    v_l, v_s, v_v, v_c = calc_res['val_long'], calc_res['val_short'], calc_res['val_virt'], calc_res['val_cash']
                    total_pnl = calc_res.get("total_pnl", tpv_total - initial_tpv)

                    l_target = Decimal(str(targets['BASE_LONG']['share']))
                    s_target = Decimal(str(targets['BASE_SHORT']['share']))
                    v_target = Decimal(str(targets['VIRTUAL']['share']))

                    h_msg = (
                        f"Heartbeat: TPV={tpv_total:.2f}{res_str} | PnL={total_pnl:+.2f} | {base_ticker}={price:.6g} | "
                        f"L:{l_p:.1f}% [{get_dev(Decimal(str(l_p))/100, l_target):+.1f}%] {{{v_l:+.2f}$}} | "
                        f"S:{s_p:.1f}% [{get_dev(Decimal(str(s_p))/100, s_target):+.1f}%] {{{v_s:+.2f}$}} | "
                        f"V:{v_p:.1f}% [{get_dev(Decimal(str(v_p))/100, v_target):+.1f}%] {{{v_v:+.2f}$}} | "
                        f"C:{c_p:.1f}% {{{v_c:.2f}$}}"
                    )
                    logger.info(h_msg)

                # Логика ребалансировки
                valid_actions = []
                fused_actions = []
                if actions:
                    # Внедряем Notional Value Guard для ВСЕХ ордеров
                    # Проверяем и в портфеле, и в глобальном конфиге
                    min_notional = portfolio_cfg.get("min_notional_usdt", current_config.get("min_notional_usdt", 6.0))
                    valid_actions = [a for a in actions if abs(a.get("diff_usdt", 0)) >= min_notional]
                    
                    fused_actions = valid_actions
                    if state.get("cash_invariant_violation"):
                        blocked_increases = [a for a in fused_actions if not a.get("is_reduction")]
                        fused_actions = [a for a in fused_actions if a.get("is_reduction")]
                        if blocked_increases:
                            logger.error(
                                f"Cash invariant violation active: blocked {len(blocked_increases)} increase action(s)."
                            )

                    if fused_actions:
                        # Log specific trigger reasons
                        for action in fused_actions:
                            act_type = action.get("type")
                            side = action.get("position_side", "BOTH")
                            symbol = action.get("symbol")
                            diff_usdt = action.get("diff_usdt", 0)

                            key_map = {"ORDER": "BASE_" + side, "VIRTUAL_ORDER": "VIRTUAL"}
                            key = key_map.get(act_type, symbol)

                            share_suffix = "virt" if key == "VIRTUAL" else key.split('_')[-1].lower()
                            current_share = Decimal(str(calc_res.get(f"share_{share_suffix}_pct", 0))) / 100
                            target_share = Decimal(str(targets.get(key, {}).get("share", 0)))
                            dev = (current_share - target_share) * 100

                            active_thresh = threshold_surplus if dev > 0 else threshold_deficit
                            trigger_key = key.replace("BASE_", "")
                            logger.info(f"Rebalance triggered: {trigger_key} deviation {dev:+.2f}% exceeds limit {active_thresh*100:.2f}%")

                        logger.info(f"Rebalance needed ({len(fused_actions)} fused actions). Shares: L:{calc_res['share_long_pct']:.1f}% S:{calc_res['share_short_pct']:.1f}% V:{calc_res['share_virt_pct']:.1f}%\nTPV: {tpv_active:.2f}")
                        
                        rebalance_msg = (
                            f"🔄 <b>Rebalance #{cycles + 1} Starting</b>: <code>{base_ticker}</code>\n"
                            f"Shares: L:{calc_res['share_long_pct']:.1f}% S:{calc_res['share_short_pct']:.1f}% V:{calc_res['share_virt_pct']:.1f}%\n"
                            f"TPV: <code>{tpv_total:.2f} USDT</code>"
                        )
                        asyncio.create_task(notifier.send_message(rebalance_msg))

                        # 1. Execute actions concurrently
                        budget_args = {"cash_budget": calc.val_cash} if paper_mode and not ignore_limits else {}
                        if not paper_mode:
                            from uuid import uuid4
                            for action in fused_actions:
                                if action.get("type") != "VIRTUAL_ORDER":
                                    action["client_order_id"] = "reb-" + uuid4().hex
                            state["startup_blocked"] = True
                            state["rebalance_inflight"] = {
                                "ticker": base_ticker, "created_at": time.time(),
                                "actions": json.loads(json.dumps(fused_actions, default=str))}
                            await save_json(state_file_path, state)
                        exec_results = await executor.execute_actions(
                            fused_actions, price, paper_mode, portfolio_cfg, step_sizes, paper_state, **budget_args
                        )
                        if not paper_mode:
                            state["rebalance_inflight"]["results"] = json.loads(json.dumps(exec_results, default=str))
                            await save_json(state_file_path, state)

                        any_success = False
                        # 2. Update core states (Virtual Quantity, Paper Positions/Balance)
                        for res in exec_results:
                            status = res.get("status")
                            pos_side = res.get("type", "UNKNOWN")

                            if status == "ERROR":
                                logger.error(f"Action failed: {res.get('message')}")
                                continue

                            if status in ["SUCCESS", "SUCCESS_LIMIT", "SUCCESS_FALLBACK"]:
                                # -------------------------------------------------------------------------
                                # [V3.8.3] LOGGING & ACCOUNTING FOR VIRTUAL SPOT POSITION (1x Leverage)
                                # -------------------------------------------------------------------------
                                if res.get("type") == "VIRTUAL_ORDER":
                                    any_success = True
                                    diff_usdt = Decimal(str(res.get("diff_usdt", 0.0)))

                                    # [V-CASH] VIRTUAL = чистый USDT-кэш: ордер только
                                    # перекладывает метку «свободный кэш <-> V» (value-neutral).
                                    # Баланс и цена актива в это не вмешиваются.
                                    new_virt_debt = Decimal(str(state.get("virt_debt", 0.0))) + diff_usdt
                                    if Decimal('0') < new_virt_debt < Decimal('1.0'):
                                        new_virt_debt = Decimal('0')  # dust-метка -> в ноль
                                    if new_virt_debt < Decimal('0'):
                                        new_virt_debt = Decimal('0')
                                    state["virt_debt"] = float(new_virt_debt.quantize(Decimal('1e-4')))
                                    paper_state_dirty = True
                                    state_dirty = True

                                    # Логируем USDT-семантику (цену актива не показываем)
                                    v_side = "DESIGNATE" if diff_usdt > 0 else "RELEASE"
                                    mode_tag = "PAPER" if paper_mode else "REAL"
                                    v_trade_log = (
                                        f"📝 {mode_tag}_VIRTUAL (cash label): {v_side} {abs(float(diff_usdt)):.2f} USDT "
                                        f"| V now: {float(new_virt_debt):.2f} USDT"
                                    )
                                    logger.info(v_trade_log)
                                    continue

                                key = res.get("symbol", "UNKNOWN")
                                side = res.get("side", "UNKNOWN")

                                qty = Decimal(str(res.get("qty", 0.0)))
                                if paper_mode and (not qty.is_finite() or qty < 0):
                                    raise ValueError("Invalid PAPER fill quantity")
                                if qty <= 0:
                                    logger.warning(f"⚠️ Skip state update for {key} because executed qty is {qty}")
                                    continue

                                if trail_state is not None or paper_mode:
                                    try:
                                        is_new_fill = share_trail.fill_receipt(paper_state, res)
                                    except ValueError:
                                        state["startup_blocked"] = True
                                        state["share_trail_unreconciled_results"] = exec_results
                                        await save_json(state_file_path, state)
                                        raise
                                    if not is_new_fill:
                                        logger.info("Duplicate confirmed fill ignored before position/cash update")
                                        continue
                                any_success = True

                                trade_pnl = Decimal(str(res.get("trade_pnl", 0.0)))
                                commission = Decimal(str(res.get("commission", 0.0)))
                                reduce_only = res.get("reduce_only", False)

                                pos_key = f"{base_ticker}_{pos_side}"
                                old_qty = Decimal(str(paper_state["positions"].get(pos_key, 0.0)))
                                entry_key = "long_entry_price" if pos_side == "LONG" else "short_entry_price"
                                old_entry = Decimal(str(paper_state.get(entry_key, price)))
                                if old_entry <= 0: old_entry = Decimal(str(price))

                                # Математически точный расчет изменения позиции
                                if (side == "BUY" and pos_side == "LONG") or (side == "SELL" and pos_side == "SHORT"):
                                    new_qty = old_qty + qty
                                else:
                                    new_qty = old_qty - qty

                                if paper_mode and (
                                        pos_side not in ("LONG", "SHORT") or side not in ("BUY", "SELL")
                                        or key != pos_key or not new_qty.is_finite() or new_qty < 0
                                        or not trade_pnl.is_finite() or not commission.is_finite() or commission < 0
                                        or bool(reduce_only) != ((pos_side == "LONG" and side == "SELL")
                                                                 or (pos_side == "SHORT" and side == "BUY"))):
                                    raise ValueError("Invalid PAPER fill quantity or identity")

                                # Расчет средней цены входа (только при увеличении позиции)
                                if not reduce_only and (old_qty + qty) > 0:
                                    dec_price = Decimal(str(res.get("price", price)))
                                    new_entry = (old_qty * old_entry + qty * dec_price) / (old_qty + qty)
                                    paper_state[entry_key] = float(new_entry.quantize(Decimal('1e-8')))

                                    # [LIQUIDATION GUARD] Calculate simulated liquidation price
                                    # For LONG: liq_price < entry (price must drop to liq)
                                    # For SHORT: liq_price > entry (price must rise to liq)
                                    # Diagnostic isolated approximation; external collateral is zero.
                                    _leverage = Decimal(str(targets.get(f"BASE_{pos_side}", {}).get("leverage", 7)))
                                    _mmr = Decimal('0.004')
                                    if pos_side == "LONG":
                                        _iso_liq = new_entry * (Decimal('1') - Decimal('1') / _leverage + _mmr)
                                        paper_state["long_liquidation_price"] = float(_iso_liq.quantize(Decimal('1e-8')))
                                    else:
                                        _iso_liq = new_entry * (Decimal('1') + Decimal('1') / _leverage - _mmr)
                                        paper_state["short_liquidation_price"] = float(_iso_liq.quantize(Decimal('1e-8')))

                                elif reduce_only and new_qty == 0:
                                    # Если позиция закрыта полностью, сбрасываем цену входа
                                    paper_state[entry_key] = 0.0
                                    paper_state["long_liquidation_price"] = 0.0
                                    paper_state["short_liquidation_price"] = 0.0

                                # Запись обратно во float-структуру JSON
                                paper_state["positions"][pos_key] = float(new_qty.quantize(Decimal('1e-8')))

                                # Two-sided share trailing: a confirmed fill (full or partial, qty > 0)
                                # consumes the signal of this leg and direction only. Unknown/failed
                                # statuses are NOT consumed (reconciliation required first).
                                if trail_state is not None:
                                    _direction = "REDUCE" if reduce_only else "ADD"
                                    share_trail.consume(trail_state, f"BASE_{pos_side}", _direction)
                                    state_dirty = True
                                    if new_qty == 0:
                                        share_trail.reset_leg(trail_state, f"BASE_{pos_side}")
                                        state_dirty = True

                                current_balance = Decimal(str(paper_state["balance"]))
                                paper_state["balance"] = float((current_balance + trade_pnl - commission).quantize(Decimal('1e-4')))
                                paper_state_dirty = True
                                if trail_state is not None or paper_mode:
                                    share_trail.fill_receipt(paper_state, res, record=True)
                                    paper_state["share_trail_checkpoint"] = {
                                        "spec": state["share_trail_spec"],
                                        "trail": share_trail.load_state(trail_state),
                                        "execution_id": res["execution_id"],
                                    }
                                    state["share_trail_last_fill"] = res["execution_id"]
                                    await save_json(paper_state_file_path, paper_state)
                                    await save_json(state_file_path, state)

                                mode_tag = "PAPER" if paper_mode else "REAL"
                                trade_log = f"📝 {mode_tag}: {side} {float(qty)} {pos_key} @ {float(res.get('price', price)):.8g}"
                                if trade_pnl != 0: trade_log += f" | PnL: {float(trade_pnl):+.4f}"
                                logger.info(trade_log)
                                # asyncio.create_task(notifier.send_message(f"<b>{trade_log}</b>"))
                            else:
                                logger.warning(f"❌ {side} {key} execution status: {status}. Message: {res.get('message')}")

                        if not paper_mode:
                            await save_json(paper_state_file_path, paper_state)
                            uncertain = (len(exec_results) != len(fused_actions) or any(
                                result.get("execution_pending") or result.get("status") not in
                                {"SUCCESS", "SUCCESS_LIMIT", "SUCCESS_FALLBACK", "SKIPPED", "NO_ORDER"}
                                for result in exec_results))
                            if uncertain:
                                state["startup_blocked"] = True
                            else:
                                state.pop("rebalance_inflight", None)
                                state.pop("startup_blocked", None)
                            await save_json(state_file_path, state)
                            if uncertain:
                                logger.critical("Uncertain market execution: facts saved; reconciliation required")
                                continue

                        if any_success and (ignore_limits or paper_mode):
                            post_virt = Decimal(str(state.get("virt_debt", 0.0)))
                            post_calc = PortfolioCalculator(
                                positions=paper_state["positions"],
                                spot_price=price,
                                real_equity=float(Decimal(str(paper_state["balance"])) - post_virt),
                                virt_qty=virt_qty,
                                virt_debt=float(post_virt),
                                base_ticker=base_ticker,
                                siphoning_reserve=siphoning_reserve,
                                targets=targets,
                                initial_capital=initial_tpv,
                                long_entry_price=paper_state.get("long_entry_price", 0.0),
                                short_entry_price=paper_state.get("short_entry_price", 0.0),
                                last_rebalance_price=price,
                                min_notional=active_min_notional
                            )
                            residual = post_calc.val_cash
                            missing = [
                                key for key in ("BASE_LONG", "BASE_SHORT")
                                if float(targets.get(key, {}).get("share", 0)) > 0
                                and abs(float(paper_state["positions"].get(
                                    f"{base_ticker}_{key.split('_')[-1]}", 0
                                ))) <= 1e-12
                            ]
                            if residual < 0:
                                state["initial_build_pending"] = None
                                state["cash_invariant_violation"] = {
                                    "cash": float(residual),
                                    "balance": paper_state["balance"],
                                    "virt_debt": float(post_virt),
                                    "positions": dict(paper_state["positions"]),
                                    "executions": [
                                        {
                                            "symbol": r.get("symbol"),
                                            "qty": r.get("qty"),
                                            "price": r.get("price"),
                                            "commission": r.get("commission"),
                                        }
                                        for r in exec_results if r.get("qty")
                                    ],
                                    "at": time.time(),
                                }
                                state_dirty = True
                                await save_json(state_file_path, state)
                                await save_json(paper_state_file_path, paper_state)
                                logger.critical(
                                    f"Cash invariant violated after execution: CASH={float(residual):.8f}. "
                                    "Executions saved; further increases blocked."
                                )
                            elif ignore_limits and missing:
                                state["initial_build_pending"] = {
                                    "missing": missing,
                                    "attempts": int((state.get("initial_build_pending") or {}).get("attempts", 0)) + 1,
                                }
                                state_dirty = True
                                await save_json(state_file_path, state)
                                await save_json(paper_state_file_path, paper_state)
                                logger.warning(
                                    f"Initial build incomplete: missing {','.join(missing)}; "
                                    f"residual CASH={float(residual):.8f} kept for continuation."
                                )
                            elif ignore_limits:
                                state["initial_build_pending"] = None
                                state.setdefault("initial_build_completed_at", time.time())
                                state["virt_debt"] = float(post_virt + residual)
                                state_dirty = True
                                logger.info(f"Initial residual {float(residual):.8f} USDT moved to VIRTUAL")

                        if any_success:
                            if not ignore_limits:
                                state["last_rebalance_completed_at"] = time.time()
                            state["last_rebalance_price"] = price
                            cycles += 1
                            state["rebalance_cycles"] = cycles
                            state_dirty = True
                            logger.info(f"🎯 Baseline Updated: Last rebalance price set to {price:.6g}")

                # 3. GLOBAL SAFE SIPHONING (Runs every cycle)
                # siphoning_reserve is local variable, but we should update state as well
                if actions and len(valid_actions) > 0:
                    # ALWAYS use shadow balance (paper_state) for siphoning calculation to support shared accounts
                    l_qty_p = abs(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0))
                    s_qty_p = abs(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0))
                    
                    if paper_mode:
                        safe_l_entry = paper_state.get("long_entry_price", price)
                        safe_s_entry = paper_state.get("short_entry_price", price)
                    else:
                        # In real mode, use exchange entry prices for better accuracy
                        raw_positions_new = await connector.get_positions()
                        safe_l_entry = raw_positions_new.get(f"{base_ticker}_LONG", {}).get("entry_price", 0.0)
                        safe_s_entry = raw_positions_new.get(f"{base_ticker}_SHORT", {}).get("entry_price", 0.0)
                        
                    safe_real_equity = paper_state["balance"]
                    safe_positions = paper_state["positions"]
                else:
                    safe_real_equity = paper_state["balance"]
                    safe_positions = positions
                    safe_l_entry = l_entry
                    safe_s_entry = s_entry

                if paper_mode:
                    safe_l_entry = paper_state.get("long_entry_price", price)
                    safe_s_entry = paper_state.get("short_entry_price", price)

                # Calculate surplus using CURRENT virtual parameters
                # Use reconstructed real_equity for safe_calc
                safe_virt_debt = Decimal(str(state.get("virt_debt", 0.0)))
                safe_real_equity_adj = float(Decimal(str(safe_real_equity)) - safe_virt_debt)
                if safe_real_equity_adj < 0: safe_real_equity_adj = 0.0

                safe_calc = PortfolioCalculator(
                    positions=safe_positions,
                    spot_price=price,
                    real_equity=safe_real_equity_adj,
                    virt_qty=virt_qty,
                    virt_debt=float(safe_virt_debt),
                    base_ticker=base_ticker,
                    siphoning_reserve=siphoning_reserve,
                    targets=targets,
                    initial_capital=initial_tpv,
                    long_entry_price=safe_l_entry,
                    short_entry_price=safe_s_entry,
                    last_rebalance_price=state.get("last_rebalance_price", 0.0),
                    min_notional=active_min_notional
                )
                safe_calc_res = safe_calc.calculate_rebalance(targets, 0.0, 0.0, safe_calc.tpv, True)

                total_tpv_final = safe_calc_res["total_tpv"]

                # ВСЕГДА обновляем last_tpv и last_profit для актуального PnL в summary
                state["last_tpv"] = total_tpv_final
                state["last_profit"] = total_tpv_final - initial_tpv
                state["total_pnl_pct"] = safe_calc_res.get("total_pnl_pct", 0.0)
                state["last_update"] = time.time()
                state_dirty = True

                # SURPLUS = Current Total Capital (including reserve) - Initial Targeted Capital
                total_surplus: float = total_tpv_final - initial_tpv
                siphoning_threshold_abs: float = initial_tpv * (siphoning_threshold_pct / 100)

                # Siphon only if total_surplus > existing reserve (meaning there is NEW profit)
                if total_surplus > siphoning_reserve + max(0.1, siphoning_threshold_abs):
                    new_profit = total_surplus - siphoning_reserve
                    siphon_amount: float = new_profit * (1 - reinvestment_ratio)
                    
                    if siphon_amount > 0.1:
                        siphoning_reserve += siphon_amount
                        # ALWAYS subtract from shadow balance to track isolated per-bot equity
                        paper_state["balance"] -= siphon_amount
                        paper_state_dirty = True

                        state["siphoning_reserve"] = siphoning_reserve
                        state_dirty = True
                        logger.info(f"💰 SAFE ACTIVATED: Siphoned {siphon_amount:.4f} USDT. New Reserve: {siphoning_reserve:.2f}")
                        asyncio.create_task(notifier.send_message(f"💰 <b>SAFE</b>: +{siphon_amount:.4f} USDT (Surplus)"))

                # Update reporting value in summary to account for new reserve
                final_reported_tpv = total_tpv_final

                if any_success:
                    summary_msg = (
                        f"<b>✅ Rebalance #{cycles} Complete</b>: <code>{base_ticker}</code>\n"
                        f"New Shares: L:{safe_calc_res['share_long_pct']:.1f}% S:{safe_calc_res['share_short_pct']:.1f}% V:{safe_calc_res['share_virt_pct']:.1f}%\n"
                        f"TPV: <code>{total_tpv_final:.2f} USDT</code>"
                    )
                    logger.info(f"Rebalance #{cycles} complete. TPV: {total_tpv_final:.2f}")
                    
                    # [V3.9.0] Silence Telegram for Rebalance #1 (Baseline formation) to avoid startup spam
                    if cycles > 1:
                        asyncio.create_task(notifier.send_message(summary_msg))
                    else:
                        logger.info(f"ℹ️ Rebalance #1 (Baseline) notification suppressed in Telegram.")

                # Обновляем cycles в стейте (если были действия)
                state.update({
                    "rebalance_cycles": cycles
                })
                state_dirty = True

                # [LIQUIDATION GUARD] Verify real positions every cycle, not only after a
                # successful execution: a leg can be liquidated while the bot is idle.
                # Expected quantities come from the shadow ledger — state["positions"] is a
                # legacy dict the loop never writes.
                if not paper_mode:
                    try:
                        real_pos_dict = await connector.get_position_risk()
                        # Convert dict {"SYMBOL_LONG": {...}, "SYMBOL_SHORT": {...}} to list
                        real_pos = []
                        for key, val in real_pos_dict.items():
                            entry = dict(val)
                            entry["symbol"] = key.split("_")[0] if "_" in key else key
                            entry["positionSide"] = key.split("_")[1] if "_" in key else "BOTH"
                            real_pos.append(entry)
                        real_pos = [p for p in real_pos if base_ticker in p.get("symbol", "")]
                        has_long = False
                        has_short = False
                        for p in real_pos:
                            amt = float(p.get("qty", p.get("positionAmt", 0)))
                            if abs(amt) < 1e-10:
                                continue
                            ps = p.get("positionSide", "")
                            if "LONG" in ps or (ps == "BOTH" and amt > 0):
                                has_long = True
                            elif "SHORT" in ps or (ps == "BOTH" and amt < 0):
                                has_short = True

                        # Check if one side is missing (liquidated)
                        expected_long = abs(float(paper_state["positions"].get(f"{base_ticker}_LONG", 0.0)))
                        expected_short = abs(float(paper_state["positions"].get(f"{base_ticker}_SHORT", 0.0)))

                        if expected_long > 1e-10 and not has_long:
                            logger.error(f"Expected position {base_ticker}_LONG is missing on exchange; reconciliation required")
                            await _handle_liquidation_recovery(connector, base_ticker, state, state_file_path,
                                                                paper_state, paper_state_file_path,
                                                                config_path, logger, notifier)
                            while True:
                                await asyncio.sleep(86400)
                        if expected_short > 1e-10 and not has_short:
                            logger.error(f"Expected position {base_ticker}_SHORT is missing on exchange; reconciliation required")
                            await _handle_liquidation_recovery(connector, base_ticker, state, state_file_path,
                                                                paper_state, paper_state_file_path,
                                                                config_path, logger, notifier)
                            while True:
                                await asyncio.sleep(86400)
                    except Exception as e:
                        logger.warning(f"Liquidation guard check failed: {e}")

                if state_dirty:
                    await save_json(state_file_path, state)
                if paper_state_dirty:
                    await save_json(paper_state_file_path, paper_state)

                if (i + status_offset) % 100 == 0:
                    total_pnl_final = safe_calc_res.get("total_pnl", total_tpv_final - initial_tpv)
                    logger.info(f"Heartbeat: TPV={total_tpv_final:.2f} | PnL={total_pnl_final:+.2f} | {base_ticker}={price:.6g} | Cycles={cycles}")

            except Exception as e:
                if paper_mode and isinstance(e, ValueError) and str(e).startswith("Invalid PAPER"):
                    state["startup_blocked"] = True
                    state["startup_block_reason"] = str(e)
                    await save_json(state_file_path, state)
                logger.error(f"Error in cycle: {e}")
                await asyncio.sleep(10)
            await asyncio.sleep(check_interval)
            i += 1
    finally:
        await notifier.close()

async def emergency_stop(connector: BinanceConnector, config_path: str, state_file_path: str, paper_state_file_path: str, logger: logging.Logger, ticker_override: str = None, paper_mode: bool = False, close_only: bool = False):
    config = safe_load_json_sync(config_path, {})

    base_ticker = ticker_override if ticker_override else config.get("base_ticker", "BTCUSDT")
    portfolio_cfg = config.get("portfolios", [{}])[0]
    initial_capital = portfolio_cfg.get("initial_capital", 60.0)

    if paper_mode:
        from core.paper_close import close_wallet
        from core.funding import save_funding_boundary, load_funding_state
        state = await load_json(state_file_path, {})
        wallet = await load_json(paper_state_file_path, {})
        if not state or not wallet or not state.get("started_at") or float(state.get("initial_tpv", 0)) <= 0:
            raise ValueError("PAPER stop requires an identified strategy/wallet pair")
        if any(float(q) for q in wallet.get("positions", {}).values()):
            prices = await connector.get_mark_prices([base_ticker])
            close_price = prices.get(base_ticker)
        else:
            close_price = wallet.get("last_price") or 1
        event = f"stop:{state['started_at']}:{state.get('rebalance_cycles', 0)}"
        wallet = close_wallet(wallet, base_ticker, close_price, event, PAPER_COMMISSION_RATE)
        equity = float(wallet["balance"]) + float(state.get("siphoning_reserve", 0))
        state.update(last_tpv=equity, last_profit=equity - float(state["initial_tpv"]),
                     paper_close_complete=True, trailing_stop_triggered=True, last_update=time.time())
        state["total_pnl_pct"] = state["last_profit"] / float(state["initial_tpv"]) * 100
        journal, halt = load_funding_state(state)
        save_funding_boundary(state_file_path, paper_state_file_path, state, wallet, journal, halt)
        archive = Path(state_file_path).resolve().parent.parent / "history"
        archive.mkdir(exist_ok=True)
        await save_json(str(archive / f"paper_close_{base_ticker}_{state['started_at']}.json"),
                        {"state": state, "paper_state": wallet, "timestamp": time.time(), "close_event": event})
        logger.info(f"PAPER CLOSE committed: {base_ticker} equity={equity:.8f} net={state['last_profit']:.8f}; opening and ATH retained")
        return

    logger.info(f"🛑 EMERGENCY STOP for {base_ticker} (Paper: {paper_mode}, CloseOnly: {close_only})")

    # --- PERSISTENCE PROTOCOL: Archive state before reset ---
    # --- PERSISTENCE PROTOCOL: Archive state before reset ---
    try:
        history_dir = Path("history")
        history_dir.mkdir(exist_ok=True)
        state = await load_json(state_file_path, {})
        paper_state = await load_json(paper_state_file_path, {})

        # Ensure final profit is calculated from the *current* state before archiving
        current_total_tpv_final = float(state.get("last_tpv", 0.0))
        current_initial_tpv = float(state.get("initial_tpv", 0.0))
        final_calculated_profit = current_total_tpv_final - current_initial_tpv

        archive_data = {
            "ticker": base_ticker,
            "timestamp": time.time(),
            "state": state,
            "paper_state": paper_state,
            "final_profit": final_calculated_profit,
            "final_total_tpv": current_total_tpv_final,
            "final_pnl_pct": state.get("total_pnl_pct", 0.0)
        }
        archive_path = history_dir / f"archive_{base_ticker}_{int(time.time())}.json"
        await save_json(str(archive_path), archive_data)
        logger.info(f"💾 State archived to {archive_path}")
    except Exception as e:
        logger.error(f"Failed to archive state: {e}")

    exchange_info = await connector.get_exchange_info()
    step_sizes = {s["symbol"]: float(f["stepSize"]) for s in exchange_info["symbols"] for f in s["filters"] if f["filterType"] == "LOT_SIZE"}

    # Always try to load paper_state to reset it (unless close_only)
    paper_state = await load_json(paper_state_file_path, {})

    if paper_mode:
        if paper_state and "positions" in paper_state:
            for pos_key, qty in paper_state["positions"].items():
                if qty != 0:
                    logger.info(f"Closing PAPER position {pos_key}: {qty}")

        if not close_only:
            # Reset paper state to fresh start
            paper_state.update({
                "balance": initial_capital,
                "positions": {f"{base_ticker}_LONG": 0.0, f"{base_ticker}_SHORT": 0.0},
                "long_entry_price": 0.0,
                "short_entry_price": 0.0,
                "last_price": 0.0
            })
            await save_json(paper_state_file_path, paper_state)
    else:
        # REAL mode: close on exchange
        raw_positions = await connector.get_positions()
        for pos_key, data in raw_positions.items():
            if base_ticker in pos_key:
                qty = data["qty"]
                if qty != 0:
                    side = "SELL" if qty > 0 else "BUY"
                    step_size = step_sizes.get(base_ticker, 0.0)
                    logger.info(f"Closing REAL position {pos_key}: {qty}")
                    await PortfolioExecutor(connector).execute_market_order(
                        symbol=base_ticker,
                        qty=Decimal(str(abs(qty))),
                        side=side,
                        step_size=Decimal(str(step_size)),
                        reduce_only=True,
                        position_side=pos_key.split('_')[1] if '_' in pos_key else "BOTH",
                        min_notional=Decimal('0')
                    )

        if not close_only:
            if paper_state:
                paper_state.update({
                    "balance": initial_capital,
                    "positions": {f"{base_ticker}_LONG": 0.0, f"{base_ticker}_SHORT": 0.0},
                    "long_entry_price": 0.0,
                    "short_entry_price": 0.0
                })
                await save_json(paper_state_file_path, paper_state)

    if not close_only:
        # Сброс основного состояния
        state = await load_json(state_file_path, {})
        state.update({
            "virt_qty": 0.0,
            "virt_debt": 0.0,
            "initial_tpv": 0.0,
            "reference_tpv": 0.0,
            "tpv_ath": 0.0,
            # "trailing_stop_triggered": False, # [RESTRICTION] Manual reset only by supervisor
            "trailing_stop_violation_start": 0.0
        })
        await save_json(state_file_path, state)
        logger.info(f"✅ Emergency stop completed for {base_ticker}. All positions closed and state reset (TS flag preserved).")
    else:
        # При close_only (например, при ротации супервайзером) выполняем санитарию ATH и базы,
        # чтобы при следующем запуске бот не стартанул с глубокой просадки относительно старого ATH.
        try:
            state = await load_json(state_file_path, {})
            if state:
                current_tpv = float(state.get("last_tpv", 0.0))
                if current_tpv > 0:
                    logger.info(f"🔄 Санитизация состояния при плановом стопе ({base_ticker}). Ребазирование на {current_tpv:.4f}")
                    # Floor: tpv_ath cannot be below global initial capital
                    _sanitized_ath = max(current_tpv, initial_capital)
                    state.update({
                        "initial_tpv": current_tpv,
                        "reference_tpv": current_tpv,
                        "tpv_ath": _sanitized_ath,
                        "trailing_stop_violation_start": 0.0
                    })
                    await save_json(state_file_path, state)
        except Exception as e:
            logger.error(f"Failed to sanitize state during emergency_stop: {e}")
        logger.info(f"✅ Positions closed for {base_ticker}. State preserved and sanitized.")

if __name__ == "__main__":
    import argparse
    import sys
    import io

    # Force UTF-8 for Windows streams
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--stop", action="store_true", help="Close all positions and stop")
    parser.add_argument("--close-only", action="store_true", help="Only close positions on exchange, preserve bot state")
    parser.add_argument("--wipe", action="store_true", help="Wipe bot state (destructive stop)")
    parser.add_argument("--paper", action="store_true", help="Force paper mode for this instance")
    parser.add_argument("--real", action="store_true", help="Force real mode (live) for this instance")
    parser.add_argument("--expect-live-snapshot", action="store_true",
                        help="Require a usable live snapshot pair; block startup when it is lost, corrupt or mismatched")
    args = parser.parse_args()
    
    config_base = os.path.splitext(os.path.basename(args.config))[0]

    def get_initial_cfg():
        return safe_load_json_sync(args.config, {})

    cfg = get_initial_cfg()
    base_ticker = args.ticker if args.ticker else cfg.get("base_ticker", "BTCUSDT")
    
    # Paper mode logic:
    # 1. If --real flag is present, force REAL mode.
    # 2. If --paper flag is present, force PAPER mode.
    # 3. Otherwise, fall back to global config paper_mode.
    if args.real:
        is_paper_instance = False
    elif args.paper:
        is_paper_instance = True
    else:
        is_paper_instance = cfg.get("paper_mode", False)

    # Configure individual logger
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_prefix = "paper" if is_paper_instance else "real"
    log_filename = f"{log_prefix}_{base_ticker}.log"
    log_path = os.path.join(log_dir, log_filename)

    logger = logging.getLogger(f"Bot_{base_ticker}_{log_prefix}")
    logger.setLevel(logging.INFO)
    # Clear handlers if any (prevent double logging on reload if it ever happens)
    if logger.handlers:
        logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    
    # File Handler (UTF-8)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    # Stream Handler (Console)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    
    # Prevent propagation to root logger
    logger.propagate = False

    # Launch contract: refuse a PAPER invocation before any state read, API call or
    # PM2 action. --stop/--wipe keep working: they never launch trading.
    if args.expect_live_snapshot and is_paper_instance and not args.stop:
        logger.critical(live_refusal_in_paper(base_ticker))
        while True:
            time.sleep(86400)
    
    if is_paper_instance:
        # PAPER mode uses paper_state_*.json for primary state and paper_shadow_*.json for simulated wallet
        instance_state_file = os.path.abspath(os.path.join(os.path.dirname(__file__), f"paper_state_{base_ticker}.json"))
        instance_paper_state_file = os.path.abspath(os.path.join(os.path.dirname(__file__), f"paper_shadow_{base_ticker}.json"))
    else:
        # REAL mode uses real_state_*.json for primary state and shadow_state_*.json for shadow balance
        instance_state_file = os.path.abspath(os.path.join(os.path.dirname(__file__), f"real_state_{base_ticker}.json"))
        instance_paper_state_file = os.path.abspath(os.path.join(os.path.dirname(__file__), f"shadow_state_{base_ticker}.json"))
    
    api_key = os.environ.get("BINANCE_API_KEY", cfg.get("api_key", ""))
    secret_key = os.environ.get("BINANCE_SECRET_KEY", cfg.get("secret_key", ""))

    # [Hardware Block] Zombie Mode to prevent PM2 autorestart loop and API spam
    pre_state = safe_load_json_sync(instance_state_file, {})
    # A non-object snapshot must not crash the CLI before the loop can fail closed.
    if isinstance(pre_state, dict) and pre_state.get("trailing_stop_triggered", False) and not args.stop and not args.wipe:
        logger.critical(f"🚨 [ZOMBIE MODE] Trailing Stop triggered for {base_ticker}. Entering infinite sleep to block PM2 autorestart.")
        import time
        while True:
            time.sleep(86400)

    if os.environ.get("MOCK_MODE") == "1":
        from connector import BinanceConnectorMock
        connector = BinanceConnectorMock()
    else:
        @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=10),
               before_sleep=lambda retry_state: logger.warning(f"Connection failed, retrying... (attempt {retry_state.attempt_number})"))
        async def init_connector():
            conn = BinanceConnector(api_key=api_key, secret_key=secret_key, testnet=cfg.get("testnet", True))
            await conn.verify_connection()
            return conn

        try:
            connector = asyncio.run(init_connector())
            logger.info("BinanceConnector initialized and verified successfully.")
        except Exception as e:
            logger.critical(f"Failed to initialize BinanceConnector after 5 attempts: {e}")
            exit(1)

    if args.stop:
        # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: По умолчанию НЕ удаляем стейт при стопе. 
        # Только если явно передан --wipe
        should_wipe = args.wipe
        asyncio.run(emergency_stop(connector, args.config, instance_state_file, instance_paper_state_file, logger, ticker_override=base_ticker, paper_mode=is_paper_instance, close_only=(not should_wipe)))
    else:
        logger.info(f"💾 State files: REAL={instance_state_file}, PAPER={instance_paper_state_file} | Mode: {'PAPER' if is_paper_instance else 'REAL'}")
        # Передаем признак paper_mode в rebalance_loop через конфиг-обертку или напрямую, 
        # но rebalance_loop читает конфиг из файла. Лучше пропатчить rebalance_loop чтобы он принимал paper_mode_override.
        asyncio.run(rebalance_loop(connector, args.config, instance_state_file, instance_paper_state_file, logger, ticker_override=base_ticker, paper_mode_override=is_paper_instance, expect_live_snapshot=args.expect_live_snapshot))
