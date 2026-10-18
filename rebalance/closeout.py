"""User-approved synthetic candle terminal closeout, offline research only."""
from copy import deepcopy
from decimal import Decimal as D, getcontext

from .kernel import Action, SIDES, decimal


def closeout(portfolio, mark, spread_bps, slippage_bps, capital, curve):
    mark, spread, slip, capital = map(decimal, (mark, spread_bps, slippage_bps, capital))
    friction = (spread/2+slip)/10000
    if mark <= 0 or capital <= 0 or min(spread, slip) < 0 or friction >= 1:
        raise ValueError('invalid closeout inputs')
    if not curve:
        raise ValueError('terminal curve required')
    previous = None
    values = [capital]
    for row in curve:
        stamp, equity = row['time'], decimal(row['equity'])
        if type(stamp) is not int or stamp < 0 or previous is not None and stamp <= previous:
            raise ValueError('invalid curve chronology')
        values.append(equity)
        previous = stamp
    p = deepcopy(portfolio)
    if p.order_reserve or p.liability:
        raise ValueError('closeout requires research state without reserve/liability')
    for side in SIDES:
        leg = p.legs[side]
        if type(leg.units) is not int or leg.units < 0:
            raise ValueError('invalid terminal units')
        if leg.units and decimal(leg.avg) <= 0:
            raise ValueError('invalid terminal entry price')
        for value in (leg.margin, leg.open_fees):
            if decimal(value) < 0:
                raise ValueError('invalid terminal leg balance')
        if not leg.units and any((leg.avg, leg.margin, leg.open_fees)):
            raise ValueError('inconsistent flat leg')
    before = p.snapshot(mark)['equity']
    if before != values[-1]:
        raise ValueError('terminal state/curve mismatch')
    bid, ask = mark*(1-friction), mark*(1+friction)
    adjustment = fees = D(0)
    fills = []
    for side, price in (('LONG', bid), ('SHORT', ask)):
        units = p.legs[side].units
        if not units:
            continue
        quantity = units*p.config.step
        fee = quantity*price*p.config.fee_rate
        adjustment += quantity*(price-mark)*(1 if side == 'LONG' else -1)
        fees += fee
        identity = 'synthetic-terminal-'+str(previous)+'-'+side
        if identity in p.receipts:
            raise ValueError('terminal receipt already exists')
        p.fill(identity, Action('REDUCE', side, units), price, fee, mark)
        fills.append(dict(id=identity,time=previous,phase='TERMINAL',kind='REDUCE',side=side,
                          units=units,quantity=str(quantity),price=str(price),fee=str(fee)))
    equity = p.snapshot(mark)['equity']
    expected = before+adjustment-fees
    # Decimal recombination of avg-cost PnL can differ at the final context ULP.
    scale = max(abs(before), abs(equity), abs(expected), D(1)).adjusted()
    tolerance = D(8).scaleb(scale-getcontext().prec+1)
    if abs(equity-expected) > tolerance:
        raise ValueError('closeout arithmetic bridge mismatch')
    if any(leg.units or leg.margin or leg.avg or leg.open_fees for leg in p.legs.values()):
        raise ValueError('closeout did not flatten both legs')
    peak, drawdown = capital, D(0)
    for value in values[1:]+[equity]:
        peak = max(peak, value)
        drawdown = max(drawdown, (peak-value)/peak*100)
    return dict(mode='SYNTHETIC_CANDLE_CLOSEOUT',time=previous,mark_proxy=str(mark),
                sell_price=str(bid),buy_price=str(ask),equity_open=str(before),
                price_adjustment=str(adjustment),exit_fees=str(fees),equity_flat=str(equity),
                net_flat=str(equity-capital),formula_equity=str(expected),
                decimal_bridge_residual=str(equity-expected),
                max_close_plus_exit_drawdown_pct=str(drawdown),
                terminal_curve_point=dict(time=previous,phase='TERMINAL',equity=str(equity)),
                fills=fills,quantities={s:str(p.legs[s].units*p.config.step) for s in SIDES},
                margin=str(p.snapshot(mark)['margin']),unrealized='0',
                total_fees=str(p.fees),funding=str(p.funding),
                admission_ready=False,trading_authorized=False,
                limitations=['synthetic full fills; not exchange execution evidence',
                             'same-time terminal phase; no elapsed time/funding beyond window',
                             'close-only drawdown plus exit; no intrabar liquidation model'])
