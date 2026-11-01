"""The desk behind the web page: every API route is a method here, JSON in and JSON out, no HTTP inside.

Levels and rates cross the API as fractions (0.6 = 60 %, 0.08 = 8 % p.a.), exactly as in the library; the page
shows and takes percent and converts at its edge.
"""

from __future__ import annotations

import datetime as dt
import json
import re

import numpy as np

from ..config import paths
from ..structured import PRESETS, MASKS, PricingContext, TermSheet, TermSheetError, describe, preset, quote
from ..structured.compiler import compile_sheet
from ..structured.lifecycle import events
from ..structured.portfolio import Book
from ..structured.pricer import price
from ..structured.schema import SECTIONS, json_schema
from ..structured.script.lexer import ScriptError
from ..structured.script.parser import parse
from ..structured.script.validate import validate
from ..structured.trade import Trade
from .jobs import Jobs
from .repository import Repository
from .serialize import book_json, clean, events_json, quote_json, trade_json

PRECISION = {"quick": (20_000, 10_000), "standard": (100_000, 25_000), "precise": (200_000, 25_000)}
FIELD = re.compile(r"^([a-z_]+\.[a-z_]+)\b")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, field: str | None = None):
        super().__init__(message)
        self.status, self.field = status, field


def _sheet(body: dict, strict: bool = False) -> TermSheet:
    try:  # from_dict drops computed fields (the gearing) that a sheet read back from the API carries
        values = {s: dict(v) for s, v in (body.get("values") or {}).items()}
        return TermSheet.from_dict({"type": body.get("type", "autocall"), **values}, strict=strict)
    except TermSheetError as exc:
        m = FIELD.match(str(exc))
        raise ApiError(str(exc), 422, m.group(1) if m else None)
    except (TypeError, ValueError) as exc:
        raise ApiError(str(exc), 422)


