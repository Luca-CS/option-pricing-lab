"""The desk's hedges: instruments booked as trades (ADR-0025), their marks and costs, and their lifecycle.

A thin adapter over `optlab.structured.hedges`, in the style of `risk_api`: it finds the market an instrument is valued
on (its name's, or its currency's reference index for a rates instrument), the real level of that name over the model's,
the issuer's funding curve from the lake and the swap spreads of the cost table, and books, promotes, discards and rolls
instrument trades. JSON in and out.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np
from scipy.stats import norm

from optlab.core.log import get_logger
from optlab.server.api import ApiError
from optlab.server.serialize import clean, trade_json
from optlab.server.tasks import register
from optlab.structured.hedges import HedgeError, Instrument, LinearEnv, SpreadCurve, Valuation, parse
from optlab.structured.hedges import costs as hedge_costs
from optlab.structured.hedges.contracts import listed_option, quarterly_expiries
from optlab.structured.hedges.instruments import BANK, REFERENCE, Cash, Deposit, Future, Vanilla, home
from optlab.structured.trade import Trade

LOG = get_logger(__name__)
ORIGINS = {
    "future": "model (the forward of the market in use)",
    "cash": "observed (the spot of the market in use)",
    "irs": "government proxy (the government curve plus the swap spread)",
    "deposit": "synthetic (the fictitious bank's funding curve)",
    "funding_swap": "synthetic (the fictitious bank's funding curve)",
    "dividend_swap": "model (the dividends the forward carries)",
    "dividend_future": "model (the dividends the forward carries)",
}


def default_fill(inst: Instrument, trade_date: dt.date) -> float:
    """The cash paid per unit at the fill when the trader gives none: the price of cash, the principal of a deposit that
    has started, nothing for the others (their price is in the instrument, or they start at zero)."""
    if isinstance(inst, Cash):
        return float(inst.price)
    if isinstance(inst, Deposit):
        return 1.0 if inst.start <= trade_date else 0.0
    if isinstance(inst, Vanilla):
        assert inst.multiplier is not None  # noqa: S101 (narrows the type for pyright: set by the instrument)
        return float(inst.price) * inst.multiplier
    return 0.0


class HedgesApi:
    def __init__(self, desk):
        self.desk = desk

    # --- what an instrument is valued on ---
    def scale(self, name: str, ctx) -> float | None:
        """The real spot over the model's: the snapshot in use for its own name, else the lake's close."""
        desk = self.desk
        if ctx.snapshot_id and ctx.underlying == name:
            try:
                snap = desk.market_store.get(str(ctx.snapshot_id).split("+")[0])
            except KeyError:
                snap = None
            if snap is not None and snap.spot_scale:
                return float(snap.spot_scale)
        try:
            from optlab.marketdata.pit import real_close

            return float(real_close(desk.lake, name, ctx.valuation)[0]) / float(ctx.market.spot)
        except (KeyError, ValueError, IndexError) as exc:
            LOG.info("no real level of %s on %s: %s", name, ctx.valuation, exc)
            return None

    def funding(self, day) -> SpreadCurve:
        """The fictitious bank's curve at `day` from the lake; a flat 0 bp assumption without one."""
        try:
            from optlab.marketdata.hedges import funding_curve

            return funding_curve(self.desk.lake, day)
        except (KeyError, ValueError) as exc:
            LOG.info("no funding curve on %s: %s", day, exc)
            return SpreadCurve.flat(0.0, BANK, "assumption")

    def rows(self, version: int | None = None) -> dict:
        """The hedging rows of the cost table (a job's version, else the latest), completed from the starting ones."""
        stored = self.desk.repo.defaults(version) if version is not None else self.desk.repo.defaults()
        return hedge_costs.merged((stored or {}).get("hedges"))

    def linear_env(self, names, ctx, version: int | None = None, scales: dict | None = None) -> LinearEnv:
        """The LinearEnv of a group on `ctx`: each name's scale, the funding curve at its date, the swap spreads."""
        found = dict(scales or {})
        for n in names:
            if n not in found:
                s = self.scale(n, ctx)
                if s is not None:
                    found[n] = s
        return LinearEnv(found, {BANK: self.funding(ctx.valuation)}, hedge_costs.swap_spreads(self.rows(version)))

    def _valued(self, inst: Instrument):
        """(Valuation, context, home name) of an instrument on the desk's market of its home."""
        desk = self.desk
        name, pay = home(inst)
        ctx, _ = desk._single_ctx(name, desk.ctx, pay)
        if ctx.underlying != name:
            raise HedgeError(f"no market of {name} on {desk.ctx.valuation}, from the lake or in use")
        env = self.linear_env([name], ctx)
        if inst.underlying and inst.underlying not in env.scales:
            raise HedgeError(f"no real level of {name} on {ctx.valuation}: a {inst.type} is valued in real points")
        return env.valuation(inst, ctx.market, ctx.valuation), ctx, name

    # --- the listed universe ---
    def chain(self, name: str, day=None, expiry=None) -> dict:
        """The day's listed European options of a name from the lake (two-sided quotes), each with its forward call
        delta on the desk's surface of the name. Raw quotes: refused on a shared desk unless its owner serves them."""
        from optlab.marketdata.quotes import chain_rows

        desk = self.desk
        desk._private("the option chains")
        spec = listed_option(name)
        ctx, _ = desk._single_ctx(name, desk.ctx, spec.currency)
        day = dt.date.fromisoformat(str(day)) if day else ctx.valuation
        rows = chain_rows(desk.lake, name, day) if desk.lake.has("option_quotes") else None
        if rows is None or rows.empty:
            raise ApiError(f"no option quotes of {name} on {day} in the lake", 404)
        rows = rows[(rows["bid"] > 0) & (rows["ask"] >= rows["bid"])]
        rows = rows[rows["expiry"].astype(str) > day.isoformat()]
        if expiry:
            rows = rows[rows["expiry"].astype(str) == str(expiry)]
        scale = self.scale(name, ctx) if ctx.underlying == name else None
        out = []
        for r in rows.to_dict("records"):
            exp = str(r["expiry"])[:10]
            delta = None
            if scale and ctx.valuation == day:
                T = max((dt.date.fromisoformat(exp) - day).days / 365, 1 / 365)
                k = float(np.log(float(r["strike"]) / scale / float(ctx.market.F(T))))
                vol = float(ctx.surface.iv(k, T))
                delta = float(norm.cdf((-k + 0.5 * vol * vol * T) / (vol * np.sqrt(T))))
            bid, ask = float(r["bid"]), float(r["ask"])
            out.append(
                {
                    "expiry": exp,
                    "strike": float(r["strike"]),
                    "kind": "call" if str(r["cp"]) == "C" else "put",
                    "bid": bid,
                    "ask": ask,
                    "mid": 0.5 * (bid + ask),
                    "delta": delta,
                    "multiplier": spec.multiplier,
                }
            )
        out.sort(key=lambda x: (x["expiry"], x["kind"], x["strike"]))
        return clean(
            {
                "name": name,
                "day": day,
                "origin": str(rows["origin"].iloc[0]) if "origin" in rows and len(rows) else "observed",
                "source": str(rows["source"].iloc[0]) if "source" in rows and len(rows) else "",
                "multiplier": spec.multiplier,
                "expiries": sorted({x["expiry"] for x in out}),
                "rows": out,
            }
        )

    def listed_quote(self, inst: Vanilla, day=None) -> tuple[float, float] | None:
        """(bid, ask) of a listed vanilla in the day's chain, None when the chain does not hold it."""
        try:
            got = self.chain(inst.name, day, inst.expiry.isoformat())["rows"]
        except ApiError:
            return None
        for r in got:
            if r["kind"] == inst.kind and abs(r["strike"] - inst.strike) < 1e-9:
                return r["bid"], r["ask"]
        return None

    def vanilla_mark(self, inst: Vanilla) -> dict:
        """A vanilla on the desk's surface of its name: its value per lot (a replication on the smile), the fair premium
        per index point, the half-spread per lot (the chain's for a listed one, else the cost table's vol spread)."""
        from optlab.structured.greeks import ShiftedSurface
        from optlab.structured.portfolio import Book, Position

        desk = self.desk
        name, pay = home(inst)
        ctx, _ = desk._single_ctx(name, desk.ctx, pay)
        if ctx.underlying != name:
            raise HedgeError(f"no market of {name} on {desk.ctx.valuation}, from the lake or in use")
        env = self.linear_env([name], ctx)
        if name not in env.scales:
            raise HedgeError(f"no real level of {name} on {ctx.valuation}: a vanilla's strike is a real level")
        book = Book([Position("v", inst, 1.0)], env)
        unit = book.price(ctx).value
        mult = float(inst.multiplier or 1.0)
        quoted = self.listed_quote(inst) if inst.listed else None
        if quoted is not None:
            spread, origin = 0.5 * (quoted[1] - quoted[0]) * mult, "observed (the chain's bid and ask)"
        else:
            vol = self.rows()["otc_vol_points"] / 100
            wider = book.price(ctx, surface=ShiftedSurface(ctx.surface, vol)).value
            spread, origin = abs(wider - unit), "model (the desk surface, the cost table's vol spread)"
        return {"unit": unit, "fair": unit / mult, "half_spread": spread, "origin": origin, "ctx": ctx, "name": name}

    # --- marks and booking ---
    def mark(self, body: dict) -> dict:
        """An instrument at the market in use: its value and fair level per unit, its half-spread, and for a size."""
        inst = _instrument(body.get("instrument"))
        q = float(body.get("quantity", 1.0))
        if isinstance(inst, Vanilla):
            m = self.vanilla_mark(inst)
            return clean(
                {
                    "instrument": inst.to_dict(),
                    "label": inst.label,
                    "currency": inst.currency,
                    "home": m["name"],
                    "valuation": m["ctx"].valuation,
                    "unit": {"value": m["unit"], "fair": m["fair"], "half_spread": m["half_spread"]},
                    "quantity": q,
                    "value": q * m["unit"],
                    "cost": abs(q) * m["half_spread"],
                    "fill": default_fill(inst, m["ctx"].valuation),
                    "origin": m["origin"],
                    "funding_origin": None,
                    "swap_spread": 0.0,
                }
            )
        v, ctx, name = self._valued(inst)
        unit = inst.value(v)
        spread = hedge_costs.half_spread(inst, v, self.rows())
        return clean(
            {
                "instrument": inst.to_dict(),
                "label": inst.label,
                "currency": inst.currency,
                "home": name,
                "valuation": ctx.valuation,
                "unit": {"value": unit, "fair": inst.fair(v), "half_spread": spread},
                "quantity": q,
                "value": q * unit,
                "cost": abs(q) * spread,
                "fill": default_fill(inst, ctx.valuation),
                "origin": ORIGINS.get(inst.type, "model"),
                "funding_origin": v.funding.origin if v.funding else None,
                "swap_spread": v.swap_spread,
            }
        )

    def create(self, body: dict) -> dict:
        desk = self.desk
        inst = _instrument(body.get("instrument"))
        q = float(body.get("quantity", 0.0))
        if not q:
            raise HedgeError("a hedge needs a non-zero quantity: its side is its sign (> 0: the desk is long)")
        trade_date = dt.date.fromisoformat(str(body.get("trade_date") or desk.ctx.valuation.isoformat()))
        fill = body.get("fill")
        if isinstance(inst, Vanilla) and not inst.price:
            if inst.listed:  # the chain's ask when buying, its bid when selling
                quoted = self.listed_quote(inst)
                if quoted is None:
                    raise HedgeError(f"{inst.label} is not in the day's chain: give its premium")
                price = quoted[1] if q > 0 else quoted[0]
            else:  # OTC: the surface's mid plus or minus the cost table's vol spread
                m = self.vanilla_mark(inst)
                price = max(m["fair"] + (1 if q > 0 else -1) * m["half_spread"] / float(inst.multiplier or 1.0), 0.0)
            inst = dataclasses.replace(inst, price=price)
        tags = tuple(dict.fromkeys(["hedge", *(body.get("tags") or ())]))
        trade = Trade.from_instrument(
            body.get("trade_id") or desk.repo.new_trade_id("H"),
            inst,
            q,
            trade_date,
            fill=default_fill(inst, trade_date) if fill is None else float(fill),
            fees=float(body.get("fees") or 0.0),
            status=str(body.get("status") or "live"),
            book=body.get("book", "default"),
            counterparty=body.get("counterparty", ""),
            tags=tags,
        )
        return trade_json(desk.repo.save_trade(trade))

    def promote(self, trade_id: str) -> dict:
        """A simulated trade booked: live, a new version."""
        t = self.desk.repo.trade(trade_id)
        if t.status != "simulated":
            raise ApiError(f"{trade_id} is {t.status}: only a simulated trade is promoted", 409)
        return trade_json(self.desk.repo.save_trade(dataclasses.replace(t, status="live")))

    def discard(self, trade_id: str) -> dict:
        """A simulated trade removed (a soft delete, as any trade's)."""
        t = self.desk.repo.trade(trade_id)
        if t.status != "simulated":
            raise ApiError(f"{trade_id} is {t.status}: only a simulated trade is discarded", 409)
        self.desk.repo.delete_trade(trade_id)
        return {"ok": True, "trade_id": trade_id}

    # --- the optimizer (job `hedge`) ---
    def optimize(self, body: dict) -> dict:
        """Submit the hedge optimizer on the book as it is (a job): the proposal comes back as simulated trades to book."""
        return self.desk._submit("hedge", body, self.desk.freeze_book())

    def candidates(self, groups: list, universe: dict, version: int | None = None) -> list:
        """The hedging universe of the book's single-name groups, each candidate at its fair level with its half-spread:
        futures on the next quarterly expiries, vanillas per expiry and delta bucket (listed from the day's chain when
        asked and quoted, else OTC), and in the group of a currency's reference index its swaps, deposits and funding
        swaps per tenor; dividend swaps per calendar year."""
        from optlab.structured.multiasset import BasketContext

        rows = self.rows(version)
        u = {**UNIVERSE, **(universe or {})}
        out: list = []
        for g in groups:
            if not isinstance(g.ctx, BasketContext):
                _GroupUniverse(self, g, u, rows, version, out).build()
        return out

    def _vanilla_on(self, inst: Vanilla, ctx, env) -> tuple[float, float]:
        """(value per lot, half-spread per lot) of a vanilla on a group's context: replication, the vol spread."""
        from optlab.structured.greeks import ShiftedSurface
        from optlab.structured.portfolio import Book, Position

        book = Book([Position("v", inst, 1.0)], env)
        unit = book.price(ctx).value
        wider = book.price(ctx, surface=ShiftedSurface(ctx.surface, self.rows()["otc_vol_points"] / 100)).value
        return unit, abs(wider - unit)

    def _nearest_listed(self, name: str, expiry: dt.date, strike: float, kind: str):
        """The listed option of the day's chain nearest an expiry and a strike, with its half-spread per lot."""
        try:
            chain = self.chain(name)
        except ApiError:
            return None
        rows = [r for r in chain["rows"] if r["kind"] == kind]
        if not rows:
            return None
        best = min(
            rows, key=lambda r: (abs((dt.date.fromisoformat(r["expiry"]) - expiry).days), abs(r["strike"] - strike))
        )
        inst = Vanilla(name, dt.date.fromisoformat(best["expiry"]), best["strike"], kind, listed=True)
        return inst, 0.5 * (best["ask"] - best["bid"]) * best["multiplier"]

    def run_optimize(self, p: dict, f) -> dict:
        from optlab.risk.hedge import FAMILIES, Target, optimise
        from optlab.server.tasks import envelope

        desk = self.desk
        opts = p.get("options") or {}
        settings = desk.risk._settings(opts, smoothing=float(opts.get("smoothing", 0.005)))
        groups, ctx = desk.risk._start(f, "hedge", opts)
        progress_step("hedge: the candidates")
        cands = self.candidates(groups, p.get("universe") or {}, f.costs)
        targets = None
        if p.get("targets"):
            targets = [Target(t["key"], float(t.get("value", 0.0)), float(t["tolerance"])) for t in p["targets"]]
        shocks, warnings = self._var_shocks(groups, ctx.valuation, int(p.get("var_days", 250)))
        progress_step("hedge: the book and the candidates in one risk run")
        run = optimise(
            groups,
            cands,
            targets,
            tuple(p.get("families") or FAMILIES),
            settings,
            None,
            None,
            bool(p.get("grid", True)),
            shocks,
            float(p.get("relative", 0.1)),
            float(p.get("floor", 1.0)),
        )
        out = run.to_dict()
        out["candidates"] = len(cands)
        out["warnings"] = sorted(set(out["warnings"]) | set(warnings))
        method = {"name": "hedge_optimizer", "n_paths": settings.n_paths, "candidates": len(cands)}
        return envelope(desk._market(ctx), method, clean(out))

    def _var_shocks(self, groups, day, days: int) -> tuple[dict | None, list]:
        """The history's 1-day moves of the book's factors, as shocks (None without the lake's history)."""
        if days <= 0:
            return None, []
        from optlab.marketdata.days import shifted_day
        from optlab.risk.shocks import context_currency
        from optlab.risk.var import FACTORS, changes, factor_levels, group_names, shock_of

        names = sorted({n for g in groups for n in group_names(g)})
        currencies = sorted({context_currency(g.ctx) for g in groups})
        try:
            levels, _ = factor_levels(self.desk.lake, names, currencies, shifted_day(day, days + 15), day)
        except (KeyError, ValueError) as exc:
            return None, [f"no VaR control: the lake's history is missing ({exc})"]
        moves = changes(levels, 1).iloc[-days:]
        if moves.empty:
            return None, ["no VaR control: no history before the valuation date"]
        return {f"1d:{d}": shock_of(row, names, currencies[0], FACTORS) for d, row in moves.iterrows()}, []

    def book_proposal(self, body: dict) -> dict:
        """The proposal of a finished `hedge` job booked as trades, simulated or live, in one call: each at its fill (the
        chain's or the surface's for a vanilla, its fair level for the others with its half-spread as fees)."""
        desk = self.desk
        job = desk.job(str(body["job_id"]))
        if job.get("kind") != "hedge" or job.get("state") != "done":
            raise ApiError("not a finished hedge job", 409)
        rows = (job.get("result") or {}).get("proposal") or []
        chosen = body.get("indexes")
        picked = [r for r in rows if chosen is None or r["index"] in chosen]
        if not picked:
            raise ApiError("nothing to book in this proposal", 422)
        status = str(body.get("status") or "simulated")
        booked = []
        for r in picked:
            inst = r["instrument"]
            fees = 0.0 if inst.get("type") == "vanilla" else float(r["cost"])
            booked.append(
                self.create(
                    {
                        "instrument": inst,
                        "quantity": r["quantity"],
                        "status": status,
                        "fees": fees,
                        "tags": ["hedge", f"proposal:{body['job_id']}"],
                    }
                )
            )
        return {"trades": booked, "status": status}

    def settle(self, trade_id: str) -> dict:
        """An expired vanilla or future settled in cash against the lake's close at its expiry: status matured, the
        amount per unit and in total kept on the trade (never the index level, personal-use data)."""
        from optlab.marketdata.pit import real_close

        desk = self.desk
        t = desk.repo.trade(trade_id)
        inst = t.instrument()
        if not isinstance(inst, (Vanilla, Future)) or t.status != "live":
            raise ApiError(f"{trade_id} is not a live vanilla or future", 409)
        if inst.expiry > desk.ctx.valuation:
            raise ApiError(f"{trade_id} expires on {inst.expiry}, after the valuation date {desk.ctx.valuation}", 409)
        try:
            level, _ = real_close(desk.lake, inst.name, inst.expiry)
        except (KeyError, ValueError) as exc:
            raise ApiError(f"no close of {inst.name} at {inst.expiry} in the lake: {exc}", 422) from exc
        mult = float(inst.multiplier or 1.0)
        if isinstance(inst, Future):
            unit = mult * (level - inst.price)
        else:
            unit = mult * max(level - inst.strike if inst.kind == "call" else inst.strike - level, 0.0)
        settlement = {"date": inst.expiry.isoformat(), "unit": unit, "amount": t.quantity * unit}
        product = {**t.product, "settlement": settlement}
        return trade_json(desk.repo.save_trade(dataclasses.replace(t, status="matured", product=product)))

    def roll(self, trade_id: str) -> dict:
        """A live future closed at its level today and the same size opened on the next quarterly expiry at its level;
        each leg pays its half-spread, recorded as the new trade's fees."""
        desk = self.desk
        t = desk.repo.trade(trade_id)
        inst = t.instrument()
        if not isinstance(inst, Future) or t.status != "live":
            raise ApiError(f"{trade_id} is not a live future", 409)
        v, ctx, _ = self._valued(inst)
        level = inst.forward(v)
        (nxt,) = quarterly_expiries(inst.expiry, 1)
        opened = Future(inst.name, nxt, 1.0, inst.multiplier)
        level_next = opened.forward(v)
        opened = Future(inst.name, nxt, level_next, inst.multiplier)
        rows = self.rows()
        fees = abs(t.quantity) * (hedge_costs.half_spread(inst, v, rows) + hedge_costs.half_spread(opened, v, rows))
        product = {**t.product, "close": {"date": ctx.valuation.isoformat(), "price": level}}
        closed = desk.repo.save_trade(dataclasses.replace(t, status="closed", product=product))
        new = Trade.from_instrument(
            desk.repo.new_trade_id("H"),
            opened,
            t.quantity,
            ctx.valuation,
            fill=0.0,
            fees=fees,
            book=t.book,
            counterparty=t.counterparty,
            tags=tuple(dict.fromkeys([*t.tags, f"rolled:{trade_id}"])),
        )
        return {"closed": trade_json(closed), "opened": trade_json(desk.repo.save_trade(new))}


