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
