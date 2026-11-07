"""Correlation: realised from prices, implied through the Cboe COR indices, and pseudo CvC/PvP spreads.

Realised: closes of US and European names are hours apart, so pairs across zones use overlapping multi-day returns.

Implied: COR indices quote an average implied correlation of large SPX members. Its premium over the realised average
of the universe's US shares gives lambda, applied to any pair as rho' = rho + lambda (1 - rho). With lambda in [0, 1]
this is a mix of the matrix with the all-ones matrix, so a positive semi-definite matrix stays so.

CvC and PvP are the way correlation trades: an option on an equally weighted basket against the same option on each member,
struck in % of spot, usually 100 or 110 % for calls and 100 or 90 % for puts. With X_i = S_i(T) / S_i(0):
    CvC(K) = max(mean X_i - K, 0) - mean max(X_i - K, 0)      PvP(K) = max(K - mean X_i, 0) - mean max(K - X_i, 0)
Both are written long the basket: they gain with correlation and are never positive. The members do not depend on the
correlation and take out most of the volatility, so the correlation reads directly off the basket's implied volatility.

By put-call parity, CvC(K) = PvP(K): on one strike they are one instrument and carry one correlation. Long one and short the
other on one strike is flat, and the skew of correlation shows only between 110 % and 90 %.

There are no free quotes, so these are pseudo-quotes (origin = synthetic), never to be read as broker prices. Marginals come
from each name's surface, joined by a Gaussian copula with a correlation that is an assumption per strike, so reading it back
gives that assumption and no market information. What they do measure is how far the direct reading is from the correlation
that was put in, once the surfaces have a smile. Performances are in each name's own currency, with no quanto adjustment.
The formulas of the pseudo-quotes are `structured.localcorr`'s (they price products); this module reads the lake and
stores what they give.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from optlab.core.correlation import nearest_correlation
from optlab.core.instruments import instrument
from optlab.core.log import get_logger
from optlab.marketdata.lake import Lake, new_run_id, sql_str
from optlab.structured.localcorr import basket_rows

LOG = get_logger(__name__)

WINDOWS = (60, 120, 250)
CROSS_ZONE_HORIZON = 3  # days of overlapping returns between places with different closing hours
TENOR_WINDOW = {"COR1M": 21, "COR3M": 63, "COR6M": 126, "COR1Y": 252}


def closes_wide(lake: Lake, names, day=None, as_of=None) -> pd.DataFrame:
    quoted = ", ".join(sql_str(n) for n in names)
    where = f"instrument IN ({quoted})" + (
        f" AND obs_date <= DATE '{pd.Timestamp(day).date()}'" if day is not None else ""
    )
    rows = lake.read("prices_eod", where=where, columns="instrument, obs_date, close", as_of=as_of)
    wide = rows.pivot_table(index="obs_date", columns="instrument", values="close").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide[[n for n in names if n in wide.columns]]


def pair_horizon(a: str, b: str) -> int:
    return 1 if instrument(a).place == instrument(b).place else CROSS_ZONE_HORIZON


def rolling_pair(closes: pd.DataFrame, a: str, b: str, window: int) -> pd.Series:
    h = pair_horizon(a, b)
    both = closes[[a, b]].dropna()
    r = np.log(both).diff(h).dropna()
    return r[a].rolling(window).corr(r[b]).dropna()


def realised_matrix(closes: pd.DataFrame, names, window: int = 120) -> np.ndarray:
    """Correlation of the last `window` returns. On one zone, a sample covariance is semi-definite by construction;
    mixed horizons need the projection."""
    n = len(names)
    out = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            series = rolling_pair(closes.iloc[-(window + CROSS_ZONE_HORIZON + 10) :], names[i], names[j], window)
            out[i, j] = out[j, i] = float(series.iloc[-1]) if len(series) else 0.0
    return nearest_correlation(out)


def apply_premium(matrix, lam: float) -> np.ndarray:
    """rho' = rho + lambda (1 - rho), which keeps a semi-definite matrix for lambda in [0, 1]."""
    if not 0.0 <= lam <= 1.0:
        raise ValueError(f"lambda must be in [0, 1], got {lam}")
    m = np.asarray(matrix, dtype=float)
    return (1 - lam) * m + lam * np.ones_like(m)


def premium_series(implied: pd.Series, closes: pd.DataFrame, names, window: int) -> pd.Series:
    """lambda from an implied average correlation (fraction) and the realised average of `names`, in [0, 1]."""
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            pairs.append(rolling_pair(closes, a, b, window))
    avg = pd.concat(pairs, axis=1).mean(axis=1)
    both = pd.concat({"imp": implied, "avg": avg}, axis=1).dropna()
    return ((both["imp"] - both["avg"]) / (1 - both["avg"])).clip(0.0, 1.0)


