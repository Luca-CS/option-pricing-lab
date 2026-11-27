"""The correlation skew's shape from the lake's closes: how far the names' correlation moves when their basket has
fallen or risen, as the lambda shift of `structured.localcorr.LambdaShift` reads it.

Returns are 3-day overlapping log returns, standardised by an ex-ante EWMA volatility (weight 0.06, lagged one day),
so that their correlation estimates the drivers'. The state, known at the start of each window, is the equal-weight
basket's 63-day trailing return in standard deviations (its trailing 252-day volatility). Per bin of the state the mean
pairwise correlation rho_b gives lambda_b against the central bin's rho_0: (rho_b - rho_0)/(1 - rho_0) above it,
(rho_b - rho_0)/rho_0 below it. Bands are a block bootstrap (21-day blocks). Each side's cap and scale are fitted on
its two bins at their mean state, weighted by the bootstrap variances, within the model's bounds. Only rows dated on
or before the day are read. Under P, so the correlation risk premium is not in the shape (the level's is in the base's
matrix, through COR).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from optlab.marketdata.lake import Lake, sql_str

HORIZON = 3
STATE = 63
VOL_WINDOW = 252
EWMA = 0.06
EDGES = (-np.inf, -1.0, -0.25, 0.25, 1.0, np.inf)
LAMBDA_MAX = 0.95
SCALES = np.linspace(0.2, 3.0, 57)


def _closes(lake: Lake, names, day) -> pd.DataFrame:
    rows = lake.read(
        "prices_eod",
        where=f"instrument IN ({', '.join(sql_str(n) for n in names)}) AND obs_date <= {sql_str(str(day))}",
        columns="instrument, obs_date, close",
    )
    wide = rows.pivot_table(index="obs_date", columns="instrument", values="close").sort_index().dropna()
    missing = [n for n in names if n not in wide.columns]
    if missing:
        raise KeyError(f"no closes of {', '.join(missing)} on or before {day}")
    return wide[list(names)]


def _mean_corr(z: np.ndarray) -> float:
    c = np.corrcoef(z.T)
    k = c.shape[0]
    return float(c[np.triu_indices(k, 1)].mean())


def _lambda(rho: float, rho0: float) -> float:
    return (rho - rho0) / (1 - rho0) if rho >= rho0 else (rho - rho0) / rho0


def _fit_side(u: np.ndarray, lam: np.ndarray, var: np.ndarray) -> tuple[float, float]:
    """(cap, scale) of cap (1 - exp(-|u|/scale)) on the side's bins, least squares weighted by 1/var."""
    w = 1.0 / np.maximum(var, 1e-6)
    best = (0.0, 1.0, np.inf)
    for s in SCALES:
        g = 1 - np.exp(-np.abs(u) / s)
        cap = float(np.clip(np.sum(w * g * lam) / max(np.sum(w * g * g), 1e-12), 0.0, LAMBDA_MAX))
        err = float(np.sum(w * (lam - cap * g) ** 2))
        if err < best[2]:
            best = (cap, float(s), err)
    return best[0], best[1]


def correlation_shape(lake: Lake, names, day, n_boot: int = 200, seed: int = 2026) -> dict:
    """The lambda shift's shape of these names on a day: lam_dn, s_dn, lam_up, s_up, the bins and their bands."""
    names = tuple(names)
    if len(names) < 2:
        raise ValueError("a correlation shape needs two names or more")
    closes = _closes(lake, names, day)
    logp = pd.DataFrame(np.log(closes.to_numpy(dtype=float)), index=closes.index, columns=closes.columns)
    daily = logp.diff()
    vol = ((daily**2).ewm(alpha=EWMA, adjust=False).mean() ** 0.5).shift(1) * np.sqrt(HORIZON)
    ret = (logp.shift(-HORIZON) - logp) / vol  # the window after the state's date, standardised ex ante
    basket = pd.Series(np.log(np.exp((logp - logp.iloc[0]).to_numpy()).mean(axis=1)), index=logp.index)
    bvol = basket.diff().rolling(VOL_WINDOW).std() * np.sqrt(STATE)
    state = (basket - basket.shift(STATE)) / bvol
    frame = pd.concat([ret.add_prefix("r_"), state.rename("u")], axis=1).dropna()
    if len(frame) < 2 * VOL_WINDOW:
        raise ValueError(f"{len(frame)} usable days of {', '.join(names)} before {day}: too few for a shape")
    z, u = frame[[f"r_{n}" for n in names]].to_numpy(), frame["u"].to_numpy()
    bins = np.digitize(u, EDGES[1:-1])  # 0..4, the centre is 2

    def lambdas(rows) -> np.ndarray:
        zz, bb = z[rows], bins[rows]
        rho = [_mean_corr(zz[bb == b]) if np.sum(bb == b) > 10 else np.nan for b in range(5)]
        return np.array([_lambda(r, rho[2]) for r in rho])

    point = lambdas(np.arange(len(u)))
    rng = np.random.default_rng(seed)
    block, n = 21, len(u)
    starts = np.arange(0, n - block + 1)
    boots = []
    for _ in range(n_boot):
        pick = rng.choice(starts, size=n // block + 1)
        rows = (pick[:, None] + np.arange(block)[None, :]).ravel()[:n]
        boots.append(lambdas(rows))
    boots = np.array(boots)
    band = np.nanpercentile(boots, [5, 95], axis=0)
    var = np.nanvar(boots, axis=0)
    mean_u = np.array([u[bins == b].mean() if np.any(bins == b) else np.nan for b in range(5)])
    lam_dn, s_dn = _fit_side(mean_u[:2], point[:2], var[:2])
    lam_up, s_up = _fit_side(mean_u[3:], -point[3:], var[3:])
    return {
        "names": list(names),
        "lam_dn": lam_dn,
        "s_dn": s_dn,
        "lam_up": lam_up,
        "s_up": s_up,
        "bins": [
            {
                "u": float(mean_u[b]),
                "lambda": float(point[b]),
                "band": [float(band[0][b]), float(band[1][b])],
                "days": int(np.sum(bins == b)),
            }
            for b in range(5)
        ],
        "from": pd.Timestamp(frame.index[0]).date().isoformat(),
        "to": pd.Timestamp(frame.index[-1]).date().isoformat(),
        "origin": "derived",
        "method": "conditional realised correlation of standardised 3-day returns by the basket's 63-day state",
    }
