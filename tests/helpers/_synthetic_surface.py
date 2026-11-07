"""A known SSVI surface on a flat market: the calibration tests fit it back, the SLV test reprices it."""

import numpy as np

from optlab.core import SSVI, Market

MATURITIES = np.array([0.1, 0.25, 0.5, 1.0, 2.0])
TRUE_SSVI = SSVI.power_law(-0.7, 1.1, 0.4, MATURITIES, 0.04 * MATURITIES * (1 + 0.2 * MATURITIES))
MARKET = Market.flat(100.0, 0.02, 0.01)
