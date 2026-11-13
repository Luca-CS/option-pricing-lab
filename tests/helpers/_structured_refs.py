"""Independent references for the structured pricer: Black-Scholes closed forms and a flat SVI surface."""

import functools

import numpy as np
from _paths import SPX_CALIBRATION
from scipy.stats import norm

from optlab.core import BlackScholesModel, Market, SVISurface
from optlab.core.blackscholes import black_price
from optlab.structured.context import PricingContext

S0, R, Q, VOL = 100.0, 0.04, 0.01, 0.2
FLAT = Market.flat(S0, R, Q)


def flat_surface(vol=VOL, maturities=(0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0)):
    """SVI slices with b = 0: total variance vol^2 T at every strike."""
    return SVISurface(maturities, [[vol**2 * T, 0.0, 0.0, 0.0, 0.1] for T in maturities])


def flat_context(valuation="2026-10-27", vol=VOL) -> PricingContext:
    """Black-Scholes world: flat market, flat surface; Heston and SLV taken from the SPX calibration (unused)."""
    import datetime as dt

    spx = PricingContext.from_calibration(SPX_CALIBRATION)
    ctx = PricingContext(FLAT, flat_surface(vol), spx.heston, spx.slv, dt.date.fromisoformat(valuation))
    ctx.model = lambda name, T=1.0: BlackScholesModel(vol) if name in ("bs", "lv") else spx.model(name, T)
    return ctx


def forward(T):
    return S0 * np.exp((R - Q) * T)


def put_fwd(K, T, vol=VOL):
    """Undiscounted E[(K - S_T)+]."""
    return float(black_price(forward(T), K, T, vol, 1.0, "put"))


def call_fwd(K, T, vol=VOL):
    return float(black_price(forward(T), K, T, vol, 1.0, "call"))


def prob_below(K, T, vol=VOL):
    """P(S_T < K), the undiscounted cash-or-nothing digital put."""
    sd = vol * np.sqrt(T)
    d2 = np.log(forward(T) / K) / sd - 0.5 * sd
    return float(norm.cdf(-d2))


def rc_european(N, cpn, pay_times, T, T_pay, K, B, market=FLAT):
    """Reverse convertible with a European PDI: fixed coupons plus N D(T_pay) [1 - E[(K - perf)+ 1{perf < B}]].
    For B <= K: (K S0 - S)+ 1{S < B S0} = (B S0 - S)+ + (K - B) S0 1{S < B S0}."""
    coupons = N * cpn * sum(float(market.D(t)) for t in pay_times)
    loss = (put_fwd(B * S0, T) + (K - B) * S0 * prob_below(B * S0, T)) / S0
    return coupons + N * float(market.D(T_pay)) * (1 - loss)


# --- autocall reference: backward recursion on a log-spot grid with the exact Gaussian kernel ---


def _edges(levels, lo, hi, dx):
    """Cell edges in x = ln(S/S0) containing every barrier level, about dx apart."""
    points = sorted({lo, hi, *(np.log(v) for v in levels if lo < np.log(v) < hi)})
    edges = [np.linspace(a, b, max(2, int(np.ceil((b - a) / dx)) + 1))[:-1] for a, b in zip(points[:-1], points[1:])]
    return np.concatenate([*edges, [hi]])