class Desk:
    def __init__(self, store=None, calibration=None, workers: int | None = None, max_jobs: int = 2):
        self.ctx = PricingContext.from_calibration(calibration)
        self.calibration = calibration
        self.repo = Repository(store)
        self.jobs = Jobs(max_jobs)
        self.workers = workers
        self.started = dt.datetime.now(dt.timezone.utc)
        self.last_book = None

    # --- status and market ---
    def health(self) -> dict:
        return {"ok": True, "snapshot_id": self.ctx.snapshot_id, "valuation": self.ctx.valuation.isoformat(),
                "workers": self.workers, "jobs": self.jobs.running(), "store": str(self.repo.path),
                "started": self.started.isoformat(timespec="seconds")}

    def context(self) -> dict:
        c = self.ctx
        tenors = [0.25, 0.5, 1.0, 1.5, 2.0]
        return clean({"snapshot_id": c.snapshot_id, "valuation": c.valuation, "underlying": c.underlying,
                      "spot": c.market.spot, "t_max": c.t_max, "maturities": c.surface.maturities,
                      "atm_vols": {f"{T:g}": c.atm_vol(T) for T in tenors},
                      "zero_rates": {f"{T:g}": float(c.market.zero_rate(T)) for T in tenors},
                      "carry": {f"{T:g}": float(c.market.carry_rate(T)) for T in tenors},
                      "slv_mix": c.slv.mix, "heston": c.heston.to_dict()})

    def smile(self, T: float) -> dict:
        T = float(T)
        F = float(self.ctx.market.F(T))
        k = np.linspace(-0.6, 0.3, 61)
        return clean({"T": T, "forward": F, "moneyness": np.exp(k), "iv": self.ctx.surface.iv(k, T)})

    def term_structure(self) -> dict:
        T = np.linspace(0.05, self.ctx.t_max, 40)
        return clean({"T": T, "atm_vol": [self.ctx.atm_vol(t) for t in T],
                      "forward": self.ctx.market.F(T) / self.ctx.market.spot, "discount": self.ctx.market.D(T)})

    def model_risk(self) -> dict:
        path = self.calibration or (paths().results / "spx_calibration.json")
        cal = json.loads(open(path, encoding="utf-8").read())
        return clean({"exotics": cal.get("exotics", {}), "errors": cal.get("errors", []),
                      "note": cal.get("meta", {}).get("note")})

    # --- term sheets ---
    def presets(self) -> dict:
        out = []
        for name, (mask, _) in PRESETS.items():
            out.append({"name": name, "type": mask, "label": name.replace("_", " ").title(),
                        "sheet": preset(name).to_dict(json_safe=True)})
        return {"presets": out, "masks": {k: m.label for k, m in MASKS.items()}}

    def form(self, body: dict) -> dict:
        """A (partial) term sheet: the validated sheet and its visible form, or the first error with its field."""
        mask = body.get("type", "autocall")
        if mask not in MASKS:
            raise ApiError(f"unknown product type {mask!r}", 422)
        error, sheet, near = None, None, None
        try:
            sheet = near = _sheet(body)
        except ApiError as exc:
            error = {"message": str(exc), "field": exc.field}
            # The form follows the sheet without the faulty fields (their defaults instead), not the mask defaults:
            # otherwise a conditional coupon with a bad barrier would show as fixed and hide the field to correct.
            values = {s: dict(v) for s, v in (body.get("values") or {}).items()}
            field = exc.field
            for _ in range(8):
                if not field:
                    break
                s, f = field.split(".")
                values.get(s, {}).pop(f, None)
                try:
                    near = _sheet({"type": mask, "values": values})
                    break
                except ApiError as again:
                    field = again.field
        shown = describe(mask, near) if near is not None else describe(mask)
        bounds = {}
        for s in MASKS[mask].sections:
            for f in SECTIONS[s].fields:
                bounds[f"{s}.{f.name}"] = {"min": f.minimum, "max": f.maximum, "default": f.default}
        return clean({"type": mask, "sheet": sheet.to_dict(json_safe=True) if sheet else None, "form": shown,
                      "bounds": bounds, "error": error})

    def schema(self) -> dict:
        return json_schema()

    # --- quotes ---
    def _options(self, opts: dict) -> dict:
        n_paths, batch = PRECISION.get(opts.get("precision", "standard"), PRECISION["standard"])
        return {"n_paths": int(opts.get("n_paths") or n_paths), "batch": batch,
                "control": "default" if opts.get("control", True) else None,
                "with_greeks": bool(opts.get("greeks", False)), "fast": bool(opts.get("fast", False)),
                "slv_vega": bool(opts.get("slv_vega", False)),
                "control_forwards": bool(opts.get("control_forwards", False)),
                "model": opts.get("model") or None, "sticky": opts.get("sticky", "moneyness"),
                "mix_band": (0.0, 0.25, 0.5, 0.75) if opts.get("mix_band") else None,
                "control_paths": min(int(opts.get("n_paths") or n_paths), 100_000)}

    def quote(self, body: dict) -> dict:
        sheet = _sheet(body.get("sheet") or {}, strict=True)
        fixings = self._fixings_for(sheet)
        o = self._options(body.get("options") or {})

        def run():
            q = quote(sheet, self.ctx, model=o["model"], control=o["control"], n_paths=o["n_paths"],
                      batch=o["batch"], control_paths=o["control_paths"], fixings=fixings,
                      with_greeks=o["with_greeks"], slv_vega=o["slv_vega"], workers=self.workers,
                      control_forwards=o["control_forwards"],
                      fast=o["fast"], sticky=o["sticky"], mix_band=o["mix_band"])
            out = quote_json(q)
            out["sheet"] = sheet.to_dict(json_safe=True)
            out["options"] = clean(o)
            out["quote_id"] = self.repo.save_quote(out["sheet"], out)
            return out

        return {"job": self.jobs.submit("quote", run)}

    def _fixings_for(self, sheet: TermSheet) -> dict | None:
        strike = sheet.get("general.strike_date")
        if strike is None or strike >= self.ctx.valuation:
            return None
        stored = self.repo.fixings()
        return {n: stored[n] for n in sheet.underlyings if n in stored} or None

    def job(self, job_id: str) -> dict:
        try:
            return clean(self.jobs.get(job_id))
        except KeyError as exc:
            raise ApiError(str(exc), 404)

    def cancel(self, job_id: str) -> dict:
        self.jobs.cancel(job_id)
        return {"ok": True}

    def quotes(self) -> dict:
        return {"quotes": self.repo.quotes()}

    def saved_quote(self, quote_id: str) -> dict:
        try:
            return self.repo.quote(quote_id)
        except KeyError as exc:
            raise ApiError(str(exc), 404)

    # --- trades and book ---
    def trades(self) -> dict:
        return {"trades": [trade_json(t) for t in self.repo.trades()]}

    def create_trade(self, body: dict) -> dict:
        sheet = _sheet(body.get("sheet") or {}, strict=True)
        rate = body.get("coupon_rate")
        if rate is not None and sheet.get("coupon.type") not in (None, "none"):
            sheet = sheet.replace(**{"coupon.rate": float(rate), "wrapper.solve": "upfront"})
        elif sheet.get("wrapper.solve") == "coupon":
            sheet = sheet.replace(**{"wrapper.solve": "upfront"})
        trade_date = dt.date.fromisoformat(body.get("trade_date") or self.ctx.valuation.isoformat())
        trade = Trade.from_term_sheet(body.get("trade_id") or self.repo.new_trade_id(sheet.mask.name[:3].upper()),
                                      sheet, float(body.get("quantity", 1.0)), trade_date, body.get("strike_date"),
                                      book=body.get("book", "default"), counterparty=body.get("counterparty", ""),
                                      tags=tuple(body.get("tags", ())))
        return trade_json(self.repo.save_trade(trade))

    def update_trade(self, trade_id: str, body: dict) -> dict:
        old = self.repo.trade(trade_id)
        changes = {k: body[k] for k in ("quantity", "status", "book", "counterparty") if k in body}
        d = json.loads(old.to_json()) | changes
        d["version"] = old.version
        return trade_json(self.repo.save_trade(Trade(**d)))

    def delete_trade(self, trade_id: str) -> dict:
        self.repo.delete_trade(trade_id)
        return {"ok": True, "undo": f"/api/trades/{trade_id}/restore"}

    def restore_trade(self, trade_id: str) -> dict:
        self.repo.restore_trade(trade_id)
        return {"ok": True}

    def price_book(self, body: dict) -> dict:
        o = self._options(body.get("options") or {})
        greeks = bool((body.get("options") or {}).get("greeks", True))
        live = [t for t in self.repo.trades() if t.status == "live"]
        stored = self.repo.fixings()
        positions, skipped = [], []
        for t in live:
            sheet = t.term_sheet()
            if sheet is not None and len(sheet.underlyings) > 1:
                skipped.append({"trade_id": t.trade_id, "reason": "worst-of: needs a basket context"})
                continue
            fix = None
            if t.strike_date < self.ctx.valuation and sheet is not None:
                fix = {n: stored[n] for n in sheet.underlyings if n in stored} or None
            positions.append(t.to_position(fix))
        if not positions:
            raise ApiError("no live single-name trade in the book", 422)

        def run():
            book = Book(positions)
            res = book.price(self.ctx, model=o["model"], n_paths=o["n_paths"], batch=o["batch"],
                             workers=self.workers)
            out = book_json(res)
            if greeks:
                g = book.greeks(self.ctx, model=o["model"], n_paths=o["n_paths"], batch=o["batch"],
                                slv_vega=o["slv_vega"], workers=self.workers)
                out["greeks"] = clean({"delta": g["delta"], "vega": g.get("vega"), "note": g.get("note"),
                                       "units": g["units"], "model": g["model"]})
            out["skipped"] = skipped
            self.last_book = out
            return out

        return {"job": self.jobs.submit("book", run)}

    # --- fixings and lifecycle ---
    def fixings(self, underlying: str | None = None) -> dict:
        return clean({"fixings": self.repo.fixings(underlying)})

    def put_fixings(self, body: dict) -> dict:
        und = body.get("underlying", "SPX")
        levels = body.get("levels") or {}
        scale = float(body.get("real_spot") or 0)
        if scale:  # real index levels: divide by real spot / model spot (the market is rescaled to 100)
            ratio = scale / self.ctx.market.spot
            levels = {d: float(v) / ratio for d, v in levels.items()}
        for d in levels:
            dt.date.fromisoformat(str(d))
            if dt.date.fromisoformat(str(d)) > self.ctx.valuation:
                raise ApiError(f"fixing on {d} is after the valuation date {self.ctx.valuation}", 422)
        n = self.repo.put_fixings(und, levels, body.get("source", "manual"))
        return {"ok": True, "stored": n}

    def delete_fixing(self, underlying: str, date: str) -> dict:
        self.repo.delete_fixing(underlying, date)
        return {"ok": True}

    def trade_events(self, trade_id: str) -> dict:
        trade = self.repo.trade(trade_id)
        sheet = trade.term_sheet()
        compiled = compile_sheet(sheet, self.ctx.valuation)
        upcoming = [{"observation": ev.when.value, "payment": ev.pay.value if ev.pay else None}
                    for ev in compiled.program.events[1:]
                    if dt.date.fromisoformat(ev.when.value) > self.ctx.valuation]
        past, missing = [], None
        if trade.strike_date < self.ctx.valuation:
            try:
                past = events_json(events(trade, self.repo.fixings(), self.ctx.valuation))
            except KeyError as exc:  # names the dates, or only the underlying when it has no fixing at all
                missing = str(exc).strip("'\"")
                if missing in sheet.underlyings:
                    missing = f"no fixing of {missing}"
        return clean({"trade_id": trade_id, "past": past, "upcoming": upcoming, "missing": missing,
                      "maturity": compiled.maturity, "strike_date": compiled.strike_date})

    def upcoming(self, days: int = 45) -> dict:
        horizon = self.ctx.valuation + dt.timedelta(days=int(days))
        rows = []
        for t in self.repo.trades():
            if t.status != "live" or t.term_sheet() is None:
                continue
            compiled = compile_sheet(t.term_sheet(), self.ctx.valuation)
            for ev in compiled.program.events[1:]:
                d = dt.date.fromisoformat(ev.when.value)
                if self.ctx.valuation <= d <= horizon:
                    rows.append({"trade_id": t.trade_id, "observation": ev.when.value,
                                 "payment": ev.pay.value if ev.pay else None,
                                 "final": ev is compiled.program.events[-1]})
        return {"events": sorted(rows, key=lambda r: r["observation"]), "until": horizon.isoformat()}

    # --- script lab ---
    def script_check(self, body: dict) -> dict:
        try:
            program = validate(parse(body.get("script", "")))
        except ScriptError as exc:
            return {"ok": False, "message": exc.message, "line": exc.line, "col": exc.col}
        return {"ok": True, "underlyings": program.underlying_names, "params": program.param_values,
                "events": len(program.events)}

    def script_price(self, body: dict) -> dict:
        check = self.script_check(body)
        if not check["ok"]:
            raise ApiError(f"line {check['line']}, col {check['col']}: {check['message']}", 422)
        model = (body.get("model") or "lv").lower()
        n_paths = int(body.get("n_paths") or 40_000)

        def run():
            res = price(body["script"], self.ctx.model(model, 2.0), self.ctx.market, n_paths=n_paths,
                        seed=int(body.get("seed", 2026)), batch=min(n_paths, 25_000), valuation=self.ctx.valuation,
                        workers=self.workers)
            return clean({"price": res.price, "stderr": res.stderr, "seconds": res.seconds, "model": model,
                          "n_paths": n_paths, "expected_life": res.expected_life, "warnings": res.warnings,
                          "notional": res.notional})

        return {"job": self.jobs.submit("script", run)}
