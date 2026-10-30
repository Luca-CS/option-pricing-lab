"""Pricing policy: the model and method suited to a product, chosen from its profile, with the reason.

The rules are data and can be replaced. Black-Scholes and Heston are never chosen by default (BS ignores the
smile, Heston does not reprice it exactly) but stay available when forced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .profile import ProductProfile


@dataclass(frozen=True)
class Rule:
    method: str  # replication or monte_carlo
    model: str  # surface (replication), slv, lv, heston, bs
    control: str | None  # second model priced for comparison
    reason: str


RULES = {
    "european": Rule("replication", "surface", "lv",
                     "only the final fixing matters: any smile-consistent model gives the same price, so it is "
                     "replicated on the implied surface (no Monte Carlo noise); local vol Monte Carlo as a check"),
    "path_dependent": Rule("monte_carlo", "slv", "lv",
                           "the payoff depends on the path, so the smile dynamics matter: SLV, which fits the smile "
                           "and keeps stochastic forward skew; the local vol price shows the model risk"),
    "callable": Rule("monte_carlo", "slv", "lv",
                     "autocalls depend on the forward skew between observation dates, which local vol flattens: "
                     "SLV; the local vol price shows the model risk"),
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

    @property
    def label(self) -> str:
        what = "replication on the SVI surface" if self.method == "replication" else \
            f"{self.model.upper()} Monte Carlo ({self.n_paths:,} paths)"
        ctrl = f", control {self.control.upper()}" if self.control else ""
        return f"{self.product_class} -> {what}{ctrl}" + (" [forced]" if self.forced else "")


def decide(prof: ProductProfile, t_max: float, model: str | None = None, control: str | None = "default",
           n_paths: int = 200_000, batch: int = 25_000, seed: int = 2026, rules: dict | None = None,
           has_surfaces: bool = True) -> PricingDecision:
    """Policy decision for a profile. `model` forces a Monte Carlo model ('bs', 'heston', 'lv', 'slv') or
    'surface' (replication, European class only); `control=None` skips the control price."""
    rules = rules or RULES
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
    return PricingDecision(prof.product_class, method, model, control, reason, n_paths, batch, seed, forced, warnings)
