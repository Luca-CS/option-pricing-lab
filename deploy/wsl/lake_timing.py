"""The reference reads of ADR-0009 and ADR-0022 on one lake folder: the coverage (every Parquet file of every table), the SPX closes
(the versions resolved), and the market of a day (curves, carry, surface; flat leverage). Best of five, after a warm-up.

    python lake_timing.py <lake folder> <label>      (in the worker image, or natively)
"""

import statistics
import sys
import time

from optlab.marketdata.lake import Lake
from optlab.marketdata.pit import market_at

lake = Lake(sys.argv[1])
cases = {
    "coverage": lambda: lake.coverage(),
    "spx_closes": lambda: lake.read("prices_eod", where="instrument = 'SPX'"),
    "market_at": lambda: market_at(lake, "SPX", "2026-11-04"),
}
for name, fn in cases.items():
    fn()
    times = []
    for _ in range(5):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    print(f"{sys.argv[2]:6s} {name:12s} best {min(times):.3f} s  median {statistics.median(times):.3f} s", flush=True)