def build_realized(lake: Lake, names) -> list[dict]:
    closes = closes_wide(lake, names)
    run = new_run_id("derive_correl")
    rows = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            for w in WINDOWS:
                s = rolling_pair(closes, a, b, w)
                rows.append(
                    pd.DataFrame(
                        {"name_a": a, "name_b": b, "window": w, "obs_date": s.index, "correlation": s.to_numpy()}
                    )
                )
    return [lake.ingest("correl_realized", pd.concat(rows, ignore_index=True), "prices_eod", "derived", run)]


def build_premium(lake: Lake, us_shares=("AAPL", "AMZN", "GOOGL", "GS", "IBM")) -> list[dict]:
    closes = closes_wide(lake, us_shares)
    run = new_run_id("derive_premium")
    out = []
    for name, window in TENOR_WINDOW.items():
        cor = lake.read(
            "correl_implied", where=f"index_name = '{name}' AND source = 'cboe'", columns="obs_date, tenor, correlation"
        )
        if cor.empty:
            continue
        implied = pd.Series(cor["correlation"].to_numpy(), index=pd.to_datetime(cor["obs_date"])).sort_index()
        lam = premium_series(implied, closes, list(us_shares), window)
        rows = pd.DataFrame(
            {
                "index_name": f"LAMBDA.{name}",
                "tenor": float(cor["tenor"].iloc[0]),
                "obs_date": lam.index,
                "correlation": lam.to_numpy(),
            }
        )
        out.append(lake.ingest("correl_implied", rows, "cor_vs_realised", "derived", run))
    return out


def premium_at(lake: Lake, day, index_name: str = "COR3M", as_of=None) -> float:
    rows = lake.read(
        "correl_implied",
        where=f"index_name = 'LAMBDA.{index_name}' AND obs_date <= DATE '{pd.Timestamp(day).date()}'",
        columns="obs_date, correlation",
        order="obs_date DESC LIMIT 1",
        as_of=as_of,
    )
    return float(rows["correlation"].iloc[0]) if len(rows) else 0.0


def correlation_at(
    lake: Lake, names, day, window: int = 120, implied: bool = True, as_of=None
) -> tuple[np.ndarray, dict]:
    """The correlation matrix of `names` from what is known at `day`: realised, with the implied premium when asked."""
    names = list(names)
    closes = closes_wide(lake, names, day, as_of)
    missing = [n for n in names if n not in closes.columns]
    if missing:
        raise KeyError(f"no prices of {', '.join(missing)} up to {day}")
    matrix = realised_matrix(closes, names, window)
    lam = premium_at(lake, day, as_of=as_of) if implied else 0.0
    return apply_premium(matrix, lam), {"window": window, "lambda": lam, "origin": "derived"}


def build_basket_quotes(lake: Lake, baskets, days, n_pairs: int = 100_000, as_of=None, log=None) -> list[dict]:
    """Pseudo-quotes on the reconstructed layer only, so that the series never changes layer. A day missing a member's market
    is skipped and counted: nothing is carried over."""
    from optlab.marketdata.derive.surfaces import surface_at
    from optlab.marketdata.pit import MODEL_SPOT, market_curves

    rows, skipped = [], 0
    for day in days:
        cache = {}

        def parts(name):
            if name not in cache:
                cache[name] = (
                    surface_at(lake, name, day, as_of, layer="reconstructed").surface,
                    market_curves(lake, name, day, as_of)[0],
                )
            return cache[name]

        for names in baskets:
            try:
                surfaces, markets = zip(*(parts(n) for n in names))
                base, _ = correlation_at(lake, names, day, as_of=as_of)
            except KeyError:
                skipped += 1
                continue
            rows += basket_rows(
                pd.Timestamp(day).date(),
                names,
                surfaces,
                lambda T, m=markets: [float(x.F(T)) / MODEL_SPOT for x in m],
                base,
                n_pairs=n_pairs,
            )
        if log:
            log(f"{pd.Timestamp(day).date()}: {len(rows)} lines, {skipped} skipped")
    if skipped:
        LOG.warning("basket quotes: %d basket-days skipped, a member had no market", skipped)
    if not rows:
        return []
    return [
        lake.ingest(
            "basket_spreads", pd.DataFrame(rows), "copula_on_surfaces", "synthetic", new_run_id("derive_baskets")
        )
    ]
