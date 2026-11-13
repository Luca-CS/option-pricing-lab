"""Historical VaR by full revaluation: each day of history becomes a shock on today's market, and the whole book is revalued.

Factors per day, from the lake (`history`): per name the log return of the spot, the change of the at-the-money vol at each
tenor and of the 90-110 % skew at one year (on the reconstructed surface layer, origin "reconstructed"), the change of the
dividend yield per tenor (zero rate less implied carry); per currency the change of the zero rate per tenor; per pair of names
the change of the 120-day realised correlation. A scenario applies day d's changes to today's market: relative for the spots,
absolute for vols, skew, rates, dividends and correlation, under sticky moneyness (the observed vols already hold the
spot-vol relation of the day).

- 1-day horizon over the last `days` business days (500, two years).
- 10-day horizon on overlapping 10-day changes over the same window: not sqrt(10) times the 1-day VaR.
- Stressed VaR: the one-year window of the largest 1-day VaR in the lake's history. It is searched with the delta-gamma-vega
  approximation of the book's P&L over every date (a full revaluation of 2,000 dates would take an hour), frozen and stored,
  and then revalued in full like the others.
- Outputs: VaR 99 % (a loss, positive), ES 97.5 %, the P&L distribution, contributions per trade (the trade's P&L on the VaR
  scenario, and its mean over the ES tail: Euler shares that add up to the book's ES) and per factor (the VaR scenario's day
  replayed one factor at a time; the rest is the interaction).
- Every scenario runs on the base's seed; the standard error of the VaR scenario's P&L is reported against the VaR.
- Check: the delta-gamma-vega approximation of every scenario, from the Greeks of the day, and its VaR.

Revaluations run in processes (`workers`), each on the engine of the settings: the GPU simulates, the CPU builds the local
volatility tables and evaluates the scripts, which is where the time goes.
"""

from __future__ import annotations

import dataclasses
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from optlab.core import progress
from optlab.risk import grid
from optlab.risk.curves import CurveMove
from optlab.risk.engine import RiskSettings, run_group_safe
from optlab.risk.shocks import RiskEnv, Shock, VolMove, context_currency
from optlab.structured.multiasset import BasketContext

LEVEL, ES_LEVEL = 0.99, 0.975
FACTORS = ("spot", "vol", "skew", "dividend", "rates", "corr")


# --- factor history ---


def group_names(group) -> list[str]:
    items = group.book._prepare(group.ctx)
    used = sorted({n for it in items for n in it["underlyings"]})
    return [n for n in group.ctx.names if n in used] if isinstance(group.ctx, BasketContext) else used


def factor_levels(lake, names, currencies, start, end, layer: str = "reconstructed") -> tuple[pd.DataFrame, dict]:
    """Levels of every factor by date (rows where every spot and surface is known), and their origins."""
    from optlab.risk.history import carry, closes, realised_correlation, vol_factors, zero_rates

    start_wide = (pd.Timestamp(start) - pd.tseries.offsets.BDay(130)).date()  # the correlation window needs its history
    spots = closes(lake, names, start_wide, end)
    cols, origins = {}, {}
    for n in names:
        cols[f"spot:{n}"] = spots[n]
        v = vol_factors(lake, n, start_wide, end, layer)
        for lab in grid.TENOR_LABELS:
            cols[f"atm:{n}:{lab}"] = v[f"atm_{lab}"]
        cols[f"skew:{n}"] = v["skew"]
        origins[f"vol:{n}"] = sorted(set(v["origin"]))
    for c in currencies:
        z = zero_rates(lake, c, start_wide, end)
        for lab in grid.TENOR_LABELS:
            cols[f"rate:{c}:{lab}"] = z[f"r_{lab}"]
        origins[f"rates:{c}"] = ["derived"]
    for n in names:
        try:
            cy = carry(lake, n, start_wide, end)
        except Exception:  # noqa: BLE001 - a name without carry keeps its dividends
            continue
        if cy.empty:
            continue
        c = currencies[0] if currencies else "USD"
        for lab in grid.TENOR_LABELS:
            r = cols.get(f"rate:{c}:{lab}")
            if r is not None:
                cols[f"div:{n}:{lab}"] = r - cy[f"c_{lab}"]
        origins[f"dividend:{n}"] = sorted(set(cy["method"].dropna()))
    for pair, series in realised_correlation(spots[names], 120).items():
        cols[f"corr:{pair}"] = series
        origins[f"corr:{pair}"] = ["derived (realised, 120 days)"]
    df = pd.DataFrame(cols).sort_index()
    need = [c for c in df.columns if c.startswith(("spot:", "atm:"))]
    df = df.dropna(subset=need).ffill()
    return df[df.index >= pd.Timestamp(start).date()], origins


