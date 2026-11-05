"""Correlation: realised from prices, implied through the Cboe COR indices, and pseudo CvC/PvP quotes.

Realised: closes of US and European names are hours apart, so pairs across zones use overlapping multi-day returns.

Implied: COR indices quote an average implied correlation of large SPX members. Its premium over the realised average
of the universe's US shares gives lambda, applied to any pair as rho' = rho + lambda (1 - rho). With lambda in [0, 1]
this is a mix of the matrix with the all-ones matrix, so a positive semi-definite matrix stays so.

CvC and PvP are broker quotes with no free source, so these are pseudo-quotes (origin = synthetic), never to be read as
broker prices. Definitions used here, on S_i(T) / F_i(T): CvC(m) = max(min(X1, X2) - m, 0), paying when both names end
above m; PvP(m) = max(m - max(X1, X2), 0), paying when both end below m. Marginals come from the two surfaces, joined by a
Gaussian copula with a correlation rho(m) that is an assumption, higher in the joint down tail. They give the Lab and the
worst-ofs a correlation skew to fit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.special import ndtr

from ..lake import Lake, new_run_id, sql_str
from ..universe import instrument

WINDOWS = (60, 120, 250)
CROSS_ZONE_HORIZON = 3  # days of overlapping returns between places with different closing hours
TENOR_WINDOW = {"COR1M": 21, "COR3M": 63, "COR6M": 126, "COR1Y": 252}


def closes_wide(lake: Lake, names, day=None, as_of=None) -> pd.DataFrame:
    quoted = ", ".join(sql_str(n) for n in names)
    where = f"instrument IN ({quoted})" + (f" AND obs_date <= DATE '{pd.Timestamp(day).date()}'" if day is not None else "")
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
            series = rolling_pair(closes.iloc[-(window + CROSS_ZONE_HORIZON + 10):], names[i], names[j], window)
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
        for b in names[i + 1:]:
            pairs.append(rolling_pair(closes, a, b, window))
    avg = pd.concat(pairs, axis=1).mean(axis=1)
    both = pd.concat({"imp": implied, "avg": avg}, axis=1).dropna()
    return ((both["imp"] - both["avg"]) / (1 - both["avg"])).clip(0.0, 1.0)


def build_realized(lake: Lake, names) -> list[dict]:
    closes = closes_wide(lake, names)
    run = new_run_id("derive_correl")
    rows = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for w in WINDOWS:
                s = rolling_pair(closes, a, b, w)
                rows.append(pd.DataFrame({"name_a": a, "name_b": b, "window": w, "obs_date": s.index, "correlation": s.to_numpy()}))
    return [lake.ingest("correl_realized", pd.concat(rows, ignore_index=True), "prices_eod", "derived", run)]


def build_premium(lake: Lake, us_shares=("AAPL", "AMZN", "GOOGL", "GS", "IBM")) -> list[dict]:
    closes = closes_wide(lake, us_shares)
    run = new_run_id("derive_premium")
    out = []
    for name, window in TENOR_WINDOW.items():
        cor = lake.read("correl_implied", where=f"index_name = '{name}' AND source = 'cboe'", columns="obs_date, tenor, correlation")
        if cor.empty:
            continue
        implied = pd.Series(cor["correlation"].to_numpy(), index=pd.to_datetime(cor["obs_date"])).sort_index()
        lam = premium_series(implied, closes, list(us_shares), window)
        rows = pd.DataFrame({"index_name": f"LAMBDA.{name}", "tenor": float(cor["tenor"].iloc[0]), "obs_date": lam.index,
                             "correlation": lam.to_numpy()})
        out.append(lake.ingest("correl_implied", rows, "cor_vs_realised", "derived", run))
    return out


def premium_at(lake: Lake, day, index_name: str = "COR3M", as_of=None) -> float:
    rows = lake.read("correl_implied", where=f"index_name = 'LAMBDA.{index_name}' AND obs_date <= DATE '{pd.Timestamp(day).date()}'",
                     columns="obs_date, correlation", order="obs_date DESC LIMIT 1", as_of=as_of)
    return float(rows["correlation"].iloc[0]) if len(rows) else 0.0


def correlation_at(lake: Lake, names, day, window: int = 120, implied: bool = True, as_of=None) -> tuple[np.ndarray, dict]:
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


def pair_payoff(kind: str, m: float):
    if kind == "cvc":
        return lambda a, b: np.maximum(np.minimum(a, b) - m, 0.0)
    if kind == "pvp":
        return lambda a, b: np.maximum(m - np.maximum(a, b), 0.0)
    raise ValueError(f"unknown two-asset kind {kind!r}, expected cvc or pvp")


@dataclass(frozen=True)
class CorrelationSkew:
    """rho(m) assumed for the pseudo-quotes: more correlated in the joint down tail, less in the joint up tail."""
    atm_down: float = 0.05  # PvP above the base correlation at m = 1
    slope_down: float = 0.5
    slope_up: float = 0.25
    atm_up: float = -0.05  # CvC below it at m = 1

    def rho(self, kind: str, m: float, base: float) -> float:
        if kind == "pvp":
            value = base + self.atm_down + self.slope_down * max(1.0 - m, 0.0)
        else:
            value = base + self.atm_up - self.slope_up * max(m - 1.0, 0.0)
        return float(np.clip(value, -0.95, 0.99))


def skewed_matrix(matrix, m: float, kind: str = "pvp", skew: CorrelationSkew = CorrelationSkew()) -> np.ndarray:
    """A constant-correlation matrix for a product living around moneyness m: every pair moved along the assumed
    rho(m), then back to a valid matrix. A worst-of with a low barrier lives in the joint down tail, so it takes the PvP
    side. This is how the one correlation of `BasketContext` carries a skew until the simulation has a local correlation."""
    m_ = np.array(matrix, dtype=float)
    n = len(m_)
    for i in range(n):
        for j in range(i + 1, n):
            m_[i, j] = m_[j, i] = skew.rho(kind, m, float(matrix[i, j]))
    return nearest_correlation(m_)


def implied_pair_rho(price: float, kind: str, m: float, q_a, q_b, n: int = 200) -> float:
    """The constant copula correlation that gives this undiscounted price (the Lab fits these). Both kinds increase
    with the correlation."""
    payoff = pair_payoff(kind, m)
    gap = lambda r: copula_expectation(payoff, q_a, q_b, r, n) - price
    return float(brentq(gap, -0.95, 0.995, xtol=1e-5))


def pair_rows(day, a: str, b: str, surf_a, surf_b, base_rho: float, tenors=(0.25, 0.5, 1.0),
              moneyness=(0.8, 0.9, 1.0, 1.1, 1.2), skew: CorrelationSkew = CorrelationSkew()) -> list[dict]:
    """Prices in % of the forward, undiscounted; price_se is the change when the quadrature is refined, a numerical
    error with no sampling in it."""
    rows = []
    for T in tenors:
        qa, qb = forward_quantile(surf_a, T), forward_quantile(surf_b, T)
        for kind in ("cvc", "pvp"):
            for m in moneyness:
                rho = skew.rho(kind, m, base_rho)
                payoff = pair_payoff(kind, m)
                coarse = copula_expectation(payoff, qa, qb, rho, 100)
                fine = copula_expectation(payoff, qa, qb, rho, 200)
                rows.append({"name_a": a, "name_b": b, "kind": kind, "tenor": T, "moneyness": m, "obs_date": day,
                             "price": 100 * fine, "price_se": 100 * abs(fine - coarse), "rho": rho, "implied_rho": rho})
    return rows


def build_pair_quotes(lake: Lake, pairs, days) -> list[dict]:
    from .surfaces import surface_at
    rows = []
    for day in days:
        for a, b in pairs:
            base, _ = correlation_at(lake, [a, b], day)
            sa, sb = surface_at(lake, a, day), surface_at(lake, b, day)
            rows += pair_rows(pd.Timestamp(day).date(), a, b, sa.surface, sb.surface, float(base[0, 1]))
    return [lake.ingest("pair_quotes", pd.DataFrame(rows), "copula_on_surfaces", "synthetic", new_run_id("derive_pairs"))]
