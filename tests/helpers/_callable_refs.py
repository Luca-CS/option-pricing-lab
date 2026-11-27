"""Independent reference for issuer-callable notes: a 1D PDE in x = ln(S/S0), deterministic rates, Black-Scholes or
local vol, solved backward with Crank-Nicolson (Rannacher steps after each date, where the value has a kink).

The note is seen from the investor: coupons on their dates while the note lives, a final payoff of the performance,
and at each call date the issuer may redeem at a call price. The issuer pays the least it can, so just before a call
date the value is the date's coupon plus min(continuation, call price). Discounting is at r + spread, the issuer's
curve, the drift r - q.
"""

import numpy as np
from scipy.linalg import solve_banded


def _operator(x, dx, t, r, q, spread, local_vol, s0):
    """Tridiagonal coefficients (lower, diag, upper) of L V = (r - q - s^2/2) V_x + s^2/2 V_xx - (r + spread) V."""
    sig = np.asarray(local_vol(t, s0 * np.exp(x)), dtype=float) * np.ones_like(x)
    drift = r - q - 0.5 * sig**2
    a = 0.5 * sig**2 / dx**2
    b = drift / (2 * dx)
    lower, diag, upper = a - b, -2 * a - (r + spread), a + b
    return lower, diag, upper


def _step(V, x, dx, t0, t1, theta, r, q, spread, local_vol, s0):
    """V at t0 from V at t1 (t0 < t1): (I - theta dt L) V0 = (I + (1 - theta) dt L) V1, linear at both ends."""
    dt = t1 - t0
    tm = t0 + (1 - theta) * dt if theta < 1 else t0
    lo, di, up = _operator(x, dx, tm, r, q, spread, local_vol, s0)
    n = len(V)
    rhs = V.copy()
    if theta < 1:
        lv = np.zeros(n)
        lv[1:-1] = lo[1:-1] * V[:-2] + di[1:-1] * V[1:-1] + up[1:-1] * V[2:]
        rhs = V + (1 - theta) * dt * lv
    ab = np.zeros((3, n))
    ab[0, 2:] = -theta * dt * up[1:-1]
    ab[1, 1:-1] = 1 - theta * dt * di[1:-1]
    ab[2, :-2] = -theta * dt * lo[1:-1]
    # the value is linear in x at both ends: V0 - 2 V1 + V2 = 0, written in the band as V0 = 2 V1 - V2 after solving
    ab[1, 0], ab[0, 1] = 1.0, -1.0  # V0 - V1 = rhs0, with rhs0 = V1 - V2 from the last step
    ab[1, -1], ab[2, -2] = 1.0, -1.0
    rhs[0] = V[0] - V[1]
    rhs[-1] = V[-1] - V[-2]
    return solve_banded((1, 1), ab, rhs)


def callable_note(
    final,
    T,
    coupons=(),
    calls=(),
    r=0.04,
    q=0.01,
    vol=0.2,
    local_vol=None,
    spread=0.0,
    s0=100.0,
    n_x=1601,
    width=7.0,
    steps_per_year=500,
    rannacher=4,
    exercise=True,
    at=1.0,
):
    """Investor value at the performance `at` (1: the spot at its initial fixing) of a note paying `final(perf)` at
    T and `coupons` [(t, amount)], an amount being a number or a function of the performance there, callable by the
    issuer at `calls` [(t, price)] (dates in (0, T)); `exercise=False` prices the same note without the call."""
    local_vol = local_vol or (lambda t, S: vol)
    sd = max(vol, 0.05) * np.sqrt(T)
    x = np.linspace(-width * sd, width * sd, n_x)
    dx = x[1] - x[0]
    V = np.asarray(final(np.exp(x)), dtype=float).copy()
    perf = np.exp(x)
    events: dict = {}
    for t, amount in coupons:
        paid = np.asarray(amount(perf), dtype=float) if callable(amount) else float(amount)
        events.setdefault(round(float(t), 12), [0.0, None])[0] += paid
    for t, price in calls:
        events.setdefault(round(float(t), 12), [0.0, None])[1] = price
    dates = sorted(t for t in events if 0 < t <= T)
    if T in events:  # the last coupon is paid with the final payoff
        V = V + events[T][0]
        dates.remove(T)
    grid = [0.0, *dates, T]
    for i in range(len(grid) - 1, 0, -1):
        t0, t1 = grid[i - 1], grid[i]
        n = max(int(np.ceil((t1 - t0) * steps_per_year)), rannacher + 1)
        times = np.linspace(t0, t1, n + 1)
        for k in range(n, 0, -1):
            theta = 1.0 if n - k < rannacher else 0.5
            V = _step(V, x, dx, times[k - 1], times[k], theta, r, q, spread, local_vol, s0)
        if t0 > 0:
            amount, price = events[t0]
            if price is not None and exercise:
                V = np.minimum(V, price)
            V = V + amount
    return float(np.interp(np.log(at), x, V))