def changes(levels: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """Changes over `horizon` business days (overlapping): log returns for spots, differences otherwise. Indexed by the end
    date of each change."""
    out = {}
    for c in levels.columns:
        x = levels[c].astype(float)
        out[c] = np.log(x).diff(horizon) if c.startswith("spot:") else x.diff(horizon)
    return pd.DataFrame(out).iloc[horizon:].fillna(0.0)


def shock_of(row: pd.Series, names, currency: str, factors=FACTORS) -> Shock:
    """The shock of one day's changes (only the listed factors)."""
    spot, vol, div, rates, corr = {}, {}, {}, {}, {}
    for n in names:
        if "spot" in factors:
            spot[n] = float(np.expm1(row.get(f"spot:{n}", 0.0)))
        term = (
            {j: float(row.get(f"atm:{n}:{lab}", 0.0)) for j, lab in enumerate(grid.TENOR_LABELS)}
            if "vol" in factors
            else {}
        )
        skew = float(row.get(f"skew:{n}", 0.0)) if "skew" in factors else 0.0
        if any(term.values()) or skew:
            vol[n] = VolMove(skew=skew, term={j: v for j, v in term.items() if v})
        if "dividend" in factors and f"div:{n}:1y" in row:
            div[n] = CurveMove(
                0.0, {j: float(row.get(f"div:{n}:{lab}", 0.0)) / 1e-4 for j, lab in enumerate(grid.TENOR_LABELS)}
            )
    if "rates" in factors and f"rate:{currency}:1y" in row:
        rates[currency] = CurveMove(
            0.0, {j: float(row.get(f"rate:{currency}:{lab}", 0.0)) / 1e-4 for j, lab in enumerate(grid.TENOR_LABELS)}
        )
    if "corr" in factors:
        for a_i, a in enumerate(names):
            for b in names[a_i + 1 :]:
                key = "|".join(sorted((a, b)))
                v = float(row.get(f"corr:{key}", 0.0))
                if v:
                    corr[f"{a}|{b}"] = v
    return Shock(spot=spot, vol=vol, dividend=div, rates=rates, corr_pairs=corr)


# --- revaluation of many scenarios ---

_GROUPS = None


def _init(groups, env):
    global _GROUPS
    _GROUPS = (groups, env)


def _chunk(job):
    gi, shocks, settings = job
    groups, env = _GROUPS
    r = run_group_safe(groups[gi], shocks, None, settings, env)
    return (
        gi,
        {k: (r.pnl[k][0], r.pnl[k][1], r.pnl[k][2], r.pnl[k][3]) for k in shocks},
        r.values["base"],
        r.warnings,
        r.seconds,
    )


def worker_settings(settings: RiskSettings, workers: int) -> RiskSettings:
    """Settings for revaluations spread over processes: a spawned process does not inherit `engines.set_default`, so the
    engine is named; the processes share the cores, so each evaluates on one thread."""
    if workers <= 1:
        return settings
    if settings.engine is None:
        from optlab.core import engines

        settings = dataclasses.replace(settings, engine=engines.default_engine())
    if settings.eval_threads is None:
        settings = dataclasses.replace(settings, eval_threads=1)
    return settings


def revalue_many(
    groups: list,
    shocks: dict,
    settings: RiskSettings,
    env: RiskEnv | None = None,
    workers: int = 1,
    chunk: int = 25,
    log=None,
) -> dict:
    """{"pnl": {shock: [book pnl, se]}, "positions": {shock: {position: pnl}}, "seconds"}: every shock on every group."""
    env = env or RiskEnv()
    t0 = time.perf_counter()
    names = list(shocks)
    settings = worker_settings(settings, workers)
    cost = [len(group_names(g)) for g in groups]  # the groups with most names are the slowest: they go first
    order = sorted(range(len(groups)), key=lambda gi: -cost[gi])
    jobs = [
        (gi, {k: shocks[k] for k in names[i : i + chunk]}, settings)
        for gi in order
        for i in range(0, len(names), chunk)
    ]
    results = []
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(
            max_workers=workers, mp_context=mp.get_context("spawn"), initializer=_init, initargs=(groups, env)
        ) as pool:
            try:
                for k, res in enumerate(pool.map(_chunk, jobs)):
                    results.append(res)
                    if log:
                        log(f"chunk {k + 1}/{len(jobs)} ({time.perf_counter() - t0:.0f} s)")
                    progress.tick(k + 1, len(jobs), "chunks")
            except BaseException:  # a cancelled job: the chunks not started are dropped, not waited for
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    else:
        _init(groups, env)
        for k, job in enumerate(jobs):
            results.append(_chunk(job))
            if log:
                log(f"chunk {k + 1}/{len(jobs)} ({time.perf_counter() - t0:.0f} s)")
            progress.tick(k + 1, len(jobs), "chunks")
    book = {k: [0.0, 0.0] for k in names}
    per_pos: dict = {k: {} for k in names}
    warnings = []
    for gi, pnl, base, warns, _ in results:
        pos_names = [p.name for p in groups[gi].book.positions]
        for k, (pos, pos_se, tot, tot_se) in pnl.items():
            book[k][0] += tot
            book[k][1] += tot_se**2
            for p, v, se in zip(pos_names, pos, pos_se):
                per_pos[k][p] = (v, se)
        warnings += [w for w in warns if w not in warnings]
    for k in names:
        book[k][1] = float(np.sqrt(book[k][1]))
    return {"pnl": book, "positions": per_pos, "seconds": time.perf_counter() - t0, "warnings": warnings}


# --- statistics ---


def var_es(pnl, level: float = LEVEL, es_level: float = ES_LEVEL) -> dict:
    """VaR (the loss at `level`, positive) on the empirical distribution, an actual scenario (numpy's "lower" quantile), and ES
    (the mean loss beyond the `es_level` quantile)."""
    x = np.asarray(pnl, dtype=float)
    q = float(np.quantile(x, 1 - level, method="lower"))
    qe = float(np.quantile(x, 1 - es_level, method="lower"))
    tail = x[x <= qe]
    return {
        "var": -q,
        "es": -float(tail.mean()),
        "n": int(len(x)),
        "var_index": int(np.flatnonzero(x == q)[0]),
        "tail": np.flatnonzero(x <= qe).tolist(),
    }


def kupiec(exceptions: int, n: int, p: float = 1 - LEVEL) -> dict:
    """Kupiec's proportion-of-failures test: LR = -2 ln[(1 - p)^(n - x) p^x / ((1 - x/n)^(n - x) (x/n)^x)], chi-square with
    one degree of freedom under the hypothesis that the VaR is right."""
    from scipy.stats import chi2

    x, n = int(exceptions), int(n)
    if n == 0:
        return {"exceptions": 0, "n": 0, "expected": 0.0, "lr": None, "p_value": None}
    phat = x / n

    def xlogy(a, b):
        return 0.0 if a == 0 else a * np.log(b)

    lr = -2.0 * (xlogy(n - x, 1 - p) + xlogy(x, p) - xlogy(n - x, 1 - phat) - xlogy(x, phat))
    return {"exceptions": x, "n": n, "expected": n * p, "rate": phat, "lr": float(lr), "p_value": float(chi2.sf(lr, 1))}


def taylor_pnl(report, row: pd.Series, names, currency: str) -> float:
    """Delta-gamma-vega approximation of one day's P&L from a Greeks report (`greeks.compute`): delta, gamma, vega per tenor,
    skew vega, vanna, volga, rho and epsilon per tenor, correlation per pair."""
    g = report.book
    v = lambda k: g.get(k, [0.0])[0]  # noqa: E731
    total = 0.0
    for n in names:
        r = float(np.expm1(row.get(f"spot:{n}", 0.0)))
        x = r / 0.01
        total += v(f"delta|{n}|") * x + 0.5 * v(f"gamma|{n}|") * x * x
        dv_par = 0.0
        for lab in grid.TENOR_LABELS:
            dv = float(row.get(f"atm:{n}:{lab}", 0.0)) / 0.01
            total += v(f"vega_tenor|{n}|{lab}") * dv
            dv_par += dv / len(grid.TENOR_LABELS)
        total += v(f"vanna|{n}|") * x * dv_par + 0.5 * v(f"volga|{n}|") * dv_par**2
        total += v(f"skew_vega|{n}|") * float(row.get(f"skew:{n}", 0.0)) / 0.01
        for lab in grid.TENOR_LABELS:
            total += v(f"epsilon_tenor|{n}|{lab}") * float(row.get(f"div:{n}:{lab}", 0.0)) / 1e-4
    for lab in grid.TENOR_LABELS:
        total += v(f"rho_tenor|{currency}|{lab}") * float(row.get(f"rate:{currency}:{lab}", 0.0)) / 1e-4
    for a_i, a in enumerate(names):
        for b in names[a_i + 1 :]:
            total += v(f"corr|{a}~{b}|") * float(row.get(f"corr:{'|'.join(sorted((a, b)))}", 0.0)) / 0.01
    return float(total)


def search_stressed(levels: pd.DataFrame, report, names, currency: str, window: int = 250) -> dict:
    """The one-year window (`window` business days) of largest 1-day VaR under the delta-gamma-vega approximation."""
    ch = changes(levels, 1)
    approx = np.array([taylor_pnl(report, row, names, currency) for _, row in ch.iterrows()])
    best, best_i = -np.inf, 0
    for i in range(0, len(approx) - window + 1, 5):
        v = var_es(approx[i : i + window])["var"]
        if v > best:
            best, best_i = v, i
    days = list(ch.index)
    return {
        "start": days[best_i].isoformat(),
        "end": days[best_i + window - 1].isoformat(),
        "approx_var": float(best),
        "window": window,
        "searched": [days[0].isoformat(), days[-1].isoformat()],
        "method": "delta-gamma-vega search",
    }


@dataclass
class VarResult:
    """One VaR (a horizon on a window): statistics, distribution, contributions, noise and the Taylor check."""

    label: str
    horizon: int
    start: str
    end: str
    dates: list
    pnl: list
    pnl_se: list
    var: float
    es: float
    var_date: str
    var_se: float
    contributions: dict  # {"var": {position: pnl on the VaR day}, "es": {position: mean over the tail}}
    factors: dict  # {factor: pnl of the VaR day's move of that factor alone}
    taylor: dict | None
    seconds: float
    n: int = 0
    origins: dict = field(default_factory=dict)


def _result(
    label, horizon, ch: pd.DataFrame, reval: dict, keys: list, origins: dict, seconds: float, taylor=None
) -> VarResult:
    pnl = np.array([reval["pnl"][k][0] for k in keys])
    se = [reval["pnl"][k][1] for k in keys]
    st = var_es(pnl)
    i = st["var_index"]
    tail = st["tail"]
    positions = sorted(reval["positions"][keys[0]])
    contrib_var = {p: reval["positions"][keys[i]][p][0] for p in positions}
    contrib_es = {p: float(np.mean([reval["positions"][keys[t]][p][0] for t in tail])) for p in positions}
    dates = [d.isoformat() for d in ch.index]
    return VarResult(
        label,
        horizon,
        dates[0],
        dates[-1],
        dates,
        pnl.tolist(),
        se,
        st["var"],
        st["es"],
        dates[i],
        se[i],
        {"var": contrib_var, "es": contrib_es},
        {},
        taylor,
        seconds,
        len(pnl),
        origins,
    )


def run_var(
    groups: list,
    lake,
    day,
    settings: RiskSettings | None = None,
    env: RiskEnv | None = None,
    days: int = 500,
    ten_day: bool = True,
    stressed: dict | None = None,
    greeks_report=None,
    workers: int = 1,
    store=None,
    stressed_window: int = 250,
    log=None,
    factors=FACTORS,
    with_stressed: bool = True,
) -> dict:
    """1-day and 10-day VaR over `days` business days to `day`, and the stressed VaR on `stressed` ({"start", "end"}: searched
    with `greeks_report` and stored when None and a report is given)."""
    t_all = time.perf_counter()
    settings = settings or RiskSettings()
    env = env or RiskEnv()
    day = pd.Timestamp(day).date()
    names = sorted({n for g in groups for n in group_names(g)})
    currencies = sorted({context_currency(g.ctx) for g in groups})
    cur = currencies[0]
    start = (pd.Timestamp(day) - pd.tseries.offsets.BDay(days + 15)).date()
    levels, origins = factor_levels(lake, names, currencies, start, day)
    levels = levels.iloc[-(days + 10 + 1) :]
    out = {
        "day": day.isoformat(),
        "names": names,
        "currency": cur,
        "origins": origins,
        "settings": settings.to_dict(),
        "level": LEVEL,
        "es_level": ES_LEVEL,
        "factors": list(factors),
    }

    horizons = [("1d", changes(levels, 1).iloc[-days:], 1)]
    if ten_day:
        horizons.append(("10d", changes(levels, 10).iloc[-days:], 10))
    if stressed is None and greeks_report is not None and with_stressed:
        key = ",".join(names)
        got = store.get("stressed_window", key) if store is not None else None
        if got is None:
            hist, _ = factor_levels(lake, names, currencies, "2018-01-01", day)
            stressed = search_stressed(hist, greeks_report, names, cur, stressed_window)
            if store is not None:
                stressed["version"] = store.put("stressed_window", key, stressed)
        else:
            stressed = {**got["payload"], "version": got["version"]}
    if stressed is not None:
        lv, _ = factor_levels(lake, names, currencies, stressed["start"], stressed["end"])
        horizons.append(("stressed", changes(lv, 1), 1))
    # every horizon in one pool: one start of the processes, one tail
    t0 = time.perf_counter()
    keys = {label: [f"{label}:{d.isoformat()}" for d in ch.index] for label, ch, _ in horizons}
    shocks = {
        k: shock_of(row, names, cur, factors)
        for label, ch, _ in horizons
        for k, (_, row) in zip(keys[label], ch.iterrows())
    }
    reval = revalue_many(groups, shocks, settings, env, workers, log=log)
    shared = time.perf_counter() - t0
    for label, ch, horizon in horizons:
        t1 = time.perf_counter()
        taylor = None
        if greeks_report is not None:
            approx = np.array([taylor_pnl(greeks_report, row, names, cur) for _, row in ch.iterrows()])
            full = np.array([reval["pnl"][k][0] for k in keys[label]])
            taylor = {
                "var": var_es(approx)["var"],
                "es": var_es(approx)["es"],
                "mean_abs_gap": float(np.mean(np.abs(approx - full))),
                "pnl": approx.tolist(),
            }
        res = _result(label, horizon, ch, reval, keys[label], origins, 0.0, taylor)
        row = ch.loc[pd.Timestamp(res.var_date).date()]  # the VaR day's move one factor at a time
        f_shocks = {f"{label}:factor:{f}": shock_of(row, names, cur, (f,)) for f in factors}
        fr = revalue_many(groups, f_shocks, settings, env, 1)
        res.factors = {f: fr["pnl"][f"{label}:factor:{f}"] for f in factors}
        res.factors["interaction"] = [
            res.pnl[res.dates.index(res.var_date)] - sum(v[0] for v in res.factors.values()),
            None,
        ]
        res.seconds = (
            shared * len(ch) / len(shocks) + time.perf_counter() - t1
        )  # its share of the pool, and its factors
        d = dataclasses.asdict(res)
        if label == "1d":
            out["one_day"] = d
        elif label == "10d":
            out["ten_day"] = {
                **d,
                "note": "overlapping 10-day changes over the same window, not sqrt(10) x the 1-day VaR",
            }
        else:
            out["stressed"] = {**d, "window": stressed}
    out["revaluation_seconds"] = shared
    out["scenarios"] = len(shocks)
    out["seconds"] = time.perf_counter() - t_all
    return out
