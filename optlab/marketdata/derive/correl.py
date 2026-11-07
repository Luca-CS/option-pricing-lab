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
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.special import ndtr

from optlab.blackscholes import black_price, implied_vol
from optlab.log import get_logger
from optlab.marketdata.lake import Lake, new_run_id, sql_str
from optlab.marketdata.universe import instrument

LOG = get_logger(__name__)

WINDOWS = (60, 120, 250)
CROSS_ZONE_HORIZON = 3  # days of overlapping returns between places with different closing hours
TENOR_WINDOW = {"COR1M": 21, "COR3M": 63, "COR6M": 126, "COR1Y": 252}
STRIKES = {"cvc": (1.0, 1.1), "pvp": (1.0, 0.9)}  # in % of spot, where they trade
CP = {"cvc": "call", "pvp": "put"}
SEED = 2026


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


def nearest_correlation(c, iterations: int = 50) -> np.ndarray:
    """The closest positive semi-definite matrix with a unit diagonal, by alternating projections."""
    x = np.array(c, dtype=float)
    x = 0.5 * (x + x.T)
    for _ in range(iterations):
        lam, vec = np.linalg.eigh(x)
        if lam.min() >= -1e-12 and np.allclose(np.diag(x), 1.0, atol=1e-12):
            break
        x = (vec * np.maximum(lam, 0.0)) @ vec.T
        np.fill_diagonal(x, 1.0)
    lam, vec = np.linalg.eigh(x)
    if lam.min() < 0:
        x = (vec * np.maximum(lam, 1e-10)) @ vec.T
        d = np.sqrt(np.diag(x))
        x = x / np.outer(d, d)
    x = 0.5 * (x + x.T)
    np.fill_diagonal(x, 1.0)
    return x


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


# --- two-asset prices on the marginals of two surfaces ---
def forward_quantile(surface, T: float, n_k: int = 801):
    """The quantile function of X = S_T / F from a surface: u -> X, through -dC/dK."""
    sd = float(np.sqrt(surface.theta(T)))
    k = np.linspace(-7.0 * sd, 6.0 * sd, n_k)
    s = np.sqrt(np.maximum(np.asarray(surface.w(k, T)), 1e-12))
    K = np.exp(k)
    d1 = -k / s + 0.5 * s
    call = ndtr(d1) - K * ndtr(d1 - s)
    cdf = 1.0 + np.gradient(call, K)
    cdf = np.maximum.accumulate(np.clip(cdf, 0.0, 1.0))
    keep = np.concatenate([[True], np.diff(cdf) > 1e-14])
    return lambda u: np.exp(np.interp(u, cdf[keep], k[keep]))


def _normal_nodes(n: int):
    x, w = np.polynomial.legendre.leggauss(n)
    z, weight = 7.0 * x, 7.0 * w * np.exp(-0.5 * (7.0 * x) ** 2) / np.sqrt(2 * np.pi)
    return z, weight / weight.sum()


def copula_expectation(payoff, q_a, q_b, rho: float, n: int = 200) -> float:
    """E[payoff(X1, X2)] with marginal quantiles q_a, q_b and a Gaussian copula, by tensor Gauss-Legendre quadrature."""
    z, w = _normal_nodes(n)
    e = z[:, None]
    x1 = np.broadcast_to(q_a(ndtr(z))[None, :], (n, n))
    z2 = rho * z[None, :] + np.sqrt(max(1 - rho * rho, 0.0)) * e
    x2 = q_b(ndtr(z2))
    return float(np.sum(w[:, None] * w[None, :] * payoff(x1, x2)))


# --- CvC and PvP: an option on a basket against the same options on its members ---
@dataclass(frozen=True)
class CorrelationSkew:
    """The assumption behind the pseudo-quotes: an offset to the correlation that depends on the strike only, higher below
    the spot and lower above. It cannot depend on the kind, since a CvC and a PvP on one strike are one instrument."""

    slope_down: float = 0.5
    slope_up: float = 0.25

    def offset(self, strike: float) -> float:
        return self.slope_down * max(1.0 - strike, 0.0) - self.slope_up * max(strike - 1.0, 0.0)


def skewed_matrix(matrix, strike: float, skew: CorrelationSkew = CorrelationSkew()) -> np.ndarray:
    """A valid correlation matrix for a product living around this strike, every pair moved by the same offset. This is how
    the one correlation of `BasketContext` can carry a skew until the simulation has a local correlation."""
    moved = np.clip(np.asarray(matrix, dtype=float) + skew.offset(strike), -0.95, 0.99)
    np.fill_diagonal(moved, 1.0)
    return nearest_correlation(moved)


