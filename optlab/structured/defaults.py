"""Defaults of the hedging costs: one table by group of underlyings and size of trade, nothing hard-coded in the pricers.

The table comes from the desk's store, where every version is kept. `STARTING` fills it until a value is corrected, and each screen labels
those as starting values: none of them was given by a trader. A value carries its sign and applies as given.

A term sheet takes the tier of its notional, in the currency of each group since no FX is loaded. Above the last tier there is no default:
the Pricer alerts and asks for the values. A worst-of takes, parameter by parameter, the most prudent of its groups: with one sign the largest
in size, the tightest limit, and with two signs both are measured on the product and the one against the desk stays.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..marketdata.universe import instrument
from .costs import BUMPS, CostInputs
from .digitals import DELTA_FIELD
from .schema import SECTIONS, TermSheet

TIERS = (10e6, 50e6, 250e6)


def _tiers(**columns) -> list:
    return [{"max_notional": cap, **{k: v[i] for k, v in columns.items()}} for i, cap in enumerate(TIERS)]


STARTING = {
    "index_us": {
        "label": "US index",
        "currency": "USD",
        "tiers": _tiers(
            vol=(-0.0025, -0.005, -0.01),
            skew=(-0.0025, -0.005, -0.01),
            corr=(0.01, 0.02, 0.04),
            corr_skew=(0.01, 0.02, 0.04),
            dividend=(-0.05, -0.10, -0.15),
            repo=(-0.0005, -0.001, -0.002),
            spot=(0.0002, 0.0005, 0.001),
            slv_mix=(0.5, 0.5, 0.5),
            min_spread=(0.005, 0.01, 0.02),
            delta_max_coupon=(1.0, 1.0, 0.5),
            delta_max_autocall=(1.0, 1.0, 0.5),
            delta_max_barrier=(5.0, 5.0, 2.5),
            delta_max_upside=(1.0, 1.0, 0.5),
        ),
    },
    "index_eu": {
        "label": "European index",
        "currency": "EUR",
        "tiers": _tiers(
            vol=(-0.0025, -0.005, -0.01),
            skew=(-0.0025, -0.005, -0.01),
            corr=(0.01, 0.02, 0.04),
            corr_skew=(0.01, 0.02, 0.04),
            dividend=(-0.10, -0.15, -0.20),
            repo=(-0.0005, -0.001, -0.002),
            spot=(0.0003, 0.0007, 0.0015),
            slv_mix=(0.5, 0.5, 0.5),
            min_spread=(0.005, 0.01, 0.02),
            delta_max_coupon=(1.0, 1.0, 0.5),
            delta_max_autocall=(1.0, 1.0, 0.5),
            delta_max_barrier=(5.0, 5.0, 2.5),
            delta_max_upside=(1.0, 1.0, 0.5),
        ),
    },
    "equity_us": {
        "label": "US large-cap share",
        "currency": "USD",
        "tiers": _tiers(
            vol=(-0.005, -0.01, -0.02),
            skew=(-0.005, -0.01, -0.02),
            corr=(0.02, 0.03, 0.05),
            corr_skew=(0.02, 0.03, 0.05),
            dividend=(-0.05, -0.10, -0.20),
            repo=(-0.001, -0.002, -0.004),
            spot=(0.0005, 0.001, 0.002),
            slv_mix=(0.5, 0.5, 0.5),
            min_spread=(0.01, 0.02, 0.03),
            delta_max_coupon=(1.0, 1.0, 0.5),
            delta_max_autocall=(1.0, 1.0, 0.5),
            delta_max_barrier=(5.0, 5.0, 2.5),
            delta_max_upside=(1.0, 1.0, 0.5),
        ),
    },
}
# Parameter -> how groups combine when it is not a signed bump: the widest spread and the tightest delta limit are the prudent ones.
RULES = {
    "slv_mix": "max",
    "min_spread": "max",
    "delta_max_coupon": "min",
    "delta_max_autocall": "min",
    "delta_max_barrier": "min",
    "delta_max_upside": "min",
}
SKIP = ("side",)


def parameters(sheet: TermSheet | None = None) -> list:
    """The fields of the costs section that carry a default, visible on this sheet if one is given."""
    return [
        f
        for f in SECTIONS["costs"].fields
        if f.name not in SKIP and (sheet is None or sheet.visible(f"costs.{f.name}"))
    ]


def merged(table: dict | None) -> dict:
    """A stored table completed from the starting one: a parameter added since it was saved takes its starting value."""
    out = {}
    stored = (table or {}).get("groups", {})
    for name, start in STARTING.items():
        group = stored.get(name, start)
        tiers = []
        for i, tier in enumerate(group["tiers"]):
            base = start["tiers"][min(i, len(start["tiers"]) - 1)]
            tiers.append({**base, **tier})
        out[name] = {**start, **group, "tiers": tiers}
    for name, group in stored.items():
        out.setdefault(name, group)
    return out


def validate_table(groups: dict) -> dict:
    """The table as stored, or ValueError with what is wrong: tiers rising in size, values within the bounds of the form."""
    spec = {f.name: f for f in parameters()}
    clean = {}
    for name, group in groups.items():
        tiers = group.get("tiers") or []
        if not tiers:
            raise ValueError(f"{name}: at least one size tier")
        caps = [float(t.get("max_notional", 0)) for t in tiers]
        if any(c <= 0 for c in caps) or any(b <= a for a, b in zip(caps, caps[1:])):
            raise ValueError(f"{name}: tiers must have positive sizes, rising")
        rows = []
        for t in tiers:
            row = {"max_notional": float(t["max_notional"])}
            for key, value in t.items():
                if key == "max_notional":
                    continue
                f = spec.get(key)
                if f is None:
                    raise ValueError(f"{name}: unknown parameter {key!r}")
                value = float(value)
                if f.minimum is not None and value < f.minimum or f.maximum is not None and value > f.maximum:
                    raise ValueError(f"{name}: {f.label} = {value} is outside {f.minimum} to {f.maximum}")
                row[key] = value
            rows.append(row)
        clean[name] = {
            "label": str(group.get("label", name)),
            "currency": str(group.get("currency", "")),
            "tiers": rows,
        }
    return clean


def _tier(group: dict, notional: float) -> tuple[int, dict] | None:
    for i, t in enumerate(group["tiers"]):
        if notional <= t["max_notional"]:
            return i, t
    return None


def _prudent(key: str, by_group: dict) -> tuple[float, tuple | None]:
    """(value, conflict): the most prudent of the groups' values for a parameter."""
    values = list(by_group.values())
    rule = RULES.get(key)
    if rule == "max":
        return max(values), None
    if rule == "min":
        return min(values), None
    low, high = min(values), max(values)
    if low < 0 < high:
        return (low if -low > high else high), (low, high)
    return (low if -low > high else high), None


