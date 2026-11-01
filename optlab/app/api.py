"""The desk behind the web page: every API route is a method here, JSON in and JSON out, no HTTP inside.

Levels and rates cross the API as fractions (0.6 = 60 %, 0.08 = 8 % p.a.), exactly as in the library; the page
shows and takes percent and converts at its edge. Every job captures the market context when it is submitted, so
switching snapshots never changes a run in progress.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import numpy as np

from ..config import DEFAULT_SEED, paths
from ..log import get_logger
from ..marketdata import MarketSnapshot, MarketStore
from ..structured import PRESETS, MASKS, PricingContext, TermSheet, TermSheetError, describe, preset, quote
from ..structured.compiler import compile_sheet
from ..structured.context import DEFAULT_CALIBRATION
from ..structured.lifecycle import events
from ..structured.multiasset import BasketContext
from ..structured.portfolio import Book
from ..structured.pricer import price
from ..structured.schema import SECTIONS, json_schema
from ..structured.script.lexer import ScriptError
from ..structured.script.parser import parse
from ..structured.script.validate import validate
from ..structured.trade import Trade
from .jobs import Jobs
from .repository import Repository
from .serialize import book_json, clean, events_json, quote_json, result_json, trade_json

PRECISION = {"quick": (20_000, 10_000), "standard": (100_000, 25_000), "precise": (200_000, 25_000)}
FIELD = re.compile(r"^([a-z_]+\.[a-z_]+)\b")
# Names other than the calibrated one: flat Black-Scholes markets rescaled to 100, with these defaults until the
# user sets them (Market screen). The rate defaults to the calibrated 1-year zero rate.
BASKET_DEFAULTS = {"vol": 0.20, "q": 0.02, "corr": 0.70}
LOG = get_logger(__name__)


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


def _pair(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


class Desk:
    def __init__(self, store=None, calibration=None, workers: int | None = None, max_jobs: int = 2):
        self.repo = Repository(store)
        self.market_store = MarketStore(self.repo.path.parent / "market")
        self.calibration = Path(calibration) if calibration else DEFAULT_CALIBRATION
        self.ctx = PricingContext.from_calibration(self.calibration)
        self.files: dict[str, Path] = {}  # snapshot id -> calibration file (its model-risk table)
        self._register(self.calibration)
        for path in sorted(paths().results.glob("*_calibration.json")):
            if path.resolve() != self.calibration.resolve():
                self._register(path)
        self.jobs = Jobs(max_jobs)
        self.workers = workers
        self.started = dt.datetime.now(dt.timezone.utc)
        self.last_book = None
        self.public_url = None  # set when a tunnel is open

    def _register(self, path: Path) -> None:
        """Keep a calibration file's market in the snapshot store (immutable, by content), so the desk can switch
        to it later; files without a snapshot time (the simulated market) take the current valuation date."""
        try:
            try:
                snap = MarketSnapshot.from_calibration_file(path, provenance=False)
            except ValueError:
                snap = MarketSnapshot.from_calibration_file(path, valuation=self.ctx.valuation, source="simulated",
                                                            provenance=False)
            self.files[self.market_store.put(snap)] = Path(path)
        except (OSError, KeyError, ValueError) as exc:
            LOG.warning("calibration %s not registered: %s", path, exc)

    # --- status and market ---
    def health(self) -> dict:
        return {"ok": True, "snapshot_id": self.ctx.snapshot_id, "valuation": self.ctx.valuation.isoformat(),
                "workers": self.workers, "jobs": self.jobs.running(), "store": str(self.repo.path),
                "started": self.started.isoformat(timespec="seconds"), "public_url": self.public_url}

    def context(self) -> dict:
        c = self.ctx
        tenors = [0.25, 0.5, 1.0, 1.5, 2.0]
        return clean({"snapshot_id": c.snapshot_id, "valuation": c.valuation, "underlying": c.underlying,
                      "spot": c.market.spot, "t_max": c.t_max, "maturities": c.surface.maturities,
                      "atm_vols": {f"{T:g}": c.atm_vol(T) for T in tenors},
                      "zero_rates": {f"{T:g}": float(c.market.zero_rate(T)) for T in tenors},
                      "carry": {f"{T:g}": float(c.market.carry_rate(T)) for T in tenors},
                      "slv_mix": c.slv.mix, "heston": c.heston.to_dict()})

    def snapshots(self) -> dict:
        rows = self.market_store.list()
        for r in rows:
            r["active"] = r["snapshot_id"] == self.ctx.snapshot_id
            r["file"] = self.files[r["snapshot_id"]].name if r["snapshot_id"] in self.files else None
        return {"snapshots": sorted(rows, key=lambda r: (r["valuation"], r["asof"]), reverse=True),
                "active": self.ctx.snapshot_id}

    def use_snapshot(self, body: dict) -> dict:
        sid = body.get("snapshot_id")
        try:
            snap = self.market_store.get(sid)
        except KeyError as exc:
            raise ApiError(str(exc).strip("'\""), 404)
        self.ctx = PricingContext.from_snapshot(snap)
        self.last_book = None  # valued on another market
        LOG.info("desk now on market %s (valuation %s)", self.ctx.snapshot_id, self.ctx.valuation)
        return self.context()

    def smile(self, T: float) -> dict:
        ctx, T = self.ctx, float(T)
        F = float(ctx.market.F(T))
        k = np.linspace(-0.6, 0.3, 61)
        return clean({"T": T, "forward": F, "moneyness": np.exp(k), "iv": ctx.surface.iv(k, T)})

    def term_structure(self) -> dict:
        ctx = self.ctx
        T = np.linspace(0.05, ctx.t_max, 40)
        return clean({"T": T, "atm_vol": [ctx.atm_vol(t) for t in T],
                      "forward": ctx.market.F(T) / ctx.market.spot, "discount": ctx.market.D(T)})

    def model_risk(self) -> dict:
        path = self.files.get(self.ctx.snapshot_id)
        if path is None or not path.exists():
            return {"exotics": {}, "errors": [], "note": "no model-risk table stored with this snapshot"}
        cal = json.loads(path.read_text(encoding="utf-8"))
        return clean({"exotics": cal.get("exotics", {}), "errors": cal.get("errors", []),
                      "note": cal.get("meta", {}).get("note")})

    # --- other underlyings, for worst-ofs ---
    def basket_settings(self) -> dict:
        s = self.repo.setting("basket", {})
        r = float(self.ctx.market.zero_rate(1.0))
        return clean({"names": s.get("names", {}), "corr": s.get("corr", {}), "calibrated": self.ctx.underlying,
                      "defaults": {**BASKET_DEFAULTS, "r": r}})

    def put_basket(self, body: dict) -> dict:
        names, corr = {}, {}
        try:
            for n, p in (body.get("names") or {}).items():
                n = str(n).strip().upper()
                if not re.fullmatch(r"[A-Z0-9._]{1,12}", n) or n == self.ctx.underlying:
                    raise ApiError(f"{n!r}: a name of 1 to 12 letters or digits, not the calibrated one", 422)
                vol, q = float(p.get("vol", BASKET_DEFAULTS["vol"])), float(p.get("q", BASKET_DEFAULTS["q"]))
                r = None if p.get("r") in (None, "") else float(p["r"])
                if not 0.01 <= vol <= 2.0:
                    raise ApiError(f"{n}: volatility {vol:.0%} outside 1 % to 200 %", 422)
                if not -0.1 <= q <= 0.2 or (r is not None and not -0.1 <= r <= 0.3):
                    raise ApiError(f"{n}: rate or dividend yield outside -10 % to 30 %", 422)
                names[n] = {"vol": vol, "q": q, "r": r}
            for key, rho in (body.get("corr") or {}).items():
                a, b = key.split("|")
                if not -0.99 <= float(rho) <= 0.99:
                    raise ApiError(f"correlation {a}-{b} = {float(rho):.2f} outside -0.99 to 0.99", 422)
                corr[_pair(a.upper(), b.upper())] = float(rho)
        except (TypeError, ValueError) as exc:
            raise ApiError(str(exc), 422)
        settings = {"names": names, "corr": corr}
        try:  # the full matrix must be a correlation matrix (positive definite)
            self._basket_ctx((self.ctx.underlying, *names), settings)
        except ValueError as exc:
            raise ApiError(f"the correlations do not form a valid matrix: {exc}", 422)
        self.repo.put_setting("basket", settings)
        return self.basket_settings()

    def _basket_ctx(self, names, settings: dict | None = None, ctx: PricingContext | None = None):
        """The calibrated name (local vol on its surface) and the others as flat Black-Scholes markets at 100, with
        the stored or default assumptions. Returns (context, assumptions shown with the result)."""
        ctx = ctx or self.ctx
        s = settings if settings is not None else self.repo.setting("basket", {})
        r_default = float(ctx.market.zero_rate(1.0))
        others, shown = {}, {}
        for n in names:
            if n == ctx.underlying or n in others:
                continue
            p = {**BASKET_DEFAULTS, "r": None, **s.get("names", {}).get(n, {})}
            r = r_default if p["r"] is None else p["r"]
            others[n] = (100.0, r, p["q"], p["vol"])
            shown[n] = {"vol": p["vol"], "q": p["q"], "r": r, "set": n in s.get("names", {})}
        order = (ctx.underlying, *others)
        corr = np.eye(len(order))
        rho = {}
        for i, a in enumerate(order):
            for j in range(i + 1, len(order)):
                b = order[j]
                corr[i, j] = corr[j, i] = rho[_pair(a, b)] = s.get("corr", {}).get(_pair(a, b), BASKET_DEFAULTS["corr"])
        bctx = BasketContext.with_spx(others, corr, spx=ctx)
        return bctx, {"names": shown, "corr": rho, "calibrated": ctx.underlying}

    def _context_for(self, sheet: TermSheet, ctx: PricingContext):
        if len(sheet.underlyings) > 1:
            return self._basket_ctx(sheet.underlyings, ctx=ctx)
        return ctx, None

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

    def _quote(self, sheet: TermSheet, ctx: PricingContext, o: dict, fixings):
        qctx, basket = self._context_for(sheet, ctx)
        q = quote(sheet, qctx, model=o["model"], control=o["control"], n_paths=o["n_paths"], batch=o["batch"],
                  control_paths=o["control_paths"], fixings=fixings, with_greeks=o["with_greeks"],
                  slv_vega=o["slv_vega"], workers=self.workers, control_forwards=o["control_forwards"],
                  fast=o["fast"], sticky=o["sticky"], mix_band=None if basket else o["mix_band"])
        return q, basket

    def quote(self, body: dict) -> dict:
        sheet = _sheet(body.get("sheet") or {}, strict=True)
        ctx = self.ctx
        fixings = self._fixings_for(sheet, ctx)
        o = self._options(body.get("options") or {})

        def run():
            q, basket = self._quote(sheet, ctx, o, fixings)
            out = quote_json(q)
            out["sheet"] = sheet.to_dict(json_safe=True)
            out["options"] = clean(o)
            out["basket"] = clean(basket)
            out["quote_id"] = self.repo.save_quote(out["sheet"], out)
            return out

        return {"job": self.jobs.submit("quote", run)}

    def _fixings_for(self, sheet: TermSheet, ctx: PricingContext | None = None) -> dict | None:
        ctx = ctx or self.ctx
        strike = sheet.get("general.strike_date")
        if strike is None or strike >= ctx.valuation:
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

    def trade(self, trade_id: str) -> dict:
        """One trade: its latest version, every version, its term sheet as a form, the quote it came from."""
        t = self.repo.trade(trade_id)
        sheet = t.term_sheet()
        quote_id = next((tag[6:] for tag in t.tags if tag.startswith("quote:")), None)
        return clean({"trade": trade_json(t), "versions": self.repo.versions(trade_id), "quote_id": quote_id,
                      "form": describe(sheet.mask.name, sheet) if sheet is not None else None,
                      "script": None if sheet is not None else t.product.get("script")})

    def value_trade(self, trade_id: str, body: dict) -> dict:
        """Mark one trade to the current market (a job): its price as % of notional and its value in currency,
        with the past fixings of a running trade."""
        t = self.repo.trade(trade_id)
        sheet = t.term_sheet()
        if sheet is None:
            raise ApiError("a script trade is valued with the book", 422)
        if sheet.get("wrapper.solve") != "upfront":
            sheet = sheet.replace(**{"wrapper.solve": "upfront"})
        ctx = self.ctx
        fixings = self._fixings_for(sheet, ctx)
        o = self._options(body.get("options") or {})

        def run():
            q, basket = self._quote(sheet, ctx, o, fixings)
            N = float(sheet.get("general.notional"))
            r = q.result
            return clean({"trade_id": trade_id, "quantity": t.quantity, "notional": N, "unit_price": r.price,
                          "unit_stderr": r.stderr, "value": t.quantity * N * r.price / 100,
                          "stderr": abs(t.quantity) * N * r.stderr / 100, "valuation": ctx.valuation,
                          "snapshot_id": ctx.snapshot_id, "result": result_json(r), "quote": quote_json(q),
                          "basket": basket})

        return {"job": self.jobs.submit("value", run)}

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
        """Live trades in two groups, each on its own shared simulation: single names under the policy (SLV for
        path-dependent products), worst-ofs under the basket context (local vol for the calibrated name, flat
        Black-Scholes for the others). The groups use different seeds, so their errors add in quadrature."""
        o = self._options(body.get("options") or {})
        greeks = bool((body.get("options") or {}).get("greeks", True))
        ctx = self.ctx
        stored = self.repo.fixings()
        singles, baskets, names = [], [], []
        for t in self.repo.trades():
            if t.status != "live":
                continue
            sheet = t.term_sheet()
            fix = None
            if t.strike_date < ctx.valuation and sheet is not None:
                fix = {n: stored[n] for n in sheet.underlyings if n in stored} or None
            if sheet is not None and len(sheet.underlyings) > 1:
                baskets.append(t.to_position(fix))
                names += [n for n in sheet.underlyings if n not in names]
            else:
                singles.append(t.to_position(fix))
        if not singles and not baskets:
            raise ApiError("no live trade in the book", 422)

        def run():
            t0 = time.perf_counter()
            groups = []
            if singles:
                groups.append(("single names", Book(singles), ctx, None, DEFAULT_SEED))
            if baskets:
                bctx, shown = self._basket_ctx(names, ctx=ctx)
                groups.append(("worst-of", Book(baskets), bctx, shown, DEFAULT_SEED + 1))
            parts = []
            for label, book, gctx, shown, seed in groups:
                res = book.price(gctx, model=None if shown else o["model"], n_paths=o["n_paths"], seed=seed,
                                 batch=o["batch"], workers=self.workers)
                g = None
                if greeks:
                    g = book.greeks(gctx, model=None if shown else o["model"], n_paths=o["n_paths"], seed=seed,
                                    batch=o["batch"], slv_vega=o["slv_vega"], workers=self.workers)
                parts.append((label, res, g, shown))
            out = _combine_books(parts, ctx.underlying)
            out["seconds"] = time.perf_counter() - t0  # prices and Greeks; each group's own time is its price
            self.last_book = out
            return out

        return {"job": self.jobs.submit("book", run)}

    # --- fixings and lifecycle ---
    def fixings(self, underlying: str | None = None) -> dict:
        return clean({"fixings": self.repo.fixings(underlying)})

    def put_fixings(self, body: dict) -> dict:
        und = str(body.get("underlying", "SPX")).upper()
        levels = body.get("levels") or {}
        scale = float(body.get("real_spot") or 0)
        ctx = self.ctx
        if scale:  # real index levels: divide by real spot / model spot (every market is rescaled to 100)
            ratio = scale / ctx.market.spot
            levels = {d: float(v) / ratio for d, v in levels.items()}
        for d in levels:
            if dt.date.fromisoformat(str(d)) > ctx.valuation:
                raise ApiError(f"fixing on {d} is after the valuation date {ctx.valuation}", 422)
        n = self.repo.put_fixings(und, levels, body.get("source", "manual"))
        return {"ok": True, "stored": n}

    def delete_fixing(self, underlying: str, date: str) -> dict:
        self.repo.delete_fixing(underlying, date)
        return {"ok": True}

    def trade_events(self, trade_id: str) -> dict:
        ctx = self.ctx
        trade = self.repo.trade(trade_id)
        sheet = trade.term_sheet()
        compiled = compile_sheet(sheet, ctx.valuation)
        upcoming = [{"observation": ev.when.value, "payment": ev.pay.value if ev.pay else None}
                    for ev in compiled.program.events[1:]
                    if dt.date.fromisoformat(ev.when.value) > ctx.valuation]
        past, missing = [], None
        if trade.strike_date < ctx.valuation:
            try:
                past = events_json(events(trade, self.repo.fixings(), ctx.valuation))
            except KeyError as exc:  # names the dates, or only the underlying when it has no fixing at all
                missing = str(exc).strip("'\"")
                if missing in sheet.underlyings:
                    missing = f"no fixing of {missing}"
        return clean({"trade_id": trade_id, "past": past, "upcoming": upcoming, "missing": missing,
                      "maturity": compiled.maturity, "strike_date": compiled.strike_date,
                      "underlyings": sheet.underlyings})

    def upcoming(self, days: int = 45) -> dict:
        ctx = self.ctx
        horizon = ctx.valuation + dt.timedelta(days=int(days))
        rows = []
        for t in self.repo.trades():
            if t.status != "live" or t.term_sheet() is None:
                continue
            compiled = compile_sheet(t.term_sheet(), ctx.valuation)
            for ev in compiled.program.events[1:]:
                d = dt.date.fromisoformat(ev.when.value)
                if ctx.valuation <= d <= horizon:
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
        if len(check["underlyings"]) > 1:
            raise ApiError("the script lab prices single-name scripts; worst-ofs go through the term sheet", 422)
        model = (body.get("model") or "lv").lower()
        n_paths = int(body.get("n_paths") or 40_000)
        ctx = self.ctx

        def run():
            res = price(body["script"], ctx.model(model, 2.0), ctx.market, n_paths=n_paths,
                        seed=int(body.get("seed", 2026)), batch=min(n_paths, 25_000), valuation=ctx.valuation,
                        workers=self.workers)
            return clean({"price": res.price, "stderr": res.stderr, "seconds": res.seconds, "model": model,
                          "n_paths": n_paths, "expected_life": res.expected_life, "warnings": res.warnings,
                          "notional": res.notional})

        return {"job": self.jobs.submit("script", run)}


def _combine_books(parts, calibrated: str) -> dict:
    """One book result from groups priced on independent simulations: values add, errors add in quadrature;
    deltas and vegas are reported per underlying (a worst-of moves with every name)."""
    positions, groups, warnings = [], [], []
    by_name: dict = {}
    pos_delta = []
    for label, res, g, shown in parts:
        b = book_json(res)
        for p in b["positions"]:
            p["group"] = label
        positions += b["positions"]
        warnings += b["warnings"]
        groups.append({"label": label, "value": b["value"], "stderr": b["stderr"], "method": b["method"],
                       "model": b["model"], "reason": b["reason"], "n_paths": b["n_paths"], "seconds": b["seconds"],
                       "positions": len(b["positions"]), "basket": clean(shown), "note": g.get("note") if g else None})
        if g is None:
            pos_delta += [None] * len(b["positions"])
            continue
        per_name = {calibrated: {"delta": g["delta"], "vega": g.get("vega")}} if shown is None else \
            {n: {"delta": g["delta"][n], "vega": (g.get("vega") or {}).get(n)} for n in g["delta"]}
        pos_delta += list(np.sum([d["delta"]["positions"] for d in per_name.values()], axis=0))
        for n, d in per_name.items():
            row = by_name.setdefault(n, {"delta": 0.0, "delta_var": 0.0, "vega": 0.0, "vega_var": 0.0, "no_vega": []})
            row["delta"] += d["delta"]["total"]
            row["delta_var"] += d["delta"]["total_se"] ** 2
            if d["vega"] is None:
                row["no_vega"].append(label)
            else:
                row["vega"] += d["vega"]["total"]
                row["vega_var"] += d["vega"]["total_se"] ** 2
    names = {n: {"delta": r["delta"], "delta_se": float(np.sqrt(r["delta_var"])),
                 "vega": None if r["no_vega"] else r["vega"],
                 "vega_se": None if r["no_vega"] else float(np.sqrt(r["vega_var"])),
                 "vega_missing": r["no_vega"]} for n, r in by_name.items()}
    value = float(sum(g["value"] for g in groups))
    stderr = float(np.sqrt(sum(g["stderr"] ** 2 for g in groups)))
    one = groups[0] if len(groups) == 1 else None
    note = next((g["note"] for g in groups if g["note"]), None)
    return clean({
        "value": value, "stderr": stderr, "positions": positions, "groups": groups, "warnings": warnings,
        "method": one["method"] if one else "monte_carlo", "model": one["model"] if one else
        " + ".join(g["model"] for g in groups), "n_paths": max(g["n_paths"] for g in groups),
        "seconds": sum(g["seconds"] for g in groups), "snapshot_id": getattr(parts[0][1], "snapshot_id", None),
        "independent_stderr": float(np.sqrt(sum(p["stderr"] ** 2 for p in positions))),
        "greeks": {"by_name": names, "positions": pos_delta, "note": note, "units": dict(parts[0][2]["units"])}
        if parts[0][2] is not None else None,
        "skipped": [],
    })