class _GroupUniverse:
    """The candidates of one single-name group, family by family (`HedgesApi.candidates`)."""

    def __init__(self, api: HedgesApi, g, u: dict, rows: dict, version, out: list):
        from optlab.risk.shocks import context_currency

        self.api, self.g, self.u, self.rows, self.out = api, g, u, rows, out
        self.ctx, self.name, self.cur = g.ctx, g.ctx.underlying, context_currency(g.ctx)
        has = self.name in g.book.linear.scales
        self.env = g.book.linear if has else api.linear_env([self.name], self.ctx, version)
        self.scale = self.env.scales.get(self.name)
        self.val = self.ctx.valuation
        self.lots, self.notional = float(u["max_lots"]), float(u["max_notional"])

    def build(self) -> None:
        from optlab.structured.hedges.contracts import INDEX_FUTURES, LISTED_OPTIONS

        reference = REFERENCE.get(self.cur) == self.name
        if self.scale and self.u["futures"] and self.name in INDEX_FUTURES:
            self.futures()
        if self.scale and self.u["vanillas"] and self.name in LISTED_OPTIONS:
            self.vanillas()
        if self.u["rates"] and reference:
            self.rates()
        if self.u["funding"] and reference:
            self.funding()
        if self.scale and self.u["dividends"]:
            self.dividends()

    def v(self, inst) -> Valuation:
        return self.env.valuation(inst, self.ctx.market, self.val)

    def add(self, inst, integer: bool, limit: float, cost: float | None = None) -> None:
        from optlab.risk.hedge import Candidate

        c = hedge_costs.half_spread(inst, self.v(inst), self.rows) if cost is None else cost
        self.out.append(Candidate(inst.label, inst, float(c), float(limit), integer, self.g.label))

    def futures(self) -> None:
        for exp in quarterly_expiries(self.val, int(self.u["expiries"])):
            probe = Future(self.name, exp, 1.0)
            self.add(Future(self.name, exp, probe.forward(self.v(probe))), True, self.lots)

    def vanillas(self) -> None:
        from optlab.risk.explain import DELTA_BUCKETS

        assert self.scale is not None  # noqa: S101 (narrows the type for pyright: build() asks for a scale)
        for T in self.u["tenors"]:
            expiry = self.val + dt.timedelta(days=round(365 * float(T)))
            forward = float(self.ctx.market.F(float(T)))
            for d in DELTA_BUCKETS:
                strike = round(self.scale * forward * float(np.exp(_strike_at_delta(self.ctx, float(T), float(d)))), 2)
                kind = "put" if d > 0.5 else "call"
                listed = self.api._nearest_listed(self.name, expiry, strike, kind) if self.u["listed"] else None
                if listed is not None:
                    self.add(listed[0], True, self.lots, listed[1])
                    continue
                inst = Vanilla(self.name, expiry, strike, kind, listed=False)
                _, spread = self.api._vanilla_on(inst, self.ctx, self.env)
                self.add(inst, False, self.notional / (self.scale * 100.0), spread)

    def rates(self) -> None:
        from optlab.structured.hedges.instruments import Irs

        for years in (2, 5, 10):
            probe = Irs(self.cur, self.val, _years_after(self.val, years), 0.0)
            self.add(Irs(self.cur, self.val, probe.end, probe.fair(self.v(probe))), False, self.notional)

    def funding(self) -> None:
        from optlab.structured.hedges.instruments import FundingSwap

        for years in (1, 2):
            probe = Deposit(self.cur, self.val, _years_after(self.val, years), 0.0)
            self.add(Deposit(self.cur, self.val, probe.end, probe.fair(self.v(probe))), False, self.notional)
        for years in (3, 5):
            probe = FundingSwap(self.cur, self.val, _years_after(self.val, years), 0.0)
            self.add(FundingSwap(self.cur, self.val, probe.end, probe.fair(self.v(probe))), False, self.notional)

    def dividends(self) -> None:
        from optlab.structured.hedges.instruments import DividendSwap

        assert self.scale is not None  # noqa: S101 (narrows the type for pyright: build() asks for a scale)
        for y in range(self.val.year + 1, self.val.year + 4):
            probe = DividendSwap(self.name, y, 0.0)
            pts = probe.points(self.v(probe))
            self.add(DividendSwap(self.name, y, round(pts, 4)), False, self.notional / (self.scale * 100.0))


