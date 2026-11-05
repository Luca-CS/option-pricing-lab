"""Quoting a term sheet: compile, profile, choose the model (policy), solve the coupon or the upfront, check."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..config import DEFAULT_SEED
from ..log import get_logger
from .cashflows import FundedSwap, Note, Swap, funding_periods
from .compiler import CompiledProduct, compile_sheet
from .context import PricingContext
from .policy import PricingDecision, decide
from .multiasset import BasketContext
from .pricer import StructuredResult, check_fixing_scale, history_times, price_many
from .profile import ProductProfile, profile
from .replication import replication_result
from .schema import TermSheet
from .script.nodes import Param, Program
from .script.printer import to_text
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
    snapshot_id: str | None = None  # the market the quote was priced on
    mix_band: dict | None = None  # {mix: {"value", "se", "atm_error_bp"}}: coupon p.a. or upfront per SLV mix

    @property
    def control_gap_se(self) -> float | None:
        if self.control is None:
            return None
        se = np.hypot(self.result.stderr, self.control.stderr)
        return (self.result.price - self.control.price) / se if se > 0 else float("inf")

    @property
    def script(self) -> str:
        """The product's script with the solved coupon written in."""
        if self.coupon_rate is None or self.product.coupon_param is None:
            return self.product.script
        cpn = self.coupon_rate * self.product.coupon_scale
        p = self.product.program
        params = tuple(Param(x.name, cpn, x.line) if x.name == self.product.coupon_param else x for x in p.params)
        return to_text(Program(p.underlyings, params, p.events))

    def table(self) -> str:
        from .report import quote_table
        return quote_table(self)


LOG = get_logger(__name__)


def _book_greeks(sheet: TermSheet, params: dict, fixings, ctx, decision: PricingDecision, slv_vega: bool,
                 workers: int | None = None):
    """Greeks of a running product or of a basket, through a one-position book (which keeps the fixings and bumps
    each name), converted to % of notional. For a basket, delta and vega are the sums over the names (all names
    moved together, to first order) and `per_name` holds each name; the SE of a sum adds the SEs in quadrature."""
    from .greeks import Greeks
    from .portfolio import Book, Position
    model = None if decision.method == "replication" else decision.model
    g = Book([Position("quote", sheet, 1.0, params=params, fixings=fixings)]).greeks(
        ctx, model=model, n_paths=decision.n_paths, seed=decision.seed, batch=decision.batch, slv_vega=slv_vega,
        workers=workers)
    scale = 100 / sheet.get("general.notional")

    def one(d):
        return (scale * d["positions"][0], scale * d["positions_se"][0]) if d is not None else (None, None)

    if isinstance(ctx, BasketContext):
        per = {n: dict(zip(("delta", "delta_se"), one(g["delta"][n])), **dict(zip(("vega", "vega_se"),
               one(g["vega"][n])))) for n in g["delta"]}
        delta = sum(v["delta"] for v in per.values())
        delta_se = float(np.sqrt(sum(v["delta_se"] ** 2 for v in per.values())))
        vega = sum(v["vega"] for v in per.values())
        vega_se = float(np.sqrt(sum(v["vega_se"] ** 2 for v in per.values())))
        return Greeks(delta, delta_se, vega, vega_se, g["smoothing"], 0.0, 0.0, g["method"], g["model"], {},
                      "basket: delta and vega summed over the names, see per_name", per_name=per)
    delta, delta_se = one(g["delta"])
    vega, vega_se = one(g.get("vega"))
    return Greeks(delta, delta_se, vega, vega_se, g["smoothing"], 0.0, 0.0, g["method"], g["model"], {},
                  g.get("note", "running product: Greeks through a one-position book, fixings kept"))


