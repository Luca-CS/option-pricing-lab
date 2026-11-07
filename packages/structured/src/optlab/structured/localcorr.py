"""Correlation skew: a correlation that rises when the basket falls, with the marginals of every name unchanged.

Each step, the spots are driven by Z_i = sqrt(w) Z_0 + sqrt(1 - w) (L_low e)_i, with Z_0 and e independent of each other and of the past, and w depends on the past only.
Each Z_i is then a standard normal given the past, whatever w is, so each name keeps its one-dimensional law: that is the whole point of the form, and a test checks it.
On each path the correlation is a mix of a valid matrix with the matrix of ones, so it is valid too, as in `derive.correl.apply_premium`. The form is the one of
Langnau (arXiv:0909.3441) and Reghai: the correlation moves towards 1 by a weight that depends on the state. Guyon (Calibration of local correlation models to basket
smiles, Journal of Computational Finance) shows a whole family of such models fits a basket smile, so the shape below is a choice, not the unique answer.

`LocalCorrelation` has w depend on the level of the basket at the start of the step, in standard deviations of the basket:

    w(t, y) = clip(w0 + level + down max(-u, 0) - up max(u, 0), 0, 1),     u = y / (sigma sqrt(max(t, 1 month)))

with y = ln(mean S_i / F_i). L_low comes from the measured matrix C with w0 taken out, so that w = w0 gives C back. `StochasticMix` is the stochastic alternative: w follows
a Jacobi process with its own driver, correlated with the market factor, and is kept for comparison (reports/pricing.md).

Only pseudo-quotes of CvC and PvP exist (computed below, stored by `marketdata.derive.correl`, origin synthetic), where the skew is an assumption: a provision calibrated on them reflects that assumption and
says so on the screen.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from optlab.core.blackscholes import black_price, implied_vol
from optlab.core.correlation import nearest_correlation
from scipy.special import ndtr

W0 = 0.3  # the weight at the money: there is room on both sides of the measured correlation
MONTH = 1 / 12
ORIGIN = "synthetic"
STRIKES = {"cvc": (1.0, 1.1), "pvp": (1.0, 0.9)}  # pseudo-quotes, in % of spot, where they trade
CP = {"cvc": "call", "pvp": "put"}
SEED = 2026
NOTE = "Calibrated on pseudo-quotes of CvC and PvP, where the correlation skew is an assumption: no broker level was available"


def low_matrix(corr, w0: float) -> np.ndarray:
    """The matrix that gives `corr` once mixed with the matrix of ones at w0, repaired if taking w0 out leaves it not semi-definite."""
    c = np.asarray(corr, dtype=float)
    low = (c - w0) / (1.0 - w0)
    np.fill_diagonal(low, 1.0)
    return nearest_correlation(np.clip(low, -0.999, 0.999))


def aligned_factors(corr, root, w0: float) -> tuple:
    """(factors of the low matrix with the signs of `root`, sign of the market factor, angle). With two independent normals M (the market factor of `root`) and N,
    Z_0 = cos a M + sin a N and e_top = sin a M - cos a N are independent normals, and the angle takes N out of the spots at w0: a price then moves against the constant
    matrix's on the same draws only where the weight has moved. Independence is all the marginals need, so the angle changes no law."""
    from optlab.structured.multiasset import correlation_root

    low = correlation_root(low_matrix(corr, w0)).copy()
    for j in range(low.shape[1] - 1):  # the other factors have the signs of `root`
        if np.dot(low[:, j], root[:, j]) < 0:
            low[:, j] = -low[:, j]
    if low[:, -1].mean() < 0:  # the top factor loads positively, and so does the market factor once signed
        low[:, -1] = -low[:, -1]
    sign = 1.0 if root[:, -1].mean() >= 0 else -1.0
    return low, sign, float(np.arctan2(np.sqrt(1.0 - w0) * low[:, -1].mean(), np.sqrt(w0)))


@dataclass(frozen=True)
class LocalCorrelation:
    sigma: float  # at-the-money volatility of the basket, to read its level in standard deviations
    level: float = 0.0
    down: float = 0.0
    up: float = 0.0
    w0: float = W0

    def start(self, n: int):
        return None

    def weight(self, t: float, y, state):
        u = y / (self.sigma * np.sqrt(max(t, MONTH)))
        return np.clip(self.w0 + self.level + self.down * np.maximum(-u, 0.0) - self.up * np.maximum(u, 0.0), 0.0, 1.0)

    def advance(self, state, dt: float, market, rng_normal):
        return state

    def bumped(self, slope: float) -> "LocalCorrelation":
        """The slope moved by `slope` on each side: a steeper correlation skew when positive."""
        return replace(self, down=max(self.down + slope, 0.0), up=max(self.up + slope, 0.0))


@dataclass(frozen=True)
class StochasticMix:
    """dw = kappa (theta - w) dt + nu sqrt(w (1 - w)) dB, with B correlated `rho` with the market factor: the weight rises when the market factor falls for rho < 0."""

    theta: float = W0
    nu: float = 0.5
    rho: float = -0.5
    kappa: float = 2.0
    w0: float = W0

    def start(self, n: int):
        return np.full(n, self.w0)

    def weight(self, t: float, y, state):
        return state

    def advance(self, state, dt: float, market, rng_normal):
        z = self.rho * market + np.sqrt(1 - self.rho**2) * rng_normal
        w = (
            state
            + self.kappa * (self.theta - state) * dt
            + self.nu * np.sqrt(np.maximum(state * (1 - state), 0.0) * dt) * z
        )
        return np.clip(w, 0.0, 1.0)

    def bumped(self, slope: float) -> "StochasticMix":
        return replace(self, rho=float(np.clip(self.rho - slope, -0.99, 0.99)))