def autocall_reference(sheet, market=FLAT, vol=VOL, valuation="2026-10-27", dx=0.002, lo=-2.5, hi=1.5):
    """Value (currency, notional 100, S0 = 100) of an autocall or bullet term sheet under Black-Scholes, from the
    term-sheet semantics only (not the generated script). States: missed coupons (memory) and knock-in flag."""
    import datetime as dt

    from optlab.structured.dates import add_months, adjust, business_days, schedule, year_fraction

    g = sheet.get
    val = dt.date.fromisoformat(valuation)
    M, conv, lag = g("general.maturity_months"), g("general.convention"), g("general.pay_lag")
    N, ctype = g("general.notional"), g("coupon.type")
    autocall = sheet.mask.name == "autocall"
    roles = {}
    if ctype in ("fixed", "conditional"):
        freq = g("coupon.frequency_months")
        for o, p in schedule(val, M, freq, conv, lag):
            roles.setdefault(o, {"pay": p})["coupon"] = True
        cpn = g("coupon.rate") * freq / 12
    if autocall:
        cf = g("autocall.frequency_months")
        first = g("autocall.first_call")
        for j, (o, p) in enumerate(schedule(val, M, cf, conv, lag)[first - 1 : -1]):
            roles.setdefault(o, {"pay": p})["call"] = g("autocall.trigger") - j * g("autocall.step_down")
            roles[o]["period"] = first + j
        if ctype == "snowball":
            cpn = g("coupon.rate") * cf / 12
        n_periods = M // cf
        trig_final = g("autocall.trigger") - (n_periods - first) * g("autocall.step_down")
    maturity = adjust(add_months(val, M), conv)
    final_pay = schedule(val, M, M, conv, lag)[0][1]
    roles.setdefault(maturity, {"pay": final_pay})["final"] = True
    dates = sorted(roles)
    t = {d: year_fraction(val, d) for d in dates}
    K, B = g("downside.strike", 1.0), g("downside.barrier", 0.0)
    dtype, daily = g("downside.type"), g("downside.observation") == "daily"
    cb = g("coupon.barrier") if ctype == "conditional" else None
    memory = ctype == "conditional" and g("coupon.memory")
    levels = [K, B, *(r["call"] for r in roles.values() if "call" in r)]
    levels += [cb] if cb else []
    levels += [trig_final] if autocall else []
    edges = _edges([v for v in levels if v and v > 0], lo, hi, dx)
    x = 0.5 * (edges[1:] + edges[:-1])
    S = S0 * np.exp(x)
    n_m = len(dates) + 1 if memory else 1
    r_, q_ = market.zero_rate(1.0), market.zero_rate(1.0) - market.carry_rate(1.0)

    @functools.cache
    def kernel(dt_):
        mu, sd = (float(r_ - q_) - 0.5 * vol**2) * dt_, vol * np.sqrt(dt_)
        cdf = norm.cdf((edges[None, :] - x[:, None] - mu) / sd)
        return np.diff(cdf, axis=1)

    def step(V, t0, t1, from_x=None):
        """E[V(x_t1) | x_t0] for every cell (or from one point)."""
        if from_x is not None:
            mu, sd = (float(r_ - q_) - 0.5 * vol**2) * (t1 - t0), vol * np.sqrt(t1 - t0)
            p = np.diff(norm.cdf((edges - from_x - mu) / sd))
            return V @ p
        return V @ kernel(round(t1 - t0, 12)).T

    below = x < np.log(B) if dtype == "pdi" else np.zeros_like(x, dtype=bool)

    def propagate(V, d0, d1, from_x=None):
        """Carry V (shape m, hit, cells) known at date d1 back to d0, with daily knock-in monitoring."""
        if not daily:
            return step(V, t[d0] if d0 in t else year_fraction(val, d0), t[d1], from_x)
        days = [d for d in business_days(d0, d1) if d > d0]
        times = [year_fraction(val, d0)] + [year_fraction(val, d) for d in days]
        for i in range(len(times) - 1, 0, -1):
            # arriving at a fixing: the flag switches on below the barrier
            V = V.copy()
            V[:, 0, :] = np.where(below, V[:, 1, :], V[:, 0, :])
            V = step(V, times[i - 1], times[i], from_x if i == 1 else None)
        return V

    W = None
    for k in range(len(dates) - 1, -1, -1):
        d, role = dates[k], roles[dates[k]]  # noqa: F841 (dead since v0, removed in A8)
        D = float(market.D(year_fraction(val, role["pay"])))
        new = np.zeros((n_m, 2, len(x)))
        for m in range(n_m):
            pay = np.zeros_like(x)
            m_next = np.full(len(x), m)
            if role.get("coupon"):
                if ctype == "fixed":
                    pay += N * cpn
                else:
                    hit_c = S >= cb * S0
                    pay += np.where(hit_c, N * cpn * (1 + m if memory else 1), 0.0)
                    m_next = np.where(hit_c, 0, min(m + 1, n_m - 1))
            for h in (0, 1):
                if role.get("final"):
                    tot = pay.copy()
                    if ctype == "snowball":
                        tot += np.where(S >= trig_final * S0, N * cpn * n_periods, 0.0)
                    ki = h == 1 if daily else (S < B * S0)
                    if dtype == "pdi":
                        red = np.where(ki, N - N * np.maximum(K - S / S0, 0.0), N)
                    elif dtype == "geared_put":
                        red = N - N * np.maximum(K - S / S0, 0.0) / K
                    else:
                        red = np.full_like(x, N)
                    new[m, h] = D * (tot + red)
                    continue
                cont = W[m_next, h, np.arange(len(x))] if memory else W[0, h]
                if "call" in role:
                    called = S >= role["call"] * S0
                    called_pay = N + (N * cpn * role["period"] if ctype == "snowball" else 0.0)
                    new[m, h] = D * pay + np.where(called, D * called_pay, cont)
                else:
                    new[m, h] = D * pay + cont
        W = new if k == len(dates) - 1 else new
        if k > 0:
            W = propagate(W, dates[k - 1], dates[k])
    return float(propagate(W, val, dates[0], from_x=0.0)[0, 0])