def wrapper(sheet: TermSheet, product: CompiledProduct):
    """Note (issuer spread), Swap (upfront only) or FundedSwap (floating leg until redemption) for a sheet."""
    if sheet.get("wrapper.format") == "note":
        return Note(sheet.get("wrapper.issuer_spread"))
    if sheet.get("wrapper.funding") != "floating":
        return Swap()
    g = sheet.get
    periods = funding_periods(product.strike_date, g("general.maturity_months"), g("wrapper.funding_frequency_months"),
                              product.valuation, g("general.convention"), g("general.pay_lag"), product.calendar)
    tl = build_timeline(product.program, Clock(product.valuation, product.calendar))
    return FundedSwap(g("wrapper.funding_spread"), periods, tuple(float(t) for t in tl.event_times))


def _mix_band(mixes, product, ctx, fmt, decision, prof, fixings, workers, margin, sheet) -> dict:
    """Coupon (or upfront) under SLV for each mixing fraction, and the ATM vol error of each recalibrated model."""
    from ..validation import mc_smile
    T = max(product.horizon, 1 / 365)
    T_check = min(T, ctx.t_max)
    F = float(ctx.market.F(T_check))
    out = {}
    for mix in mixes:
        model = ctx.slv_at_mix(mix, T)
        run = _pricer(product, ctx, fmt, decision, prof, "slv", "monte_carlo", fixings=fixings, workers=workers,
                      model_object=model)
        if sheet.get("wrapper.solve") == "coupon":
            target = 100 - margin if fmt.name == "note" else -margin
            r0, r1 = run([{product.coupon_param: 0.0}, {product.coupon_param: 1.0}])
            x, x_se = solve_affine(r0, r1, target)
            value, se = product.coupon_rate(x), x_se / product.coupon_scale
        else:
            (r,) = run([{}])
            value, se = (r.price if fmt.name == "note" else r.price + margin), r.stderr
        iv, _ = mc_smile(model, ctx.market, T_check, [F], n_paths=200_000, rng=decision.seed)
        out[float(mix)] = {"value": value, "se": se, "atm_error_bp": (float(iv[0]) - ctx.atm_vol(T_check)) * 1e4}
    return out


def _pricer(product: CompiledProduct, ctx: PricingContext, fmt, decision: PricingDecision, prof: ProductProfile,
            model_name: str, method: str, n_paths: int | None = None, fixings: dict | None = None,
            workers: int | None = None, control_forwards: bool = False, fast: bool = False, model_object=None):
    program, valuation = product.program, product.valuation
    clock = Clock(valuation, product.calendar)
    timeline = build_timeline(program, clock)
    names = program.underlying_names
    history = history_times(fixings, names[0], clock) if len(names) == 1 else None
    if history:
        check_fixing_scale(history, ctx.market.spot, names[0])

    def run(param_sets):
        if method == "replication":
            T_obs = prof.observation_times[-1] if prof.observation_times else timeline.horizon
            return [replication_result(program, timeline, ctx.surface, ctx.market, T_obs, fmt, p, history=history)
                    for p in param_sets]
        T = max(product.horizon, 1 / 365)
        if model_object is not None:
            model = model_object
        elif isinstance(ctx, BasketContext):
            model = ctx.model(model_name, T, names)
        else:  # `fast` only when asked, so that contexts with their own model(name, T) keep working
            model = ctx.model(model_name, T, fast=True) if fast else ctx.model(model_name, T)
        return price_many(program, model, ctx.market, param_sets, fmt, n_paths or decision.n_paths, decision.seed,
                          decision.batch, valuation=valuation, fixings=fixings, calendar=product.calendar,
                          workers=workers, control_forwards=control_forwards)
    return run


