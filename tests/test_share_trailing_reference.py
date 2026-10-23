"""Sequence parity with an independent requirement-derived oracle."""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from core import share_trail
from tests.fixtures.share_trailing_t_invest_reference import reference, update_extrema, consume_extremum


@pytest.mark.parametrize("distance", ["1e-12", ".001", ".005", ".02"])
def test_reference_sequence_including_consumed_fills_and_restart(distance):
    # Reference percentages become fractions only at the adapter boundary.
    ref = SimpleNamespace(_base_surplus_thr=D(36), _base_deficit_thr=D('29.5'),
                          _trail_distance=D(distance)*100, _peak_share_a=D(35),
                          _peak_share_b=D(35), _trough_share_a=D(35), _trough_share_b=D(35))
    state = share_trail.fresh_state()
    for a,b in [('35','35'),('36.2','28'),('36.1','28.1'),('37','27'),('35.8','29'),
                ('35.5','30'),('38','25'),('35','35'),('36.2','28')]:
        a,b = D(a),D(b)
        expected = reference(ref,a,b)
        got_a = share_trail.step(state,"BASE_LONG",a/100,D('.35'),D('.36'),D('.295'),D(distance))
        got_b = share_trail.step(state,"BASE_SHORT",b/100,D('.35'),D('.36'),D('.295'),D(distance))
        assert (got_a[0],got_b[0],got_a[1],got_b[1]) == expected
        update_extrema(ref,a,b)
        for key,leg,red,add in [('BASE_LONG','A',got_a[0],got_a[1]),('BASE_SHORT','B',got_b[0],got_b[1])]:
            if red or add:
                direction = 'REDUCE' if red else 'ADD'
                share_trail.consume(state,key,direction)
                consume_extremum(ref,SimpleNamespace(leg=leg,direction='SELL' if red else 'BUY'),a,b)
        state = share_trail.load_state(state)
        assert D(state['BASE_LONG']['peak'] or '.35')*100 == ref._peak_share_a
        assert D(state['BASE_SHORT']['trough'] or '.35')*100 == ref._trough_share_b
