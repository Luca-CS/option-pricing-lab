"""The market of any name at a date, for the desk: read once from the lake and kept, so a book or a basket does not rebuild it.

The leverage of a context is calibrated on its first SLV use and stays in that context (`PricingContext.slv_model`), so it is
kept per name and date. One surface layer serves every name, as in the replay: a basket mixing an observed fit with reconstructions
would compare two kinds of errors. A name without that layer falls back on the best one of the day.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections import OrderedDict

import pandas as pd
from optlab.core.snapshot import MarketSnapshot
from optlab.marketdata.lake import Lake
from optlab.marketdata.pit import market_at

LAYER = "reconstructed"
KEPT = 64  # contexts in memory, each with its leverages


class NameMarkets:
    def __init__(self, lake: Lake, layer: str | None = LAYER, kept: int = KEPT):
        self.lake, self.layer, self.kept = lake, layer, kept
        self._cache: OrderedDict = OrderedDict()
        self.correlations: dict = {}  # local correlations calibrated per basket and date: BasketContext.correlation_cache
        self._lock = threading.Lock()

    def context(self, name: str, day, payment: str | None = None) -> tuple:
        """(PricingContext, MarketSnapshot) of a name at a date, on its quanto market when `payment` is another currency.
        KeyError when the lake cannot build it."""
        from optlab.structured.context import PricingContext

        day = pd.Timestamp(day).date()
        key = (name, day, payment)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        snap = self._snapshot(name, day, payment)
        got = PricingContext.from_snapshot(snap), snap
        with self._lock:
            got = self._cache.setdefault(key, got)  # two jobs may build it at once: they share the first one
            while len(self._cache) > self.kept:
                self._cache.popitem(last=False)
        return got

    def _snapshot(self, name: str, day: dt.date, payment: str | None = None) -> MarketSnapshot:
        try:
            return market_at(self.lake, name, day, layer=self.layer, payment=payment)
        except KeyError:
            if self.layer is None:
                raise
            return market_at(self.lake, name, day, payment=payment)
