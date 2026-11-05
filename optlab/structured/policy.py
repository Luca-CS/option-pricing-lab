"""Pricing policy: the base model and method suited to a product, chosen from its profile, with the reason, and the models it is compared to.

The rules are data and can be replaced. The base is local vol (or replication on the surface, the same law without the noise). A family's
own risks are in other models, and the gap to each is kept as a provision when it goes against the desk (costs.py). Black-Scholes and
Heston are never chosen by default (BS ignores the smile, Heston does not reprice it exactly) but stay available when forced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import DEFAULT_SEED
from .profile import ProductProfile


@dataclass(frozen=True)
class Rule:
    method: str  # replication or monte_carlo
    model: str  # the base: surface (replication), or lv
    control: str | None  # second model priced for comparison, when no cost is asked
    reason: str
    provisions: tuple = ()  # models whose gap to the base is kept as a fixed provision when it goes against the desk


RULES = {
    "european": Rule("replication", "surface", "lv",
                     "only the final fixing matters: any smile-consistent model gives the same price, so it is "
                     "replicated on the implied surface (no Monte Carlo noise); local vol Monte Carlo as a check"),
    "path_dependent": Rule("monte_carlo", "lv", "slv",
                           "the payoff depends on the path: the base is local vol, which fits the smile; the smile dynamics "
                           "are not in it, so the gap to SLV is a provision when it goes against the desk",
                           ("slv",)),
    "callable": Rule("monte_carlo", "lv", "slv",
                     "the base is local vol, which fits the smile; autocalls depend on the forward skew between observation dates, "
                     "which local vol flattens, so the gap to SLV is a provision when it goes against the desk",
                     ("slv",)),
    "basket": Rule("monte_carlo", "lv", "bs",
                   "worst-of: each name on its own smile (Dupire local vol), correlated spots; the correlation is "
                   "an input with no skew of its own, and SLV is not extended to baskets yet; Black-Scholes as a "
                   "smile-risk control"),
}
MODELS = ("bs", "heston", "lv", "slv")


@dataclass
class PricingDecision:
    product_class: str
    method: str
    model: str
    control: str | None
    reason: str
    n_paths: int
    batch: int
    seed: int
    forced: bool = False
    warnings: list = field(default_factory=list)
    provisions: tuple = ()

    @property
    def label(self) -> str:
        what = "replication on the SVI surface" if self.method == "replication" else \
            f"{self.model.upper()} Monte Carlo ({self.n_paths:,} paths)"
        ctrl = f", control {self.control.upper()}" if self.control else ""
        kept = f", provision {' and '.join(x.upper() for x in self.provisions)}" if self.provisions else ""
        return f"{self.product_class} -> {what}{ctrl}{kept}" + (" [forced]" if self.forced else "")


def decide(prof: ProductProfile, t_max: float, model: str | None = None, control: str | None = "default",
           n_paths: int = 200_000, batch: int = 25_000, seed: int = DEFAULT_SEED, rules: dict | None = None,
           has_surfaces: bool = True) -> PricingDecision:
    """Policy decision for a profile. `model` forces a Monte Carlo model ('bs', 'heston', 'lv', 'slv') or
    'surface' (replication, European class only); `control=None` skips the control price."""
    rules = rules or RULES
    model = model.lower() if isinstance(model, str) else model
    basket = len(prof.underlyings) > 1
    rule = rules["basket"] if basket else rules[prof.product_class]
    warnings = []
    if basket and not has_surfaces:
        warnings.append("some names have a flat volatility (no smile): they are simulated with Black-Scholes")
    if prof.horizon > t_max + 1e-9:
        warnings.append(f"horizon {prof.horizon:.2f}y beyond the last calibrated maturity {t_max:.2f}y: the surface "
                        "is extrapolated and the SLV leverage is not calibrated there")
    forced = model is not None
    if model is None:
        method, model = rule.method, rule.model
    elif model == "surface":
        if prof.product_class != "european" or basket:
            raise ValueError(f"replication needs a European-class product, this one is {prof.product_class}")
        method = "replication"
    elif model in MODELS:
        if basket and model not in ("bs", "lv"):
            raise ValueError(f"baskets are priced under bs or lv, not {model}")
        method = "monte_carlo"
    else:
        raise ValueError(f"unknown model {model!r}, expected surface or one of {', '.join(MODELS)}")
    if control == "default":
        control = rule.control if rule.control != model else None
    reason = rule.reason if not forced else f"forced by the user (policy default: {rule.model}, {rule.method})"
    return PricingDecision(prof.product_class, method, model, control, reason, n_paths, batch, seed, forced, warnings,
                           () if forced else rule.provisions)
