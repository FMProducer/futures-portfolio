"""Historical payment inputs for offline parity adapters, not trading policy."""
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import sys
from unittest.mock import patch

from core import funding


class HistoricalFunding:
    def __init__(self, symbol, rows):
        self.symbol, self.rows, self.cursor = symbol, rows, 0
        self.observations = []
        previous = -1
        for row in rows:
            timestamp = row['fundingTime']
            if (type(timestamp) is not int or timestamp <= previous or row['symbol'] != symbol
                    or not Decimal(str(row['fundingRate'])).is_finite()
                    or not Decimal(str(row['markPrice'])).is_finite()
                    or Decimal(str(row['markPrice'])) <= 0):
                raise ValueError('Historical funding needs ordered unique times, rates and actual mark prices')
            previous = timestamp

    @classmethod
    def read(cls, symbol, path):
        return cls(symbol, json.loads(Path(path).read_text(encoding='utf-8')))

    def due(self, timestamp_ms):
        start = self.cursor
        while self.cursor < len(self.rows) and self.rows[self.cursor]['fundingTime'] <= timestamp_ms:
            self.cursor += 1
        return self.rows[start:self.cursor]

    def amount(self, row, long_qty, short_qty):
        long_qty, short_qty = Decimal(str(long_qty)), Decimal(str(short_qty))
        if not long_qty.is_finite() or not short_qty.is_finite() or min(long_qty, short_qty) < 0:
            raise ValueError('Historical funding requires nonnegative finite hedge positions')
        amount = (short_qty-long_qty)*Decimal(str(row['markPrice']))*Decimal(str(row['fundingRate']))
        self.observations.append(dict(fundingTime=row['fundingTime'], fundingRate=str(row['fundingRate']),
            markPrice=str(row['markPrice']), long_qty=str(long_qty), short_qty=str(short_qty),
            signed_amount=str(amount)))
        return amount

    def receive(self, journal, timestamp_ms, long_qty, short_qty):
        added = False
        for row in self.due(timestamp_ms):
            amount = self.amount(row, long_qty, short_qty)
            if amount == 0:
                continue
            event = funding.FundingEvent(event_id=f"rates:{self.symbol}:{row['fundingTime']}",
                signed_amount=amount, event_time=row['fundingTime']/1000,
                source='historical_rate_mark_price', source_ref=json.dumps(row, sort_keys=True), symbol=self.symbol)
            if journal.receive(event) not in ('new', 'duplicate'):
                raise ValueError('Historical funding input conflicts with the runtime journal')
            added = True
        return added


@contextmanager
def paper_funding_bridge(schedule):
    if schedule is None:
        yield
        return
    original = funding.FundingJournal.get_received

    def received(journal):
        caller = sys._getframe(1)
        if caller.f_code.co_name == 'rebalance_loop' and caller.f_locals.get('paper_mode') is True:
            scope = caller.f_locals
            wallet, state = scope['paper_state'], scope['state']
            positions = wallet['positions']
            if schedule.receive(journal, scope['strategy_bar_ms'],
                                positions.get(f'{schedule.symbol}_LONG', 0),
                                positions.get(f'{schedule.symbol}_SHORT', 0)):
                # The pending event must reach the native WAL before reserve settlement.
                funding.save_funding_boundary(scope['state_file_path'], scope['paper_state_file_path'],
                                              state, wallet, journal, funding.FundingHalt.from_dict(state.get('funding_halt')))
        return original(journal)

    with patch.object(funding.FundingJournal, 'get_received', new=received):
        yield


@contextmanager
def backtest_funding_bridge(schedule):
    original = funding.FundingJournal.get_received

    def received(journal):
        caller = sys._getframe(1)
        if caller.f_code.co_name == 'run_backtest' and Path(caller.f_code.co_filename).name == 'backtest_rebalance.py':
            scope = caller.f_locals
            state = scope['state']
            schedule.receive(journal, int(scope['time_window'][scope['i']]), state.pos_long, state.pos_short)
        return original(journal)

    with patch.object(funding.FundingJournal, 'get_received', new=received):
        yield