@dataclass
class Resolved:
    """The defaults of one sheet, the values it uses, and what stops it being priced."""

    groups: dict  # name -> group
    tiers: dict  # group -> {"index", "max_notional"} or None above the last tier
    params: dict  # parameter -> {"value", "default", "overridden", "missing", "by_group", "conflict", "starting"}
    side: str = "sell"
    alerts: list = field(default_factory=list)

    @property
    def missing(self) -> list:
        return [k for k, p in self.params.items() if p["missing"]]

    def inputs(self) -> CostInputs:
        if self.missing:
            raise ValueError(f"no default for {', '.join(self.missing)}: " + " ".join(self.alerts))
        bumps = {k: p["value"] for k, p in self.params.items() if k in BUMPS}
        defaults = {k: p["default"] for k, p in self.params.items()}
        candidates = {k: p["conflict"] for k, p in self.params.items() if p["conflict"] and not p["overridden"]}
        mix = self.params["slv_mix"]["value"] if "slv_mix" in self.params else None
        delta = {kind: self.params[f]["value"] for kind, f in DELTA_FIELD.items() if f in self.params}
        spread = self.params["min_spread"]["value"] if "min_spread" in self.params else None
        return CostInputs(bumps, self.side, defaults, candidates, mix, spread, delta)

    def as_json(self) -> dict:
        return {
            "groups": self.groups,
            "tiers": self.tiers,
            "params": self.params,
            "side": self.side,
            "alerts": self.alerts,
            "missing": self.missing,
        }


def resolve(table: dict | None, sheet: TermSheet) -> Resolved:
    """What a sheet's costs are: the defaults of its groups and tier, with what the sheet overrides."""
    groups_table = merged(table)
    notional = sheet.get("general.notional")
    names = {n: instrument(n).group if _known(n) else "" for n in sheet.underlyings}
    tiers, by_group, alerts = {}, {}, []
    for name, g in names.items():
        group = groups_table.get(g)
        if group is None:
            alerts.append(f"{name} belongs to no group with defaults: enter the values.")
            tiers[g or name] = None
            continue
        if g in tiers:
            continue
        got = _tier(group, notional)
        if got is None:
            last = group["tiers"][-1]["max_notional"]
            alerts.append(
                f"A notional of {notional:,.0f} is above the last tier of {group['label']} ({last:,.0f} {group['currency']}): enter the values."
            )
            tiers[g] = None
        else:
            tiers[g] = {"index": got[0], "max_notional": got[1]["max_notional"]}
            by_group[g] = got[1]
    start = {
        g: STARTING[g]["tiers"][min(tiers[g]["index"], len(STARTING[g]["tiers"]) - 1)]
        for g in by_group
        if g in STARTING
    }
    complete = len(by_group) == len(tiers) and all(t is not None for t in tiers.values())
    params = {}
    for f in parameters(sheet):
        values = {g: t[f.name] for g, t in by_group.items() if f.name in t}
        override = sheet.get(f"costs.{f.name}")
        default, conflict = _prudent(f.name, values) if values and complete else (None, None)
        missing = override is None and default is None
        params[f.name] = {
            "value": override if override is not None else default,
            "default": default,
            "overridden": override is not None,
            "missing": missing,
            "by_group": values,
            "conflict": conflict,
            "starting": bool(values) and all(g in start and start[g].get(f.name) == v for g, v in values.items()),
        }
    return Resolved({n: g for n, g in names.items()}, tiers, params, sheet.get("costs.side", "sell"), alerts)


def _known(name: str) -> bool:
    try:
        instrument(name)
        return True
    except KeyError:
        return False