def basket_sigma(ctx, names, T: float = 1.0) -> float:
    """ATM volatility of the equally weighted basket, from each name's and the measured correlation."""
    vols = np.array([ctx.atm_vol(n, T) for n in names])
    idx = [ctx.names.index(n) for n in names]
    c = np.asarray(ctx.corr)[np.ix_(idx, idx)]
    return float(np.sqrt(vols @ c @ vols) / len(names))


def targets(ctx, names, T: float = 1.0, skew=None) -> list:
    """The pseudo-quotes of one tenor: PvP at 90 %, CvC at 100 % and 110 %, in % of notional and undiscounted."""
    surfaces = [ctx.vols[n] for n in names]
    idx = [ctx.names.index(n) for n in names]
    matrix = np.asarray(ctx.corr)[np.ix_(idx, idx)]
    forward = lambda t: [float(ctx.markets[n].F(t)) / float(ctx.markets[n].spot) for n in names]  # noqa: E731
    rows = basket_rows(
        ctx.valuation, list(names), surfaces, forward, matrix, tenors=(T,), skew=skew or CorrelationSkew()
    )
    keep = [r for r in rows if (r["kind"], r["strike"]) in (("pvp", 0.9), ("cvc", 1.0), ("cvc", 1.1))]
    return sorted(keep, key=lambda r: r["strike"])


def model_quotes(ctx, names, mixer, wanted, T: float, n_paths: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """(prices in %, standard errors) of the same lines under a mixer, by local vol per name on a coarse grid."""
    from optlab.structured.multiasset import simulate_basket

    basket = ctx.model("lv", T, names, mixer=mixer)
    paths = simulate_basket(basket, [T], n_paths, seed, dt_max=1 / 50)
    x = np.column_stack([paths[n][:, 0] / float(ctx.markets[n].spot) for n in names])
    half = len(x) // 2
    out, se = [], []
    for r in wanted:
        pay = 100.0 * spread_payoff(r["kind"], r["strike"], x)
        pay = 0.5 * (pay[:half] + pay[half:])
        out.append(float(pay.mean()))
        se.append(float(pay.std(ddof=1) / np.sqrt(half)))
    return np.array(out), np.array(se)


def _fit(ctx, names, wanted, make, start, bounds, T, n_paths, seed, steps: int = 6) -> tuple:
    """Gauss-Newton on common random numbers, with finite differences of the model's prices."""
    goal = np.array([r["price"] for r in wanted])
    p = np.array(start, dtype=float)
    lo, hi = np.array(bounds[0]), np.array(bounds[1])
    h = 0.02
    best, best_err = p.copy(), np.inf
    for _ in range(steps):
        f, noise = model_quotes(ctx, names, make(p), wanted, T, n_paths, seed)
        r = f - goal
        err = float(np.sqrt(np.mean(r * r)))
        if err < best_err:
            best, best_err = p.copy(), err
        if err < float(noise.mean()):  # within the noise of the prices: more steps would fit the noise
            break
        J = np.empty((len(goal), len(p)))
        for j in range(len(p)):
            q = p.copy()
            q[j] = q[j] + h if q[j] + h <= hi[j] else q[j] - h
            g, _ = model_quotes(ctx, names, make(q), wanted, T, n_paths, seed)
            J[:, j] = (g - f) / (q[j] - p[j])
        step = np.linalg.lstsq(J, -r, rcond=1e-3)[0]
        p = np.clip(p + 0.8 * step, lo, hi)
    return best, best_err


def calibrate(
    ctx, names, T: float = 1.0, n_paths: int = 40_000, seed: int = 2026, form: str = "local", skew=None
) -> dict:
    """The mixer fitted to the pseudo-quotes of a tenor, with the fit: {"mixer", "lines", "rmse", "origin", "note"}."""
    names = tuple(names)
    wanted = targets(ctx, names, T, skew)
    sigma = basket_sigma(ctx, names, T)
    if form == "local":
        make = lambda p: LocalCorrelation(sigma, p[0], p[1], p[2])  # noqa: E731
        start, bounds = (0.0, 0.1, 0.1), ((-W0, 0.0, 0.0), (1.0 - W0, 1.5, 1.5))
    elif form == "stochastic":
        make = lambda p: StochasticMix(p[0], p[1], p[2])  # noqa: E731
        start, bounds = (W0, 0.5, -0.5), ((0.01, 0.05, -0.95), (0.99, 3.0, 0.95))
    else:
        raise ValueError(f"unknown form {form!r}")
    p, rmse = _fit(ctx, names, wanted, make, start, bounds, T, n_paths, seed)
    mixer = make(p)
    fitted, se = model_quotes(
        ctx, names, mixer, wanted, T, n_paths, seed + 1
    )  # on other paths: a fit is judged out of its own noise
    lines = [
        {
            "kind": r["kind"],
            "strike": r["strike"],
            "tenor": T,
            "pseudo_quote": r["price"],
            "model": float(f),
            "se": float(e),
            "assumed_rho": r["rho"],
        }
        for r, f, e in zip(wanted, fitted, se)
    ]
    return {"mixer": mixer, "lines": lines, "rmse": rmse, "origin": ORIGIN, "note": NOTE, "form": form}


# --- The pseudo-quotes of CvC and PvP: the marginals of the surfaces joined by a Gaussian copula ---
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