def quote(sheet: TermSheet, ctx: PricingContext | None = None, model: str | None = None, control="default",
          n_paths: int = 200_000, batch: int = 25_000, seed: int = DEFAULT_SEED, control_paths: int | None = None,
          fixings: dict | None = None, with_greeks: bool = False, slv_vega: bool = False,
          workers: int | None = None, control_forwards: bool = False, fast: bool = False,
          sticky: str = "moneyness", mix_band: tuple | None = None) -> Quote:
    """Price a term sheet with the policy model. In 'coupon' mode the coupon is solved so that the note price is
    100 - margin, or the swap upfront is -margin (zero upfront paid, the margin kept by the bank); in 'upfront' mode
    the coupon is given and the result is the note price, or the swap upfront paid by the investor (leg value +
    margin). A running product (strike date before the valuation date) needs its past `fixings`. `with_greeks`
    adds delta and vega with the policy model, on the same paths, at the solved coupon (under SLV, the vega needs
    `slv_vega=True`: the leverage is recalibrated on the shifted surfaces). `workers` runs the Monte Carlo batches
    on a process pool (same numbers bit for bit); `control_forwards` adds forward control variates to the Monte
    Carlo estimator; `fast` uses the grid-interpolated local vol and SLV models (fastmodels); `sticky` sets the
    smile dynamics of the delta (moneyness, strike or model). `mix_band`, e.g. (0, 0.25, 0.5, 0.75), reprices
    the product under SLV with the leverage recalibrated for each mixing fraction (same mode, seed and paths): the
    mix is not calibrated, and for autocalls it moves the coupon more than the SLV - LV gap."""
    t0 = time.perf_counter()
    ctx = ctx or PricingContext.from_calibration()
    product = compile_sheet(sheet, ctx.valuation)
    if product.strike_date < product.valuation and sheet.get("wrapper.solve") == "coupon":
        raise ValueError("a running product has a fixed coupon: price it with wrapper.solve = 'upfront'")
    prof = profile(product.program, build_timeline(product.program, Clock(product.valuation, product.calendar)))
    basket = len(product.program.underlyings) > 1
    if basket != isinstance(ctx, BasketContext):
        raise ValueError("a worst-of term sheet needs a BasketContext, a single-name one a PricingContext")
    if not basket and getattr(ctx, "underlying", None) not in (None, *sheet.underlyings):
        raise ValueError(f"the market in use is {ctx.underlying}, the term sheet is on {sheet.underlyings[0]}: "
                         "choose a market of that name")
    decision = decide(prof, ctx.t_max, model, control, n_paths, batch, seed,
                      has_surfaces=getattr(ctx, "has_surfaces", True))
    fmt = wrapper(sheet, product)
    margin = 100 * sheet.get("wrapper.margin")
    run = _pricer(product, ctx, fmt, decision, prof, decision.model, decision.method, fixings=fixings,
                  workers=workers, control_forwards=control_forwards, fast=fast)
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
    band = None
    if mix_band and decision.method == "monte_carlo" and not basket:
        band = _mix_band(mix_band, product, ctx, fmt, decision, prof, fixings, workers, margin, sheet)
    ctrl = None
    if decision.control:
        ctrl_run = _pricer(product, ctx, fmt, decision, prof, decision.control, "monte_carlo",
                           control_paths or decision.n_paths, fixings, workers, fast=fast)
        (ctrl,) = ctrl_run([params])
    warnings = list(decision.warnings)
    if rate is not None and rate < 0:
        warnings.append("the solved coupon is negative: the product cannot reach the target price with a coupon")
    g = None
    if with_greeks and (basket or fixings):
        g = _book_greeks(sheet, params, fixings, ctx, decision, slv_vega, workers)
    elif with_greeks:
        from .greeks import greeks
        g = greeks(product.program, ctx, decision.model, decision.method, params, fmt, product.valuation,
                   decision.n_paths, decision.seed, decision.batch, slv_vega=slv_vega, calendar=product.calendar,
                   workers=workers, sticky=sticky)
    seconds = time.perf_counter() - t0
    LOG.info("quote %s %s: %s, %s, %.2f s", sheet.mask.name, ", ".join(sheet.underlyings), decision.label,
             f"coupon {rate:.6f} p.a." if rate is not None else f"upfront {upfront:.4f} %", seconds)
    if band:
        warnings += [f"mix {m:.2f}: the recalibrated SLV misses the ATM vol at the horizon by {b['atm_error_bp']:+.1f} "
                     "bp (over 5 bp)" for m, b in band.items() if abs(b["atm_error_bp"]) > 5]
    return Quote(product, prof, decision, fmt.name, result, target, rate, rate_se, upfront, margin, ctrl,
                 seconds, warnings, g, getattr(ctx, "snapshot_id", None), band)
