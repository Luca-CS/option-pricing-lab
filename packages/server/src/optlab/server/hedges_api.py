"""The desk's hedges: instruments booked as trades (ADR-0025), their marks and costs, and their lifecycle.

A thin adapter over `optlab.structured.hedges`, in the style of `risk_api`: it finds the market an instrument is valued
on (its name's, or its currency's reference index for a rates instrument), the real level of that name over the model's,
the issuer's funding curve from the lake and the swap spreads of the cost table, and books, promotes, discards and rolls
instrument trades. JSON in and out.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from optlab.core.log import get_logger
from optlab.server.api import ApiError
from optlab.server.serialize import clean, trade_json
from optlab.structured.hedges import HedgeError, Instrument, LinearEnv, SpreadCurve, Valuation, parse
from optlab.structured.hedges import costs as hedge_costs
from optlab.structured.hedges.contracts import quarterly_expiries
from optlab.structured.hedges.instruments import BANK, REFERENCE, Cash, Deposit, Future, home
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

    # --- marks and booking ---
    def mark(self, body: dict) -> dict:
        """An instrument at the market in use: its value and fair level per unit, its half-spread, and for a size."""
        inst = _instrument(body.get("instrument"))
        q = float(body.get("quantity", 1.0))
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
