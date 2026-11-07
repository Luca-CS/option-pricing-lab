"""A fictitious bank's credit curve (origin = synthetic): a fixed premium per tenor, a beta on a real credit index, and
an AR(1) of its own. The parameters and the seed are versioned here, so the curve is reproducible. The noise is drawn in
date order from a fixed start, so that adding future days never changes a past level.

It plugs into the existing `wrapper.issuer_spread` and `wrapper.funding_spread` fields of a term sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from optlab.marketdata.lake import Lake, new_run_id

ENTITY = "BANQUE-FICTIVE"


@dataclass(frozen=True)
class BankParams:
    premium: dict = field(default_factory=lambda: {1.0: 0.0030, 2.0: 0.0040, 3.0: 0.0050, 5.0: 0.0070, 7.0: 0.0085})
    beta: float = 0.35  # on (BAA - 10-year Treasury) minus its reference level
    credit_reference: float = 0.019
    phi: float = 0.98  # daily persistence of the idiosyncratic part
    sigma: float = 0.0003  # its daily volatility
    floor: float = 0.0005
    seed: int = 20260105
    start: str = "2015-01-01"  # the noise starts here, whatever the requested dates


def bank_spreads(credit_index: pd.Series, params: BankParams = BankParams()) -> pd.DataFrame:
    """credit_index: BAA minus 10-year Treasury in fractions, indexed by date. obs_date, tenor, spread (fractions)."""
    index = credit_index.sort_index()
    index.index = pd.to_datetime(index.index)
    days = index.index[index.index >= params.start]
    rng = np.random.default_rng(params.seed)
    shocks = rng.standard_normal(len(days))
    ar = np.empty(len(days))
    level = 0.0
    for i, z in enumerate(shocks):
        level = params.phi * level + params.sigma * z
        ar[i] = level
    common = params.beta * (index.loc[days].to_numpy() - params.credit_reference) + ar
    rows = [
        pd.DataFrame({"obs_date": days, "tenor": t, "spread": np.maximum(p + common, params.floor)})
        for t, p in params.premium.items()
    ]
    return pd.concat(rows, ignore_index=True).assign(entity=ENTITY)


def credit_index(lake: Lake) -> pd.Series:
    """BAA minus the 10-year Treasury, in fractions, on the days both exist."""
    baa = lake.read("rate_points", where="curve = 'USD.BAA'", columns="obs_date, rate")
    t10 = lake.read("rate_points", where="curve = 'USD.CMT10'", columns="obs_date, rate")
    both = baa.merge(t10, on="obs_date", suffixes=("_b", "_t")).sort_values("obs_date")
    return pd.Series((both["rate_b"] - both["rate_t"]).to_numpy() / 100, index=pd.to_datetime(both["obs_date"]))


def build_bank(lake: Lake, params: BankParams = BankParams()) -> dict:
    return lake.ingest(
        "credit_spreads",
        bank_spreads(credit_index(lake), params),
        "fictive_bank",
        "synthetic",
        new_run_id("derive_bank"),
    )


def spread_at(lake: Lake, day, tenor: float, entity: str = ENTITY) -> float:
    """The curve's spread at `day` or the day before, interpolated in tenor (flat outside)."""
    day = pd.Timestamp(day).date()
    rows = lake.read(
        "credit_spreads",
        where=f"entity = '{entity}' AND obs_date <= DATE '{day}' AND obs_date >= DATE '{day}' - INTERVAL 10 DAY",
        order="obs_date",
    )
    if rows.empty:
        raise KeyError(f"no spread of {entity} around {day}")
    last = rows[rows["obs_date"] == rows["obs_date"].max()].sort_values("tenor")
    return float(np.interp(tenor, last["tenor"], last["spread"]))


def wrapper_spreads(lake: Lake, day, maturity_years: float) -> dict:
    """The term sheet's wrapper fields at a date, from the fictitious bank's curve."""
    s = spread_at(lake, day, maturity_years)
    return {"wrapper.issuer_spread": s, "wrapper.funding_spread": s}
