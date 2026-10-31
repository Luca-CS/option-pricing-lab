"""Declarative term-sheet schema: sections of fields, product masks and validated term sheets.

A mask (product type) exposes some sections and restricts some choices; a field may be visible only for some
values of another field (`visible_if`). `describe()` exports the visible form, so a front end can build its
inputs from the schema instead of duplicating the rules. Levels are fractions of the initial fixing (1.0 = 100 %),
rates are fractions per annum.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # number, int, bool, choice, date, names (tuple of underlying names)
    default: Any
    label: str
    unit: str = ""
    choices: tuple = ()
    visible_if: tuple = ()  # ((dotted field, allowed values), ...), all must hold
    minimum: float | None = None
    maximum: float | None = None
    derived: bool = False  # read-only, computed from other fields


@dataclass(frozen=True)
class Section:
    name: str
    label: str
    fields: tuple

    def field(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)


LEVEL_MAX = 3.0  # levels are fractions of the initial fixing: 3.0 = 300 %
FRACTIONS = "levels and rates are fractions: 0.6 = 60 %, 0.08 = 8 % p.a."


def _v(path: str, *values) -> tuple:
    return ((path, values),)


SECTIONS = {s.name: s for s in [
    Section("general", "General", (
        Field("basket", "choice", "single", "Underlying type", choices=("single", "worst_of")),
        Field("underlying", "choice", "SPX", "Underlying", choices=("SPX",),
              visible_if=_v("general.basket", "single")),
        Field("underlyings", "names", ("SPX", "SX5E"), "Basket (worst-of)",
              visible_if=_v("general.basket", "worst_of")),
        Field("notional", "number", 100.0, "Notional", minimum=0.0),
        Field("strike_date", "date", None, "Strike date (default: valuation date)"),
        Field("maturity_months", "int", 24, "Maturity", "months", minimum=1),
        Field("pay_lag", "int", 5, "Payment lag", "business days", minimum=0),
        Field("convention", "choice", "modified_following", "Roll convention",
              choices=("modified_following", "following", "preceding", "unadjusted")),
    )),
    Section("wrapper", "Wrapper", (
        Field("format", "choice", "note", "Format", choices=("note", "swap")),
        Field("issuer_spread", "number", 0.0, "Issuer funding spread", "per annum", minimum=-0.05, maximum=0.2,
              visible_if=_v("wrapper.format", "note")),
        Field("funding", "choice", "none", "Funding leg", choices=("none", "floating"),
              visible_if=_v("wrapper.format", "swap")),
        Field("funding_spread", "number", 0.0, "Funding spread", "per annum", minimum=-0.05, maximum=0.2,
              visible_if=(("wrapper.format", ("swap",)), ("wrapper.funding", ("floating",)))),
        Field("funding_frequency_months", "int", 3, "Funding frequency", "months", minimum=1,
              visible_if=(("wrapper.format", ("swap",)), ("wrapper.funding", ("floating",)))),
        Field("solve", "choice", "coupon", "Solve for", choices=("coupon", "upfront")),
        Field("margin", "number", 0.02, "Margin", "of notional", minimum=-0.5, maximum=0.5),
    )),
    Section("autocall", "Autocall", (
        Field("frequency_months", "int", 3, "Call frequency", "months", minimum=1),
        Field("trigger", "number", 1.0, "Autocall trigger", "of initial", minimum=0.0, maximum=LEVEL_MAX),
        Field("step_down", "number", 0.0, "Step-down per call date", "of initial", minimum=0.0, maximum=1.0),
        Field("first_call", "int", 1, "First call date (non-call periods + 1)", minimum=1),
    )),
    Section("coupon", "Coupon", (
        Field("type", "choice", "fixed", "Coupon type", choices=("none", "fixed", "conditional", "snowball")),
        Field("rate", "number", 0.08, "Coupon rate", "per annum", minimum=0.0, maximum=1.0,
              visible_if=_v("coupon.type", "fixed", "conditional", "snowball")),
        Field("frequency_months", "int", 3, "Coupon frequency", "months", minimum=1,
              visible_if=_v("coupon.type", "fixed", "conditional")),
        Field("barrier", "number", 0.7, "Coupon barrier", "of initial", minimum=0.0, maximum=LEVEL_MAX,
              visible_if=_v("coupon.type", "conditional")),
        Field("memory", "bool", True, "Memory", visible_if=_v("coupon.type", "conditional")),
    )),
    Section("upside", "Upside", (
        Field("type", "choice", "none", "Upside", choices=("none", "call", "call_spread", "digital")),
        Field("strike", "number", 1.0, "Strike", "of initial", minimum=0.0, maximum=LEVEL_MAX,
              visible_if=_v("upside.type", "call", "call_spread", "digital")),
        Field("participation", "number", 1.0, "Participation", minimum=0.0, maximum=10.0,
              visible_if=_v("upside.type", "call", "call_spread")),
        Field("cap", "number", 1.3, "Cap", "of initial", minimum=0.0, maximum=LEVEL_MAX,
              visible_if=_v("upside.type", "call_spread")),
        Field("digital_amount", "number", 0.1, "Digital amount", "of notional", minimum=0.0, maximum=1.0,
              visible_if=_v("upside.type", "digital")),
    )),
    Section("downside", "Downside", (
        Field("type", "choice", "pdi", "Downside", choices=("none", "pdi", "geared_put")),
        Field("strike", "number", 1.0, "Put strike", "of initial", minimum=0.0, maximum=2.0,
              visible_if=_v("downside.type", "pdi", "geared_put")),
        Field("barrier", "number", 0.6, "Knock-in barrier", "of initial", minimum=0.0, maximum=LEVEL_MAX,
              visible_if=_v("downside.type", "pdi")),
        Field("observation", "choice", "european", "Barrier observation", choices=("european", "daily"),
              visible_if=_v("downside.type", "pdi")),
        Field("gearing", "number", None, "Gearing (1 / strike)", derived=True,
              visible_if=_v("downside.type", "geared_put")),
    )),
]}


@dataclass(frozen=True)
class Mask:
    name: str
    label: str
    sections: tuple
    choices: dict = field(default_factory=dict)  # dotted field → allowed choices under this mask


MASKS = {
    "autocall": Mask("autocall", "Autocallable", ("general", "wrapper", "autocall", "coupon", "downside"),
                     {"coupon.type": ("fixed", "conditional", "snowball")}),
    "bullet": Mask("bullet", "Bullet (non-callable)", ("general", "wrapper", "coupon", "upside", "downside"),
                   {"coupon.type": ("none", "fixed", "conditional")}),
}


class TermSheetError(ValueError):
    pass


def _hint(f: Field) -> str:
    return f" ({FRACTIONS})" if f.unit in ("of initial", "per annum", "of notional") else ""


def _split(path: str) -> tuple[str, str]:
    parts = path.split(".")
    if len(parts) != 2 or not all(parts):
        raise TermSheetError(f"{path!r} is not a field path: use 'section.field', e.g. 'coupon.rate'")
    return parts[0], parts[1]


def _coerce(f: Field, value, path: str):
    if value is None:
        return None
    if f.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TermSheetError(f"{path} must be a number, got {value!r}")
        value = float(value)
    elif f.kind == "int":
        if isinstance(value, bool) or not float(value).is_integer():
            raise TermSheetError(f"{path} must be an integer, got {value!r}")
        value = int(value)
    elif f.kind == "bool":
        if not isinstance(value, bool):
            raise TermSheetError(f"{path} must be true or false, got {value!r}")
    elif f.kind == "date":
        value = dt.date.fromisoformat(value) if isinstance(value, str) else value
    elif f.kind == "names":
        value = tuple(value)
        if not 2 <= len(value) <= 5 or len(set(value)) != len(value):
            raise TermSheetError(f"{path} needs 2 to 5 distinct names, got {value!r}")
        if not all(isinstance(n, str) and n.isidentifier() for n in value):
            raise TermSheetError(f"{path}: names must be identifiers (letters, digits, _), got {value!r}")
        return value
    if f.minimum is not None and value < f.minimum:
        raise TermSheetError(f"{path} = {value} is below the minimum {f.minimum}" + _hint(f))
    if f.maximum is not None and value > f.maximum:
        raise TermSheetError(f"{path} = {value} is above the maximum {f.maximum}" + _hint(f))
    return value


class TermSheet:
    """Validated values of the visible fields of a mask, as {section: {field: value}}."""

    def __init__(self, mask: str, values: dict | None = None, strict: bool = True):
        if mask not in MASKS:
            raise TermSheetError(f"unknown product type {mask!r}, expected one of {', '.join(MASKS)}")
        self.mask = MASKS[mask]
        given = {s: dict(v) for s, v in (values or {}).items()}
        for s in given:
            if s not in self.mask.sections:
                raise TermSheetError(f"section {s!r} is not part of the {mask} mask "
                                     f"(sections: {', '.join(self.mask.sections)})")
        self.values: dict = {}
        # defaults first, so that visibility can be evaluated on the merged values
        for s in self.mask.sections:
            sec = SECTIONS[s]
            unknown = set(given.get(s, {})) - {f.name for f in sec.fields}
            if unknown:
                raise TermSheetError(f"unknown field(s) in {s}: {', '.join(sorted(unknown))}")
            self.values[s] = {f.name: given.get(s, {}).get(f.name, f.default) for f in sec.fields}
        for s in self.mask.sections:
            for f in SECTIONS[s].fields:
                path = f"{s}.{f.name}"
                visible = self.visible(path)
                if not visible:
                    if strict and f.name in given.get(s, {}) and given[s][f.name] != f.default:
                        cond = ", ".join(f"{p} in {v}" for p, v in f.visible_if)
                        raise TermSheetError(f"{path} is not used here (it needs {cond})")
                    del self.values[s][f.name]
                    continue
                if f.derived:
                    if f.name in given.get(s, {}):
                        raise TermSheetError(f"{path} is computed, it cannot be set")
                    continue
                v = _coerce(f, self.values[s][f.name], path)
                allowed = self.mask.choices.get(path, f.choices)
                if f.kind == "choice" and v not in allowed:
                    raise TermSheetError(f"{path} = {v!r} is not allowed for the {self.mask.name} mask "
                                         f"(choices: {', '.join(allowed)})")
                self.values[s][f.name] = v
        if self.visible("downside.gearing"):
            self.values["downside"]["gearing"] = 1.0 / self.values["downside"]["strike"]
        self._check()

    def visible(self, path: str) -> bool:
        s, name = _split(path)
        if s not in self.mask.sections:
            return False
        f = SECTIONS[s].field(name)
        for dep, allowed in f.visible_if:
            ds, dn = dep.split(".")
            if ds not in self.values or self.values[ds].get(dn) not in allowed:
                return False
        return True

    def get(self, path: str, default=None):
        s, name = _split(path)
        return self.values.get(s, {}).get(name, default)

    def _check(self):
        m = self.get("general.maturity_months")
        for freq in ("coupon.frequency_months", "autocall.frequency_months", "wrapper.funding_frequency_months"):
            f = self.get(freq)
            if f is not None and m % f:
                raise TermSheetError(f"the maturity ({m} months) must be a multiple of {freq} ({f} months)")
        if self.get("downside.type") == "pdi" and self.get("downside.barrier") > self.get("downside.strike"):
            raise TermSheetError("the knock-in barrier must be at or below the put strike")
        if self.get("downside.type") == "geared_put" and self.get("downside.strike") <= 0:
            raise TermSheetError("the geared put needs a positive strike")
        if self.get("upside.type") == "call_spread" and self.get("upside.cap") <= self.get("upside.strike"):
            raise TermSheetError("the cap must be above the call strike")
        if self.get("wrapper.solve") == "coupon" and self.get("coupon.type", "none") == "none":
            raise TermSheetError("there is no coupon to solve for: use wrapper.solve = 'upfront'")
        if self.mask.name == "autocall":
            n_calls = m // self.get("autocall.frequency_months")
            if self.get("autocall.first_call") > n_calls:
                raise TermSheetError(f"first_call = {self.get('autocall.first_call')} is after the last "
                                     f"observation ({n_calls})")

    def replace(self, **changes) -> "TermSheet":
        """Copy with dotted-path changes, e.g. replace(**{"coupon.rate": 0.1}). Fields that the changes hide are
        dropped; changing a field that ends up hidden is an error."""
        values = {s: dict(v) for s, v in self.values.items()}
        for path, v in changes.items():
            s, name = _split(path)
            values.setdefault(s, {})[name] = v
        for s in values:
            values[s].pop("gearing", None)
        new = TermSheet(self.mask.name, values, strict=False)
        for path in changes:
            if not new.visible(path):
                raise TermSheetError(f"{path} is not used by this term sheet")
        return new

    @property
    def underlyings(self) -> tuple:
        if self.get("general.basket") == "worst_of":
            return self.get("general.underlyings")
        return (self.get("general.underlying"),)

    def to_dict(self, json_safe: bool = False) -> dict:
        """{"type": mask, section: {field: value}}; with json_safe, dates are ISO strings and tuples lists."""
        out = {"type": self.mask.name, **{s: dict(v) for s, v in self.values.items()}}
        if json_safe:
            out = {k: ({f: _json(x) for f, x in v.items()} if isinstance(v, dict) else v) for k, v in out.items()}
        return out

    @classmethod
    def from_dict(cls, d: dict, strict: bool = True) -> "TermSheet":
        """Inverse of to_dict: computed fields (the gearing) are dropped, ISO dates and lists are accepted."""
        d = dict(d)
        if "type" not in d:
            raise TermSheetError("a term-sheet dict needs its product type under 'type'")
        mask = d.pop("type")
        values = {}
        for section, fields in d.items():
            if section not in SECTIONS:
                raise TermSheetError(f"unknown section {section!r}")
            spec = {f.name: f for f in SECTIONS[section].fields}
            values[section] = {}
            for k, v in fields.items():
                if k in spec and spec[k].derived:
                    continue
                if k in spec and spec[k].kind == "names" and isinstance(v, list):
                    v = tuple(v)
                values[section][k] = v
        return cls(mask, values, strict)

    def __repr__(self) -> str:
        return f"TermSheet({self.mask.name!r}, {self.values!r})"


def _json(v):
    if isinstance(v, tuple):
        return list(v)
    return v.isoformat() if isinstance(v, dt.date) else v


def json_schema() -> dict:
    """JSON Schema (2020-12) of a term-sheet dict: one branch per mask, enums and bounds from the field list.
    The visibility rules are listed per field under "x-visible-if"; the TermSheet constructor enforces them."""
    kinds = {"number": "number", "int": "integer", "bool": "boolean", "choice": "string", "date": "string",
             "names": "array"}
    branches = []
    for mask in MASKS.values():
        props: dict = {"type": {"const": mask.name}}
        for sname in mask.sections:
            fields = {}
            for f in SECTIONS[sname].fields:
                if f.derived:
                    continue
                path = f"{sname}.{f.name}"
                spec: dict = {"type": kinds[f.kind], "description": f.label + (f" ({f.unit})" if f.unit else "")}
                if f.kind == "choice":
                    spec["enum"] = list(mask.choices.get(path, f.choices))
                if f.kind == "date":
                    spec["format"] = "date"
                if f.kind == "names":
                    spec.update(items={"type": "string"}, minItems=2, maxItems=5, uniqueItems=True)
                if f.minimum is not None:
                    spec["minimum"] = f.minimum
                if f.maximum is not None:
                    spec["maximum"] = f.maximum
                if f.default is not None:
                    spec["default"] = _json(f.default)
                if f.visible_if:
                    spec["x-visible-if"] = {dep: list(v) for dep, v in f.visible_if}
                fields[f.name] = spec
            props[sname] = {"type": "object", "properties": fields, "additionalProperties": False}
        branches.append({"type": "object", "properties": props, "required": ["type"], "additionalProperties": False})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "optlab structured term sheet",
            "oneOf": branches}


def describe(mask: str, sheet: TermSheet | None = None) -> dict:
    """The visible form of a mask: sections, fields with kind, unit, choices and current value."""
    sheet = sheet or TermSheet(mask)
    out = {"type": mask, "label": MASKS[mask].label, "sections": []}
    for s in MASKS[mask].sections:
        fields = []
        for f in SECTIONS[s].fields:
            path = f"{s}.{f.name}"
            if not sheet.visible(path):
                continue
            fields.append({"name": f.name, "path": path, "label": f.label, "kind": f.kind, "unit": f.unit,
                           "choices": list(MASKS[mask].choices.get(path, f.choices)), "value": _json(sheet.get(path)),
                           "read_only": f.derived})
        out["sections"].append({"name": s, "label": SECTIONS[s].label, "fields": fields})
    return out