# --- quanto under Black-Scholes ---


def quanto_forward(S, T, r_foreign, q, vol, vol_fx, rho):
    """Forward of a foreign asset under the domestic measure, written out rather than read from the library."""
    return S * np.exp((r_foreign - q - rho * vol * vol_fx) * T)


def quanto_call_undiscounted(S, K, T, r_foreign, q, vol, vol_fx, rho):
    """Closed form of E^d[(S_T - K)+]: Black on the quanto forward (Reiner 1992, Derman, Karasinski and Wecker 1990)."""
    F = quanto_forward(S, T, r_foreign, q, vol, vol_fx, rho)
    sd = vol * np.sqrt(T)
    d1 = np.log(F / K) / sd + 0.5 * sd
    return float(F * norm.cdf(d1) - K * norm.cdf(d1 - sd))


def quanto_call_by_change_of_measure(S, K, T, r_foreign, r_domestic, q, vol, vol_fx, rho, n=2_000_000, seed=2026):
    """(price, se) with no quanto drift anywhere: S and X = domestic per foreign are drawn under the foreign measure, where S drifts
    by r_foreign - q and X by r_domestic - r_foreign + vol_fx^2. A domestic payoff Y at T is Y / X_T foreign units, so its domestic
    value today is X_0 D_foreign E^f[Y / X_T]. X_0 cancels."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((2, n // 2))
    z = np.concatenate([z, -z], axis=1)
    z_s, z_x = z[0], rho * z[0] + np.sqrt(1 - rho**2) * z[1]
    s = S * np.exp((r_foreign - q - 0.5 * vol**2) * T + vol * np.sqrt(T) * z_s)
    x_ratio = np.exp((r_domestic - r_foreign + 0.5 * vol_fx**2) * T + vol_fx * np.sqrt(T) * z_x)  # X_T / X_0
    pv = np.exp(-r_foreign * T) * np.maximum(s - K, 0.0) / x_ratio
    pairs = 0.5 * (pv[: n // 2] + pv[n // 2 :])
    return float(pairs.mean()), float(pairs.std(ddof=1) / np.sqrt(len(pairs)))


# --- worst-of of two names under Black-Scholes ---


def _log_params(T, vol, r, q):
    return (r - q - 0.5 * vol**2) * T, vol * np.sqrt(T)


def worst_digital(b, T, vols, rho, r, qs):
    """P(min(S1_T / S1_0, S2_T / S2_0) >= b): bivariate normal."""
    from scipy.stats import multivariate_normal

    d = [(m - np.log(b)) / s for m, s in (_log_params(T, v, r, q) for v, q in zip(vols, qs))]
    return float(multivariate_normal(mean=[0, 0], cov=[[1, rho], [rho, 1]]).cdf(d))


def worst_put(K, T, vols, rho, r, qs):
    """E[(K - min(X1, X2))+], X = S_T / S_0, by conditioning on the first name: given x1, X2 is lognormal and
    (K - min(x1, X2))+ is (K - x1) + (x1 - X2)+ when x1 < K, (K - X2)+ otherwise."""
    from scipy.integrate import quad

    (m1, s1), (m2, s2) = (_log_params(T, v, r, q) for v, q in zip(vols, qs))
    s2c = s2 * np.sqrt(1 - rho**2)

    def put2(c, z):  # E[(c - X2)+ | z1 = z]
        fwd = np.exp(m2 + rho * s2 * z + 0.5 * s2c**2)
        return float(black_price(fwd, c, 1.0, s2c, 1.0, "put")) if s2c > 0 else max(c - fwd, 0.0)

    def f(z):
        x1 = np.exp(m1 + s1 * z)
        inner = (K - x1) + put2(x1, z) if x1 < K else put2(K, z)
        return inner * norm.pdf(z)

    z_star = (np.log(K) - m1) / s1
    lo = quad(f, -12, z_star, limit=200, epsabs=1e-13)[0]
    hi = quad(f, z_star, 12, limit=200, epsabs=1e-13)[0]
    return lo + hi
