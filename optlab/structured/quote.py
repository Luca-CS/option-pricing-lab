"""Quoting a term sheet: compile, profile, choose the model (policy), solve the coupon or the upfront, check."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .cashflows import Note, Swap
from .compiler import CompiledProduct, compile_sheet
from .context import PricingContext
from .policy import PricingDecision, decide
from .pricer import StructuredResult, history_times, price_many
from .profile import ProductProfile, profile
from .replication import replication_result
from .schema import TermSheet
from .script.timeline import Clock, build_timeline
from .solve import combine, solve_affine


@dataclass
class Quote:
    product: CompiledProduct
    profile: ProductProfile
    decision: PricingDecision
    fmt: str
    result: StructuredResult  # at the solved (or given) coupon
    target: float | None  # price aimed at, in % (coupon mode)
    coupon_rate: float | None  # per annum
    coupon_rate_se: float | None
    upfront: float | None  # upfront mode: paid by the investor, in % (note: the price)
    margin: float  # in % of notional
    control: StructuredResult | None = None
    seconds: float = 0.0
    warnings: list = field(default_factory=list)
    greeks: object | None = None  # greeks.Greeks at the solved coupon, with the policy model

    @property
    def control_gap_se(self) -> float | None:
        if self.control is None:
            return None
        se = np.hypot(self.result.stderr, self.control.stderr)
        return (self.result.price - self.control.price) / se if se > 0 else float("inf")

    def table(self) -> str:
        from .report import quote_table
        return quote_table(self)


def _pricer(product: CompiledProduct, ctx: PricingContext, fmt, decision: PricingDecision, prof: ProductProfile,
            model_name: str, method: str, n_paths: int | None = None, fixings: dict | None = None):
    program, valuation = product.program, product.valuation
    clock = Clock(valuation)
    timeline = build_timeline(program, clock)
    history = history_times(fixings, program.underlying_names[0], clock)

    def run(param_sets):
        if method == "replication":
            T_obs = prof.observation_times[-1] if prof.observation_times else timeline.horizon
            return [replication_result(program, timeline, ctx.surface, ctx.market, T_obs, fmt, p, history=history)
                    for p in param_sets]
        model = ctx.model(model_name, max(product.horizon, 1 / 365))
        return price_many(program, model, ctx.market, param_sets, fmt, n_paths or decision.n_paths, decision.seed,
                          decision.batch, valuation=valuation, fixings=fixings)
    return run


def quote(sheet: TermSheet, ctx: PricingContext | None = None, model: str | None = None, control="default",
          n_paths: int = 200_000, batch: int = 25_000, seed: int = 2026, control_paths: int | None = None,
          fixings: dict | None = None, with_greeks: bool = False) -> Quote:
    """Price a term sheet with the policy model. In 'coupon' mode the coupon is solved so that the note price is
    100 - margin, or the swap upfront is -margin (zero upfront paid, the margin kept by the bank); in 'upfront' mode
    the coupon is given and the result is the note price, or the swap upfront paid by the investor (leg value +
    margin). A running product (strike date before the valuation date) needs its past `fixings`. `with_greeks`
    adds delta and vega with the policy model, on the same paths, at the solved coupon."""
    t0 = time.perf_counter()
    ctx = ctx or PricingContext.from_calibration()
    product = compile_sheet(sheet, ctx.valuation)
    if product.strike_date < product.valuation and sheet.get("wrapper.solve") == "coupon":
        raise ValueError("a running product has a fixed coupon: price it with wrapper.solve = 'upfront'")
    prof = profile(product.program, build_timeline(product.program, Clock(product.valuation)))
    decision = decide(prof, ctx.t_max, model, control, n_paths, batch, seed)
    fmt = Note(sheet.get("wrapper.issuer_spread")) if sheet.get("wrapper.format") == "note" else Swap()
    margin = 100 * sheet.get("wrapper.margin")
    run = _pricer(product, ctx, fmt, decision, prof, decision.model, decision.method, fixings=fixings)
    cpn = product.coupon_param
    target = rate = rate_se = upfront = None
    if sheet.get("wrapper.solve") == "coupon":
        target = 100 - margin if fmt.name == "note" else -margin
        r0, r1 = run([{cpn: 0.0}, {cpn: 1.0}])
        x, x_se = solve_affine(r0, r1, target)
        result = combine(r0, r1, x)
        rate, rate_se = product.coupon_rate(x), x_se / product.coupon_scale
        params = {cpn: x}
    else:
        (result,) = run([{}])
        upfront = result.price if fmt.name == "note" else result.price + margin
        params = {}
    ctrl = None
    if decision.control:
        ctrl_run = _pricer(product, ctx, fmt, decision, prof, decision.control, "monte_carlo",
                           control_paths or decision.n_paths, fixings)
        (ctrl,) = ctrl_run([params])
    warnings = list(decision.warnings)
    if rate is not None and rate < 0:
        warnings.append("the solved coupon is negative: the product cannot reach the target price with a coupon")
    g = None
    if with_greeks:
        if fixings:
            raise ValueError("Greeks of running products are not supported yet")
        from .greeks import greeks
        g = greeks(product.program, ctx, decision.model, decision.method, params, fmt, product.valuation,
                   decision.n_paths, decision.seed, decision.batch)
    return Quote(product, prof, decision, fmt.name, result, target, rate, rate_se, upfront, margin, ctrl,
                 time.perf_counter() - t0, warnings, g)
