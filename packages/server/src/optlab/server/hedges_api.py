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


def _instrument(d) -> Instrument:
    if not isinstance(d, dict):
        raise HedgeError("the instrument is an object: {type, its fields}")
    return parse(d)


__all__ = ["HedgesApi", "REFERENCE", "Valuation", "default_fill", "home"]
