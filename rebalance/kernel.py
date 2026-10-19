"""Shared USDT wallet adaptation of account_equity_v1, offline only.

Quantities are integer exchange-step units; all economics use Decimal.
Margin is an explicit fixed-leverage simulation, NOT a Binance risk engine.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal as D

ZERO = D('0')
SIDES = ('LONG', 'SHORT')


def decimal(value):
    if isinstance(value, (bool, float)):
        raise ValueError('use Decimal, integer or decimal string')
    result = D(value)
    if not result.is_finite():
        raise ValueError('non-finite number')
    return result


@dataclass(frozen=True)
class Config:
    step: D
    min_notional: D
    leverage: D
    fee_rate: D
    surplus_pp: D
    deficit_pp: D
    trail_pp: D

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if (self.step <= 0 or self.leverage < 1 or self.min_notional < 0
                or not 0 <= self.fee_rate < 1 or not 0 <= self.surplus_pp < 25
                or not 0 <= self.deficit_pp < 25 or self.trail_pp < 0):
            raise ValueError('invalid configuration')


@dataclass
class Leg:
    units: int = 0
    avg: D = ZERO
    margin: D = ZERO
    open_fees: D = ZERO
    initial_units: int = 0


@dataclass(frozen=True)
class Action:
    kind: str
    side: str = ''
    units: int = 0
    reason: str = ''


def nearest(distance, unit, maximum):
    if distance <= 0 or unit <= 0 or maximum <= 0:
        return 0
    low = int(distance / unit)
    return min({0, min(low, maximum), min(low + 1, maximum)},
               key=lambda n: (abs(distance - unit * n), n))


@dataclass
class Portfolio:
    config: Config
    wallet: D
    liability: D = ZERO
    order_reserve: D = ZERO
    legs: dict = field(default_factory=lambda: {s: Leg() for s in SIDES})
    extrema: dict = field(default_factory=dict)
    receipts: dict = field(default_factory=dict)
    fees: D = ZERO
    realized: D = ZERO
    funding: D = ZERO

    def __post_init__(self):
        self.wallet = decimal(self.wallet)
        self.liability = decimal(self.liability)
        self.order_reserve = decimal(self.order_reserve)
        if min(self.liability, self.order_reserve) < 0:
            raise ValueError('negative reserve')

    def snapshot(self, mark):
        mark = decimal(mark)
        if mark <= 0:
            raise ValueError('invalid mark')
        rows = {}
        for side, leg in self.legs.items():
            qty = leg.units * self.config.step
            upl = qty * (mark - leg.avg) * (1 if side == 'LONG' else -1)
            rows[side] = {'upl': upl, 'decision': qty * leg.avg + upl - leg.open_fees}
        upl = sum((r['upl'] for r in rows.values()), ZERO)
        equity = self.wallet + upl
        trading = equity - self.liability
        margin = sum((l.margin for l in self.legs.values()), ZERO)
        # Conservative simulator: no borrowing against net positive UPL.
        available = max(ZERO, min(self.wallet - margin, equity - margin)
                        - self.liability - self.order_reserve)
        for row in rows.values():
            row['share'] = row['decision'] / trading * 100 if trading > 0 else None
        return dict(equity=equity, trading_equity=trading, target=trading / 4,
                    remainder=trading-sum((r['decision'] for r in rows.values()), ZERO),
                    margin=margin, available=available, legs=rows)

    def _duplicate(self, identity, receipt):
        if not isinstance(identity, str) or not identity:
            raise ValueError('missing event identity')
        if identity in self.receipts:
            if self.receipts[identity] != receipt:
                raise ValueError('conflicting event replay')
            return True
        return False

    def cash_event(self, identity, amount, kind):
        """Confirmed funding or external flow; never inferred from price."""
        amount = decimal(amount)
        if kind not in ('FUNDING', 'EXTERNAL'):
            raise ValueError('invalid cash event')
        receipt = (kind, str(amount))
        if self._duplicate(identity, receipt):
            return
        self.wallet += amount
        if kind == 'FUNDING':
            self.funding += amount
        self.receipts[identity] = receipt

    def fill(self, identity, action, price, fee, mark):
        price, fee, mark = map(decimal, (price, fee, mark))
        if (action.kind not in ('ADD', 'REDUCE') or action.side not in SIDES
                or type(action.units) is not int or action.units <= 0
                or price <= 0 or mark <= 0 or fee < 0):
            raise ValueError('invalid fill')
        receipt = (action.kind, action.side, action.units, str(price), str(fee))
        if self._duplicate(identity, receipt):
            return
        leg = self.legs[action.side]
        qty = action.units * self.config.step
        if action.kind == 'ADD':
            collateral = qty * price / self.config.leverage
            if collateral + fee > self.snapshot(mark)['available']:
                raise ValueError('insufficient shared funded balance')
            leg.avg = (leg.units * leg.avg + action.units * price) / (leg.units + action.units)
            leg.units += action.units
            leg.margin += collateral
            leg.open_fees += fee
            self.wallet -= fee
        else:
            if action.units > leg.units:
                raise ValueError('over-close')
            gross = qty * (price - leg.avg) * (1 if action.side == 'LONG' else -1)
            fraction = D(action.units) / leg.units
            leg.margin -= leg.margin * fraction
            leg.open_fees -= leg.open_fees * fraction
            leg.units -= action.units
            self.wallet += gross - fee
            self.realized += gross
            if not leg.units:
                leg.margin = leg.avg = leg.open_fees = ZERO
        self.fees += fee
        self.receipts[identity] = receipt
        if action.side in self.extrema:
            row = self.extrema[action.side]
            half = (self.config.surplus_pp + self.config.deficit_pp) / 2
            row['peak' if action.kind == 'REDUCE' else 'trough'] = (
                D(25) + half if action.kind == 'REDUCE' else D(25) - D('.9') * half)
            if not leg.units:
                row['peak'] = row['trough'] = None

    def plan_entry(self, mark, bid, ask):
        """Preflight both legs; entry is simulated sequentially on a copy."""
        mark, bid, ask = quotes(mark, bid, ask)
        if any(l.units for l in self.legs.values()):
            raise ValueError('entry requires flat portfolio')
        trial, actions = deepcopy(self), []
        for side, price in (('LONG', ask), ('SHORT', bid)):
            target = trial.snapshot(mark)['target']
            unit = self.config.step * (price + (mark-price)*(1 if side == 'LONG' else -1))
            unit -= self.config.step * price * self.config.fee_rate
            fee_unit = self.config.step * price * self.config.fee_rate
            natural = int(target / unit) if unit > 0 and target > 0 else 0
            affordable = int(trial.snapshot(mark)['available'] /
                             (self.config.step*price/self.config.leverage+fee_unit))
            units = min(natural, affordable)
            if not units or units*self.config.step*price < self.config.min_notional:
                return [Action('WAIT', reason='entry_min_notional_or_step')]
            action = Action('ADD', side, units)
            try:
                trial.fill('entry-' + side, action, price,
                           units*self.config.step*price*self.config.fee_rate, mark)
            except ValueError:
                return [Action('WAIT', reason='entry_shared_funding')]
            actions.append(action)
        return actions

    def next_action(self, mark, bid, ask):
        mark, bid, ask = quotes(mark, bid, ask)
        snap = self.snapshot(mark)
        if snap['trading_equity'] <= 0:
            return Action('WAIT', reason='non_positive_equity')
        ready = {}
        for side in SIDES:
            row = self.extrema.setdefault(side, {'peak': D(25), 'trough': D(25)})
            share = snap['legs'][side]['share']
            ready[side] = {kind: bool(self.legs[side].units and row[key] is not None and
                ((row[key]-share) if kind == 'REDUCE' else (share-row[key])) >=
                self.config.trail_pp*(D('.3') if kind == 'REDUCE' and side == 'SHORT' else D(1)))
                for kind, key in (('REDUCE', 'peak'), ('ADD', 'trough'))}
            if self.legs[side].units:
                row['peak'] = max(D(25) if row['peak'] is None else row['peak'], share)
                row['trough'] = min(D(25) if row['trough'] is None else row['trough'], share)
            else:
                row['peak'] = row['trough'] = None
        pending = None
        for kind in ('REDUCE', 'ADD'):
            for side in SIDES:
                leg = self.legs[side]
                if not leg.units or not ready[side][kind]:
                    continue
                sign = 1 if side == 'LONG' else -1
                price = (bid if side == 'LONG' else ask) if kind == 'REDUCE' else (ask if side == 'LONG' else bid)
                fee_unit = self.config.step * price * self.config.fee_rate
                distance = ((snap['legs'][side]['decision']-snap['target']) if kind == 'REDUCE'
                            else (snap['target']-snap['legs'][side]['decision']))
                if kind == 'REDUCE':
                    unit = self.config.step*(leg.avg+sign*(mark-leg.avg))-leg.open_fees/leg.units
                    scale = D('.9')*(D('1.5') if unit > 0 and abs(distance) >= 5*unit else D(1))
                    units = nearest(distance*scale, unit, leg.units)
                    if sign*(price-leg.avg) < 0:
                        continue
                    if units*self.config.step*sign*(price-leg.avg) < units*fee_unit + leg.open_fees*D(units)/leg.units:
                        continue
                else:
                    unit = self.config.step*(price+sign*(mark-price))-fee_unit
                    other = self.legs['SHORT' if side == 'LONG' else 'LONG']
                    scale = D('.81875')*(D('.9') if leg.units < other.units else D(1))
                    natural = nearest(distance*scale, unit, 10**12)
                    affordable = int(snap['available']/(self.config.step*price/self.config.leverage+fee_unit))
                    cap = 2*leg.initial_units if leg.initial_units > 0 else max(1, 2*leg.units)
                    units = min(natural, affordable, max(0, cap-leg.units))
                    if natural and not units:
                        pending = Action('WAIT', side, reason='add_shared_margin_or_cap')
                if not units or units*self.config.step*price < self.config.min_notional:
                    continue
                edge = abs(distance)-abs(distance-units*unit)
                if edge >= (3 if kind == 'REDUCE' else 1)*fee_unit*units:
                    return Action(kind, side, units)
        return pending or Action('WAIT', reason='no_executable_signal')


def quotes(mark, bid, ask):
    mark, bid, ask = map(decimal, (mark, bid, ask))
    if min(mark, bid, ask) <= 0 or bid > ask:
        raise ValueError('invalid quotes')
    return mark, bid, ask
