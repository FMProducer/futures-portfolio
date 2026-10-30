import logging
from typing import Any, Dict, List, Optional, Tuple
from decimal import Decimal, ROUND_HALF_EVEN, getcontext

from core.share_trail import FUTURES_LEGS, step as trail_step

logger = logging.getLogger(__name__)

# Set decimal precision and rounding mode globally for financial calculations
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN


def initial_notional_after_fee(target_notional: float, leverage: float, fee_rate: float) -> float:
    return float(Decimal(str(target_notional)) / (Decimal('1') + Decimal(str(leverage)) * Decimal(str(fee_rate))))


def book_vwap(levels: List[Any], qty: Any) -> Optional[Decimal]:
    """Volume-weighted average over available depth; None when qty is zero or depth is insufficient."""
    need = Decimal(str(qty))
    if need <= 0:
        return None
    total, taken = Decimal('0'), Decimal('0')
    for px, q in levels:
        take = min(Decimal(str(q)), need - taken)
        if take <= 0:
            break
        total += Decimal(str(px)) * take
        taken += take
        if taken >= need:
            return total / need
    return None


class PortfolioCalculator:
    def __init__(self, positions: Dict[str, float], spot_price: float, real_equity: float, 
                 virt_qty: float, 
                 long_entry_price: float = 0.0, short_entry_price: float = 0.0,
                 virt_entry_price: float = 0.0, virt_debt: float = 0.0,
                 base_ticker: str = "BTCUSDT", siphoning_reserve: float = 0.0,
                 targets: Dict[str, Dict] = None, initial_capital: float = 10000.0,
                 last_rebalance_price: float = 0.0,
                  min_notional: float = 6.0, commission_pct: float = 0.0,
                  slippage_pct: float = 0.0,
                  fuse_fill: Optional[Dict[str, Any]] = None,
                  profit_budget: float = 0.0, initial_build: bool = False) -> None:
        
        # Convert all inputs to Decimal for precision
        self.positions = {k: Decimal(str(v)) for k, v in positions.items()}
        self.price = Decimal(str(spot_price))
        self.base_ticker = base_ticker
        self.siphoning_reserve = Decimal(str(siphoning_reserve))
        self.initial_capital = Decimal(str(initial_capital))
        self.real_equity = Decimal(str(real_equity)) # Wallet Balance (Cash + Margin, NO PnL)
        self.profit_budget = Decimal(str(profit_budget))
        if not self.profit_budget.is_finite():
            raise ValueError("Profit budget must be finite")
        self.initial_build = initial_build
        self.virt_qty = Decimal(str(virt_qty))
        self.virt_debt = Decimal(str(virt_debt))
        self.targets = targets or {}
        self.last_rebalance_price = Decimal(str(last_rebalance_price)) if last_rebalance_price > 0 else self.price
        self.min_notional = Decimal(str(min_notional))
        self.commission_pct = Decimal(str(commission_pct))
        self.slippage_pct = Decimal(str(slippage_pct))
        for rate in (self.commission_pct, self.slippage_pct):
            if not rate.is_finite() or not Decimal('0') <= rate < Decimal('1'):
                raise ValueError("Execution cost rates must be finite and in [0, 1)")

        # Live-mode FUSE gate: book levels + account taker rate (None => unconfirmed)
        self.fuse_fill = None
        if fuse_fill is not None:
            validated: Dict[str, Any] = {"bids": [], "asks": [], "taker": None}
            for key in ("bids", "asks"):
                converted: List[Tuple[Decimal, Decimal]] = []
                for px, q in (fuse_fill.get(key) or []):
                    px, q = Decimal(str(px)), Decimal(str(q))
                    if not (px.is_finite() and px > 0 and q.is_finite() and q >= 0):
                        raise ValueError("invalid fuse_fill level")
                    converted.append((px, q))
                validated[key] = converted
            taker = fuse_fill.get("taker")
            if taker is not None:
                taker = Decimal(str(taker))
                if not taker.is_finite() or not Decimal('0') <= taker < 1:
                    raise ValueError("invalid fuse_fill taker rate")
            validated["taker"] = taker
            self.fuse_fill = validated

        # 1. PnL contributions for Heartbeat (RELATIVE to last rebalance)
        # This ensures the sign of $ PnL always matches the sign of % deviation.
        l_qty = abs(self.positions.get(f"{self.base_ticker}_LONG", Decimal('0')))
        s_qty = abs(self.positions.get(f"{self.base_ticker}_SHORT", Decimal('0')))

        self.pnl_l = (l_qty * (self.price - self.last_rebalance_price)) if l_qty > 0 else Decimal('0')
        self.pnl_s = (s_qty * (self.last_rebalance_price - self.price)) if s_qty > 0 else Decimal('0')

        # Real MTM PnL (for TPV) relative to entry prices for futures
        mtm_pnl_l = (l_qty * (self.price - Decimal(str(long_entry_price)))) if l_qty > 0 else Decimal('0')
        mtm_pnl_s = (s_qty * (Decimal(str(short_entry_price)) - self.price)) if s_qty > 0 else Decimal('0')
        
        # 1. VIRTUAL = чистый USDT-кэш (свободная маржа на аккаунте), а не спотовая
        #    позиция в активе. Стоимость ноги = вложенные USDT (virt_debt); она НЕ
        #    пересчитывается по цене актива. PnL чистой маржи = 0. Ребалансировка
        #    доли V к целевому значению продолжается по тому же порогу
        #    (share_virt_raw = val_virt/tpv ниже не меняется логически).
        self.virt_value = self.virt_debt
        self.pnl_v = Decimal('0')

        # Store entry prices for FUSE profitability gate
        self.long_entry_price = Decimal(str(long_entry_price))
        self.short_entry_price = Decimal(str(short_entry_price))

        # [SSOT] TPV = Доступный баланс (кэш) + PnL фьючерсов + Рыночная стоимость виртуальной ноги
        # real_equity должен передаваться как "Чистый кэш" (Wallet Balance - virt_debt)
        self.tpv = self.real_equity + mtm_pnl_l + mtm_pnl_s + self.virt_value

        if self.tpv <= 0:
            self.tpv = Decimal('1e-9')

        self.total_tpv = self.tpv + self.siphoning_reserve

        # Total PnL = Sum of all contributions
        self.total_pnl = self.tpv - self.initial_capital

        # Total PnL % = (TPV / initial_capital - 1) * 100
        self.total_pnl_pct = ((self.tpv / self.initial_capital) - 1) * 100 if self.initial_capital > 0 else Decimal('0')

        # 4. Calculate NAV for each leg
        l_lev = Decimal(str(self.targets.get("BASE_LONG", {}).get("leverage", 5)))
        s_lev = Decimal(str(self.targets.get("BASE_SHORT", {}).get("leverage", 5)))

        # Value = Initial Margin + MTM PnL (Actual Liquidation Equity of the position)
        self.val_long = ((l_qty * Decimal(str(long_entry_price)) / l_lev) + mtm_pnl_l) if l_qty > 0 else Decimal('0')
        self.val_short = ((s_qty * Decimal(str(short_entry_price)) / s_lev) + mtm_pnl_s) if s_qty > 0 else Decimal('0')
        self.val_virt = self.virt_value
        
        # 5. Cash is the remaining liquidity (Free Wallet Balance)
        self.val_cash = self.tpv - (self.val_long + self.val_short + self.val_virt)

        # 6. Shares calculation (Capital weights against true TPV)
        self.share_long_raw = (self.val_long / self.tpv)
        self.share_short_raw = (self.val_short / self.tpv)
        self.share_virt_raw = (self.val_virt / self.tpv)
        self.share_cash_raw = (self.val_cash / self.tpv)

        # Convert to percentages for display and rebalancing logic
        self.share_long_pct = (self.share_long_raw * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
        self.share_short_pct = (self.share_short_raw * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
        self.share_virt_pct = (self.share_virt_raw * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)
        self.share_cash_pct = (self.share_cash_raw * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)

        # Final correction to ensure exactly 100.00%
        total_pct = self.share_long_pct + self.share_short_pct + self.share_virt_pct + self.share_cash_pct
        if total_pct != Decimal('100.00'):
            diff = Decimal('100.00') - total_pct
            self.share_cash_pct += diff # Adjust cash by the sub-penny difference

    def calculate_rebalance(self, targets: Dict[str, Dict], threshold_surplus: float, threshold_deficit: float, current_equity: Decimal, ignore_limits: bool = False, allow_surplus_sell: bool = True, force_block: bool = False, fuse_enabled: bool = True, share_trail: Dict = None, trail_distance: float = 0.0, share_basis_scale: float = 1.0) -> Dict:
        """
        Calculate rebalance actions and return a summary of the current state.
        allow_surplus_sell: if False, surplus (profit-taking) actions are blocked.
            Used when total PnL is negative to prevent "selling winners" while "losers" accumulate.
        force_block: if True, ALL actions are blocked (Net Move Guard active).
            Used during fast unidirectional price movements (pump/dump) to prevent
            closing positions at fake profit/loss.
        share_trail: optional persistent two-sided share-trailing state
            (core.share_trail.fresh_state()); mutated in place.
        trail_distance: share-fraction pullback/rebound from the activated extremum; 0 disables trailing
            (baseline threshold behaviour, trailing state untouched).
        """
        dev_res = self.calculate_deviations(targets, threshold_surplus, threshold_deficit, current_equity, ignore_limits, allow_surplus_sell, force_block, fuse_enabled, share_trail, trail_distance, share_basis_scale)
        # calculate_deviations возвращает dict с actions, available_funds, tpv, share_*
        actions = dev_res["actions"]

        return {
            "actions": actions,
            "available_funds": dev_res["available_funds"],
            "share_long_pct": dev_res["share_long_pct"],
            "share_short_pct": dev_res["share_short_pct"],
            "share_virt_pct": dev_res["share_virt_pct"],
            "share_cash_pct": dev_res["share_cash_pct"],
            "val_long": float(self.val_long),
            "val_short": float(self.val_short),
            "val_virt": float(self.val_virt),
            "val_cash": float(self.val_cash),
            "tpv": dev_res["tpv"],
            "total_tpv": dev_res["total_tpv"],
            "siphoning_reserve": float(self.siphoning_reserve),
            "virt_current_value": float(self.val_virt),
            "pnl_l": float(self.pnl_l),
            "pnl_s": float(self.pnl_s),
            "pnl_v": float(self.pnl_v),
            "total_pnl": float(self.total_pnl),
            "total_pnl_pct": float(self.total_pnl_pct)
        }

    def calculate_deviations(self, targets: Dict[str, Dict], threshold_surplus: float, threshold_deficit: float, current_equity: Decimal, ignore_limits: bool = False, allow_surplus_sell: bool = True, force_block: bool = False, fuse_enabled: bool = True, share_trail: Dict = None, trail_distance: float = 0.0, share_basis_scale: float = 1.0) -> List[Dict]:
        """
        Rebalance based on CAPITAL (Equity) deviations. 
        This allows the portfolio to harvest volatility profit.
        allow_surplus_sell: if False, surplus actions are excluded (PnL protection mode).
        force_block: if True, ALL actions are blocked (Net Move Guard).
        share_trail / trail_distance: two-sided share trailing (see core.share_trail).
            When trail_distance > 0, futures-leg readiness comes from observed
            peak pullbacks / trough rebounds; VIRTUAL keeps fixed thresholds.
        """
        if not isinstance(fuse_enabled, bool):
            raise ValueError("fuse_enabled must be a boolean")
        scale = Decimal(str(share_basis_scale))
        if not scale.is_finite() or not 0 < scale <= 1:
            raise ValueError("share_basis_scale must be finite and in (0, 1]")
        # CASH collateral dilutes share units, but must not widen trading bands or price-based FUSE.
        share_surplus = Decimal(str(threshold_surplus)) * scale
        share_deficit = Decimal(str(threshold_deficit)) * scale
        share_distance = Decimal(str(trail_distance)) * scale

        # Trailing observation persists during WAIT/blocks: step before any
        # early return, readiness flags are only used by the action loop.
        # A flat leg falls back to baseline thresholds (None flag): trailing
        # governs existing positions only and must never block the build.
        trail_flags: Optional[Dict[str, Optional[Tuple[bool, bool]]]] = None
        if share_trail is not None and not ignore_limits and Decimal(str(trail_distance)) > 0:
            trail_flags = {}
            flat_long = abs(self.positions.get(f"{self.base_ticker}_LONG", Decimal('0'))) <= 0
            flat_short = abs(self.positions.get(f"{self.base_ticker}_SHORT", Decimal('0'))) <= 0
            for key, raw_share, flat in (("BASE_LONG", self.share_long_raw, flat_long),
                                         ("BASE_SHORT", self.share_short_raw, flat_short)):
                target_share = Decimal(str(targets[key]["share"]))
                red_ready, add_ready, _reset = trail_step(
                    share_trail, key, raw_share, target_share,
                    target_share + share_surplus, target_share - share_deficit,
                    share_distance, flat=flat,
                )
                trail_flags[key] = None if flat else (red_ready, add_ready)

        if force_block:
            # Net Move Guard active — block ALL actions during fast price movement
            return {
                "actions": [],
                "available_funds": float(max(Decimal('0'), self.val_cash)),
                "share_long_pct": float(self.share_long_pct),
                "share_short_pct": float(self.share_short_pct),
                "share_virt_pct": float(self.share_virt_pct),
                "share_cash_pct": float(self.share_cash_pct),
                "tpv": float(self.tpv),
                "total_tpv": float(self.total_tpv),
            }

        dec_threshold_surplus = Decimal(str(threshold_surplus))
        dec_threshold_deficit = Decimal(str(threshold_deficit))
        min_notional = self.min_notional
        
        # Actions split by intent
        surplus_actions: List[Dict] = []
        deficit_actions: List[Dict] = []
        
        # Use raw ratios for maximum precision
        shares: Dict[str, Decimal] = {
            "BASE_LONG": self.share_long_raw,
            "BASE_SHORT": self.share_short_raw,
            "VIRTUAL": self.share_virt_raw
        }

        # 1. Gather all legs that reached the threshold
        for key in ["BASE_LONG", "BASE_SHORT", "VIRTUAL"]:
            target_share = Decimal(str(targets[key]["share"]))
            current_share = shares[key]
            diff_share = current_share - target_share # Positive if surplus (actual > target)
            
            if not ignore_limits:
                flags = (trail_flags or {}).get(key)
                if flags is not None:
                    # Trailing gates the direction: readiness from observed extrema
                    # replaces the raw threshold test for futures legs.
                    reduce_ready, add_ready = flags
                    if diff_share > 0 and not reduce_ready:
                        continue
                    if diff_share < 0 and not add_ready:
                        continue
                else:
                    if diff_share > 0 and abs(diff_share) < share_surplus:
                        continue
                    elif diff_share < 0 and abs(diff_share) < share_deficit:
                        continue

            # Calculate theoretical diff_usdt
            # USE dynamic current_equity to ensure proportional rebalancing
            lev = Decimal(str(targets[key].get("leverage", 1)))
            diff_usdt = -diff_share * current_equity * lev
            diff_equity = -diff_share * current_equity # Real cash (margin) movement

            if abs(diff_usdt) < Decimal('1.0'): # Fundamental rounding filter
                continue

            action = {
                "key": key,
                "type": "VIRTUAL_ORDER" if key == "VIRTUAL" else "ORDER",
                "symbol": "VIRTUAL" if key == "VIRTUAL" else f"{self.base_ticker}_{key.replace('BASE_', '')}",
                "base_symbol": "VIRTUAL" if key == "VIRTUAL" else self.base_ticker,
                "position_side": "BOTH" if key == "VIRTUAL" else key.replace('BASE_', ''),
                "diff_usdt": float(diff_usdt),
                "requested_usdt": float(abs(diff_usdt)),
                "approved_usdt": float(abs(diff_usdt)),
                "diff_equity": float(diff_equity),
                "leverage": float(lev),
                "is_reduction": diff_usdt < 0
            }

            if action["is_reduction"]:
                surplus_actions.append(action)
            else:
                deficit_actions.append(action)

        # 2. Assemble final actions with Anti-Churn FUSE logic
        final_actions: List[Dict] = []
        total_proceeds = Decimal('0')
        estimated_net_profit = Decimal('0')

        # Last rebalance price for FUSE checks
        last_reb = self.last_rebalance_price
        # Cold start check: if last_reb is current price or ignore_limits is True, bypass FUSE
        bypass_fuse = ignore_limits or last_reb == self.price

        # Phase 1: Process SURPLUS (Priority 0)
        # PnL PROTECTION: If allow_surplus_sell is False, skip all surplus actions.
        # This prevents selling "winners" while the portfolio is in drawdown,
        # which would lock in profits on one side while losses accumulate on the other.
        for act in surplus_actions:
            if not allow_surplus_sell:
                logger.debug(f"🛡️ PnL GUARD: Surplus {act['key']} blocked (portfolio PnL < 0)")
                continue

            if abs(Decimal(str(act["diff_usdt"]))) < min_notional and not ignore_limits:
                continue

            act["priority"] = 0
            is_valid = True

            if fuse_enabled and not ignore_limits and act["key"] != "VIRTUAL":
                is_inverse = (act["position_side"] == "SHORT")
                if not is_inverse:
                    limit_price = last_reb * (1 + dec_threshold_surplus)
                    is_valid = bypass_fuse or self.price >= limit_price
                    # Closing proceeds must exceed entry after execution costs.
                    if is_valid and self.long_entry_price > 0:
                        if self.fuse_fill is None:
                            sell_price = self.price * (1 - self.slippage_pct)
                            is_valid = sell_price * (1 - self.commission_pct) > self.long_entry_price
                        else:
                            # Live gate sized to the actual order volume; missing taker rate or
                            # insufficient depth => closing unconfirmed, block the reduction
                            sell_qty = abs(Decimal(str(act["diff_usdt"]))) / self.price
                            px = book_vwap(self.fuse_fill["bids"], sell_qty)
                            taker = self.fuse_fill["taker"]
                            is_valid = bool(
                                px is not None and taker is not None
                                and px * (Decimal('1') - taker) > self.long_entry_price)
                else:
                    limit_price = last_reb * (1 - dec_threshold_surplus)
                    is_valid = bypass_fuse or self.price <= limit_price
                    # Closing cost includes the buy-side execution costs.
                    if is_valid and self.short_entry_price > 0:
                        if self.fuse_fill is None:
                            buy_price = self.price * (1 + self.slippage_pct)
                            is_valid = buy_price * (1 + self.commission_pct) < self.short_entry_price
                        else:
                            # Live gate sized to the actual order volume; missing taker rate or
                            # insufficient depth => closing unconfirmed, block the reduction
                            buy_qty = abs(Decimal(str(act["diff_usdt"]))) / self.price
                            px = book_vwap(self.fuse_fill["asks"], buy_qty)
                            taker = self.fuse_fill["taker"]
                            is_valid = bool(
                                px is not None and taker is not None
                                and px * (Decimal('1') + taker) < self.short_entry_price)

            if is_valid:
                final_actions.append(act)
                total_proceeds += abs(Decimal(str(act["diff_equity"])))
                if act["key"] != "VIRTUAL":
                    qty = min(abs(Decimal(str(act["diff_usdt"]))) / self.price,
                              abs(self.positions.get(act["symbol"], Decimal('0'))))
                    is_long = act["position_side"] == "LONG"
                    entry = self.long_entry_price if is_long else self.short_entry_price
                    fill_price = self.price * (1 - self.slippage_pct if is_long else 1 + self.slippage_pct)
                    if self.fuse_fill is not None:
                        fill_price = book_vwap(self.fuse_fill["bids" if is_long else "asks"], qty)
                    if fill_price is not None and entry > 0:
                        pnl = qty * (fill_price - entry) * (1 if is_long else -1)
                        estimated_net_profit += pnl - qty * fill_price * self.commission_pct
            else:
                logger.debug(f"🚫 FUSE (SURPLUS): {act['key']} blocked. Price {self.price:.6g} vs Limit {limit_price:.6g}")

        # Planned sale profit is only a sizing estimate; executors recheck confirmed net proceeds.
        building = self.initial_build
        if building:
            available_funds = max(Decimal('0'), self.val_cash)
        else:
            available_funds = min(max(Decimal('0'), self.val_cash) + total_proceeds,
                                  max(Decimal('0'), self.profit_budget + estimated_net_profit))
        
        # Futures deficits have first claim on limited CASH.
        deficit_actions.sort(key=lambda x: x["key"] == "VIRTUAL")
        
        for act in deficit_actions:
            lev = Decimal(str(act["leverage"]))
            needed_equity = abs(Decimal(str(act["diff_equity"])))
            
            if available_funds <= Decimal('0'):
                continue

            fee = Decimal('0') if act['key'] == 'VIRTUAL' else self.commission_pct
            actual_buy_usdt = min(needed_equity * lev, available_funds / (1 / lev + fee))

            if actual_buy_usdt >= min_notional or ignore_limits:
                act["diff_usdt"] = float(abs(actual_buy_usdt))
                act["approved_usdt"] = float(abs(actual_buy_usdt))
                act["priority"] = 2
                final_actions.append(act)
                available_funds -= actual_buy_usdt * (1 / lev + fee)

        return {
            "actions": final_actions,
            "available_funds": float(available_funds),
            "tpv": float(self.tpv),
            "total_tpv": float(self.total_tpv),
            "share_long_pct": float(self.share_long_pct),
            "share_short_pct": float(self.share_short_pct),
            "share_virt_pct": float(self.share_virt_pct),
            "share_cash_pct": float(self.share_cash_pct)
        }
