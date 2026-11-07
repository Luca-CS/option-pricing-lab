"""Solving for the coupon. On common random numbers and without smoothing, each path's present value is affine in
the coupon (the coupon never enters a condition), so two evaluations give the exact solution on those paths."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from optlab.products import MCResult
from optlab.structured.pricer import StructuredResult


def combine(r0: StructuredResult, r1: StructuredResult, x: float) -> StructuredResult:
    """The result at r0 + x (r1 - r0), for results priced at coupon 0 and 1 on the same paths."""
    if r0.pairs is not None and r1.pairs is not None:
        d = r0.pairs + x * (r1.pairs - r0.pairs)
        value = MCResult(float(d.mean()), float(d.std(ddof=1) / np.sqrt(len(d))), r0.n_paths)
    else:
        d = None
        value = MCResult(r0.value.price + x * (r1.value.price - r0.value.price), 0.0, r0.n_paths)
    flows = {
        k: r0.expected_flows.get(k, 0.0) + x * (r1.expected_flows.get(k, 0.0) - r0.expected_flows.get(k, 0.0))
        for k in set(r0.expected_flows) | set(r1.expected_flows)
    }
    N = r0.notional
    return replace(
        r0,
        value=value,
        price=100 * value.price / N,
        stderr=100 * value.stderr / N,
        expected_flows=dict(sorted(flows.items())),
        pairs=d,
        numerical_error=max(r0.numerical_error, r1.numerical_error),
    )


def solve_affine(r0: StructuredResult, r1: StructuredResult, target: float) -> tuple[float, float]:
    """x such that the price (in %) is the target, and its standard error by the delta method."""
    slope = r1.price - r0.price
    if abs(slope) < 1e-14:
        raise ValueError("the price does not depend on the coupon")
    x = (target - r0.price) / slope
    at_x = combine(r0, r1, x)
    return x, at_x.stderr / abs(slope)
