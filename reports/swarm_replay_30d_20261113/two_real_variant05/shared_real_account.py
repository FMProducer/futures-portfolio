"""Offline shared exchange wallet for two independently routed strategy workers."""
from decimal import Decimal as D


class SharedAccount:
    def __init__(self):
        self.wallet = D(7000)
        self.connectors = {}

    def attach(self, connector):
        assert connector.ticker not in self.connectors and len(self.connectors) < 2
        self.connectors[connector.ticker] = connector

    def totals(self):
        margin = unrealized = maintenance = D(0)
        for connector in self.connectors.values():
            price = connector.price()
            for leg, (qty, entry) in connector.positions.items():
                margin += qty*entry/D(str(connector.targets[f'BASE_{leg}']['leverage']))
                unrealized += qty*(price-entry)*(1 if leg == 'LONG' else -1)
                maintenance += qty*price*D('.004')
        return dict(wallet=self.wallet, available=self.wallet-margin,
                    equity=self.wallet+unrealized, maintenance=maintenance)

    def reconcile(self, owners, closed_cashflow=D(0)):
        assert len(owners) <= 2 and set(owners) <= {'ONUSDT','HOTUSDT'}
        expected = D(7000)+D(str(closed_cashflow))
        identities = set()
        for ticker,(state,wallet) in owners.items():
            identity = (ticker,str(state['started_at']))
            assert identity not in identities
            identities.add(identity)
            assert state['base_ticker'] == wallet['base_ticker'] == ticker
            capital = D(str(state['initial_tpv']))
            assert capital.is_finite() and capital > 0
            expected += D(str(wallet['balance']))-capital+D(str(state.get('siphoning_reserve',0)))
        if not expected.is_finite() or abs(self.wallet-expected) > D('.00000001'):
            raise ValueError('Shared modeled REAL wallet has unexplained cashflows')
        return expected


def shared_connector_type(base):
    class Shared(base):
        @property
        def wallet(self):
            return self.account.wallet if hasattr(self,'account') else self._opening_wallet

        @wallet.setter
        def wallet(self,value):
            if hasattr(self,'account'):
                self.account.wallet = D(value)
            else:
                self._opening_wallet = D(value)

        async def get_usdt_account(self):
            totals = self.account.totals()
            return {key:str(totals[key]) for key in ('wallet','available')}

        async def get_margin_ratio(self):
            totals = self.account.totals()
            maintenance = totals['maintenance']
            return dict(margin_ratio=float(totals['equity']/maintenance) if maintenance else float('inf'),
                total_wallet_balance=float(totals['wallet']),total_margin_balance=float(totals['equity']),
                total_maint_margin=float(maintenance))
    return Shared