UNIVERSE = {
    "futures": True,
    "expiries": 2,
    "vanillas": True,
    "listed": False,
    "tenors": (0.25, 0.5, 1.0),
    "rates": True,
    "dividends": True,
    "funding": True,
    "max_lots": 500.0,
    "max_notional": 1e8,
}


def progress_step(label: str) -> None:
    from optlab.core import progress

    progress.step(label)


def _years_after(d: dt.date, years: int) -> dt.date:
    from optlab.structured.dates import add_months

    return add_months(d, 12 * years)


def _strike_at_delta(ctx, T: float, delta: float) -> float:
    """ln(K / F) whose forward call delta on the surface is `delta`: a fixed point on the smile's vol at the strike."""
    from scipy.stats import norm as _norm

    vol = float(ctx.surface.iv(0.0, T))
    k = 0.0
    for _ in range(8):
        k = 0.5 * vol * vol * T - vol * np.sqrt(T) * float(_norm.ppf(delta))
        vol = float(ctx.surface.iv(k, T))
    return k


def _instrument(d) -> Instrument:
    if not isinstance(d, dict):
        raise HedgeError("the instrument is an object: {type, its fields}")
    return parse(d)


register("hedge", lambda desk, p, f: desk.hedges.run_optimize(p, f))

__all__ = ["HedgesApi", "REFERENCE", "Valuation", "default_fill", "home"]