def basket_paths(quantiles, forwards, matrix, n_pairs: int, seed: int = SEED) -> np.ndarray:
    """Antithetic pairs of S_i(T) / S_i(0): the marginals of the surfaces joined by a Gaussian copula."""
    from optlab.structured.multiasset import correlation_root

    z = np.random.default_rng(seed).standard_normal((n_pairs, len(quantiles)))
    u = ndtr(np.concatenate([z, -z]) @ correlation_root(np.asarray(matrix, dtype=float)).T)
    return np.column_stack([f * q(u[:, i]) for i, (f, q) in enumerate(zip(forwards, quantiles))])


def spread_payoff(kind: str, strike: float, x) -> np.ndarray:
    """Option on the equally weighted basket less the average of the same option on each member, path by path."""
    if kind not in CP:
        raise ValueError(f"unknown spread {kind!r}, expected cvc or pvp")
    phi = 1.0 if kind == "cvc" else -1.0
    return np.maximum(phi * (x.mean(axis=1) - strike), 0.0) - np.maximum(phi * (x - strike), 0.0).mean(axis=1)


def member_vols(surfaces, forwards, strike: float, T: float) -> np.ndarray:
    """Each member's implied volatility at the one strike of the structure, whatever its own moneyness to the forward."""
    k = np.log(strike / np.asarray(forwards, dtype=float))
    return np.array([float(np.sqrt(np.asarray(s.w(np.array([ki]), T))[0] / T)) for s, ki in zip(surfaces, k)])


def average_correlation(matrix, forwards, vols) -> float:
    """The one correlation that a basket's variance sees: pairs weighted by a_i a_j s_i s_j, with a_i the share of a member."""
    a = np.asarray(forwards, dtype=float) / np.sum(forwards) * np.asarray(vols, dtype=float)
    weights = np.outer(a, a)
    np.fill_diagonal(weights, 0.0)
    return float((weights * np.asarray(matrix, dtype=float)).sum() / weights.sum())


def read_correlation(basket_price: float, kind: str, strike: float, T: float, forwards, vols) -> tuple[float, float]:
    """(correlation, basket volatility) read off an undiscounted basket option. No model: the basket's implied volatility,
    then rho = (s_B^2 - sum a_i^2 s_i^2) / ((sum a_i s_i)^2 - sum a_i^2 s_i^2). The basket is taken as lognormal, and each
    member's volatility is the one at this strike: that is what would be applied to a real quote."""
    f = np.asarray(forwards, dtype=float)
    s_b = float(implied_vol(basket_price, f.mean(), strike, T, 1.0, CP[kind]))
    a = f / f.sum() * np.asarray(vols, dtype=float)
    diagonal = float((a * a).sum())
    return (s_b * s_b - diagonal) / (float(a.sum()) ** 2 - diagonal), s_b


def spread_quote(surfaces, forwards, x, matrix, kind: str, strike: float, T: float) -> dict:
    """One line, in % of notional and undiscounted. `price` is long the basket and short the members, so never positive.
    The spread comes from the paths, the members from the surfaces in closed form, and the basket from the two."""
    half = len(x) // 2
    pairs = spread_payoff(kind, strike, x)
    pairs = 0.5 * (pairs[:half] + pairs[half:])
    vols = member_vols(surfaces, forwards, strike, T)
    members = float(np.mean(black_price(np.asarray(forwards, dtype=float), strike, T, vols, 1.0, CP[kind])))
    spread = float(pairs.mean())
    implied, basket_vol = read_correlation(members + spread, kind, strike, T, forwards, vols)
    return {
        "kind": kind,
        "strike": strike,
        "tenor": T,
        "price": 100 * spread,
        "price_se": 100 * float(pairs.std(ddof=1)) / np.sqrt(half),
        "basket_price": 100 * (members + spread),
        "members_price": 100 * members,
        "basket_vol": basket_vol,
        "rho": average_correlation(matrix, forwards, vols),
        "implied_rho": implied,
    }


def basket_rows(
    day,
    names,
    surfaces,
    forward,
    base_matrix,
    tenors=(0.25, 0.5, 1.0),
    strikes=STRIKES,
    n_pairs: int = 100_000,
    skew: CorrelationSkew = CorrelationSkew(),
    seed: int = SEED,
) -> list[dict]:
    """Every line of one basket on one day. `forward(T)` gives S_i-forward over spot per member. Simulated once per strike, so
    a CvC and a PvP on one strike share their paths and differ by nothing."""
    label = "+".join(names)
    rows = []
    for T in tenors:
        f = np.asarray(forward(T), dtype=float)
        quantiles = [forward_quantile(s, T) for s in surfaces]
        for strike in sorted({k for ks in strikes.values() for k in ks}):
            matrix = skewed_matrix(base_matrix, strike, skew)
            x = basket_paths(quantiles, f, matrix, n_pairs, seed)
            for kind, ks in strikes.items():
                if strike in ks:
                    rows.append(
                        {"basket": label, "obs_date": day, **spread_quote(surfaces, f, x, matrix, kind, strike, T)}
                    )
    return rows


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