def callable_layers(
    T,
    dates,
    coupon,
    barrier,
    calls,
    final,
    memory=False,
    ki=None,
    daily=None,
    r=0.04,
    q=0.01,
    vol=0.2,
    local_vol=None,
    spread=0.0,
    s0=100.0,
    n_x=1201,
    width=7.0,
    steps_per_year=400,
    rannacher=4,
):
    """A callable Phoenix or BRC on layers of the same grid: the count of missed coupons (memory) and the knock-in flag.

    `dates` are the coupon dates (T the last), a coupon of `coupon` (per period) paid when the performance is at or
    above `barrier` (with memory: `coupon` times one plus the missed ones, the count reset); `calls` {t: price} on
    some of them, decided after the coupon; `final(perf, knocked)` the redemption at T. `ki` with `daily` (the times
    of every close) switches the flag on at a close below `ki`. Returns the investor value at the initial fixing."""
    local_vol = local_vol or (lambda t, S: vol)
    sd = max(vol, 0.05) * np.sqrt(T)
    x = np.linspace(-width * sd, width * sd, n_x)
    dx = x[1] - x[0]
    perf = np.exp(x)
    n_m = len(dates) if memory else 1
    n_k = 2 if ki is not None else 1
    above = perf >= barrier

    def pay_and_move(V, t):
        """Just before a coupon date: the coupon, the memory's move, then the issuer's call on what follows."""
        if t in calls:
            V = np.minimum(V, calls[t])
        out = np.empty_like(V)
        for m in range(n_m):
            paid = coupon * (1 + m) if memory else coupon
            nxt = min(m + 1, n_m - 1)
            out[m] = np.where(above[None, :], paid + V[0 if memory else m], V[nxt if memory else m])
        return out

    V = np.empty((n_m, n_k, n_x))
    for k in range(n_k):
        V[:, k, :] = final(perf, bool(k))[None, :]
    V = pay_and_move(V, T)  # the last coupon, with the redemption: no call at maturity
    knock_times = sorted(t for t in (daily or ()) if 0 < t <= T) if ki is not None else []
    marks = sorted({0.0, *[t for t in dates if t < T], *knock_times, T})
    for i in range(len(marks) - 1, 0, -1):
        t0, t1 = marks[i - 1], marks[i]
        if ki is not None and t1 in knock_times:  # arriving at a close below the barrier switches the flag on
            V[:, 0, :] = np.where(perf[None, :] < ki, V[:, 1, :], V[:, 0, :])
        n = max(int(np.ceil((t1 - t0) * steps_per_year)), 1)
        times = np.linspace(t0, t1, n + 1)
        fresh = t1 in dates or t1 == T
        for j in range(n, 0, -1):
            theta = 1.0 if fresh and n - j < rannacher else 0.5
            for m in range(n_m):
                for k in range(n_k):
                    V[m, k] = _step(V[m, k], x, dx, times[j - 1], times[j], theta, r, q, spread, local_vol, s0)
        if t0 in dates and t0 > 0:
            V = pay_and_move(V, t0)
    return float(np.interp(0.0, x, V[0, 0]))


def hw_callable_bond(
    coupons, calls, T, market, a, sigma, spread=0.0, n_x=801, width=7.0, steps_per_year=400, rannacher=4
):
    """A note on rates alone under Hull-White (G1++ fitted to `market`'s curve): `coupons` [(t, amount)] and 1 at T,
    callable by the issuer at `calls` {t: price} (after the coupon). A PDE in the rate factor x:
    V_t - a x V_x + sigma^2/2 V_xx - (phi(t) + x + spread) V = 0, phi(t) = f(0,t) + sigma^2/(2a^2)(1 - e^{-at})^2."""
    sd = sigma * np.sqrt((1 - np.exp(-2 * a * T)) / (2 * a))
    x = np.linspace(-width * sd, width * sd, n_x)
    dx = x[1] - x[0]

    def phi(t):
        h = 1e-5
        f = -(np.log(float(market.D(t + h))) - np.log(float(market.D(max(t - h, 0.0))))) / (t + h - max(t - h, 0.0))
        return f + sigma**2 / (2 * a**2) * (1 - np.exp(-a * t)) ** 2

    def step(V, t0, t1, theta):
        dt = t1 - t0
        tm = t0 + 0.5 * dt
        r = phi(tm) + x + spread
        lo = 0.5 * sigma**2 / dx**2 + a * x / (2 * dx)
        up = 0.5 * sigma**2 / dx**2 - a * x / (2 * dx)
        di = -(sigma**2) / dx**2 - r
        n = len(V)
        rhs = V.copy()
        if theta < 1:
            lv = np.zeros(n)
            lv[1:-1] = lo[1:-1] * V[:-2] + di[1:-1] * V[1:-1] + up[1:-1] * V[2:]
            rhs = V + (1 - theta) * dt * lv
        ab = np.zeros((3, n))
        ab[0, 2:] = -theta * dt * up[1:-1]
        ab[1, 1:-1] = 1 - theta * dt * di[1:-1]
        ab[2, :-2] = -theta * dt * lo[1:-1]
        ab[1, 0], ab[0, 1] = 1.0, -1.0
        ab[1, -1], ab[2, -2] = 1.0, -1.0
        rhs[0], rhs[-1] = V[0] - V[1], V[-1] - V[-2]
        return solve_banded((1, 1), ab, rhs)

    pays = dict(coupons)
    V = np.full(n_x, 1.0 + pays.get(T, 0.0))
    dates = sorted({0.0, *[t for t in pays if t < T], *[t for t in calls if t < T], T})
    for i in range(len(dates) - 1, 0, -1):
        t0, t1 = dates[i - 1], dates[i]
        n = max(int(np.ceil((t1 - t0) * steps_per_year)), rannacher + 1)
        times = np.linspace(t0, t1, n + 1)
        for k in range(n, 0, -1):
            V = step(V, times[k - 1], times[k], 1.0 if n - k < rannacher else 0.5)
        if t0 > 0:
            if t0 in calls:
                V = np.minimum(V, calls[t0])
            V = V + pays.get(t0, 0.0)
    return float(np.interp(0.0, x, V))
