"""Independent arithmetic oracle for the explicit user pullback/rebound contract."""
def reference(self, share_a, share_b):
    def ready(share, peak, trough):
        return (peak >= self._base_surplus_thr and share > 35 and peak-share >= self._trail_distance,
                trough <= self._base_deficit_thr and share < 35 and share-trough >= self._trail_distance)
    a = ready(share_a, self._peak_share_a, self._trough_share_a)
    b = ready(share_b, self._peak_share_b, self._trough_share_b)
    return a[0], b[0], a[1], b[1]

def update_extrema(self, share_a, share_b):
    for leg, share in [('a',share_a),('b',share_b)]:
        if share >= self._base_surplus_thr:
            setattr(self,'_peak_share_'+leg,max(getattr(self,'_peak_share_'+leg),share))
        if share <= self._base_deficit_thr:
            setattr(self,'_trough_share_'+leg,min(getattr(self,'_trough_share_'+leg),share))

def consume_extremum(self, action, share_a, share_b):
    field = '_peak_share_' if action.direction == 'SELL' else '_trough_share_'
    setattr(self,field+action.leg.lower(),35)
