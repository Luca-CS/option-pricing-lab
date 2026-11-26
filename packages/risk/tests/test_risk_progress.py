"""A Greeks run's progress goes through its simulations: the first one ends well before the run does (a run reported at
100 % with nothing left while dozens of shocks were still to price looked stalled)."""

import datetime as dt

from _paths import SPX_CALIBRATION

from optlab.core import progress
from optlab.risk import RiskGroup, RiskSettings
from optlab.risk.greeks import GreekOptions, compute
from optlab.structured import preset
from optlab.structured.context import PricingContext
from optlab.structured.portfolio import Book, Position


class Fractions:
    def __init__(self):
        self.seen = []

    def tick(self, done, total, unit):
        self.seen.append(done / total)


def test_a_greeks_run_reports_its_progress_across_every_simulation():
    ctx = PricingContext.from_calibration(SPX_CALIBRATION)
    sheet = preset(
        "barrier_reverse_convertible", **{"general.strike_date": dt.date(2026, 10, 27), "wrapper.solve": "upfront"}
    )
    group = RiskGroup("SPX", Book([Position("BRC", sheet, -1.0)]), ctx)
    options = GreekOptions(kxt="none", curves=False, sticky_strike=False, realistic=False, cross=False, theta=False)
    r = Fractions()
    with progress.reporting(r):
        compute([group], options=options, settings=RiskSettings(n_paths=2_000, batch=500, engine="numpy"))
    assert r.seen == sorted(r.seen) and r.seen[-1] == 1.0
    first_done = next(i for i, x in enumerate(r.seen) if x > 0)  # the base's first batch
    assert r.seen[first_done + 3] < 0.2  # the base's four batches are a small part of the run
    assert len({round(x, 3) for x in r.seen}) > 10  # it moves with each simulation
