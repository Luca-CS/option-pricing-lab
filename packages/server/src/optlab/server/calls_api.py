"""The issuer's calls on the desk's notes: the call dates to come, the review that prices each note both ways on the
market in use, and the call itself, recorded on the trade.

A review prices the note twice on the same random numbers: the next call left out (the note goes on, its later
calls on the policy fitted for them) and exercised (the call amount paid as the redemption). Both carry the same flows
before the call, so the issuer calls when the note is worth more going on than called: it pays less by calling. A
difference inside two standard errors is said to be close. Each branch's delta (per 1 % of the spot) is the hedge it
leaves.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import numpy as np

from optlab.core import progress
from optlab.core.config import DEFAULT_SEED
from optlab.server.api import ApiError
from optlab.server.serialize import clean, trade_json
from optlab.server.tasks import envelope, register
from optlab.structured.compiler import compile_sheet
from optlab.structured.context import PricingContext
from optlab.structured.exercise import at_call, next_calls
from optlab.structured.multiasset import BasketContext
from optlab.structured.pricer import price_many
from optlab.structured.quote import wrapper
from optlab.structured.script.timeline import Clock, build_timeline

if TYPE_CHECKING:
    from optlab.server.api import Desk
    from optlab.server.tasks import Frozen

BUMP = 0.01


class CallsApi:
    def __init__(self, desk: Desk):
        self.desk = desk

    def _callables(self, trade_ids=None) -> list:
        out = []
        for t in self.desk.repo.trades():
            sheet = t.term_sheet()
            if t.status != "live" or sheet is None or sheet.mask.name != "callable":
                continue
            if trade_ids and t.trade_id not in trade_ids:
                continue
            out.append(t)
        return out

    def _next(self, t, valuation):
        """The trade's product on this date, its timeline and its next call: (product, timeline, event) or None."""
        sheet = self.desk._upfront(t)
        product = compile_sheet(sheet, valuation)
        timeline = build_timeline(product.program, Clock(product.valuation, product.calendar))
        ks = next_calls(product.program, timeline)
        return (product, timeline, ks[0], len(ks)) if ks else None

    def _row(self, t, valuation) -> dict | None:
        got = self._next(t, valuation)
        if got is None:
            return None
        product, _, k, left = got
        sheet = product.sheet
        ev = product.program.events[k]
        obs = ev.when.value
        calendar: Any = product.calendar
        notice = calendar.add_business_days(obs, -int(sheet.get("issuer_call.notice_days")))
        return {
            "trade_id": t.trade_id,
            "quantity": t.quantity,
            "underlyings": list(sheet.underlyings),
            "call_date": obs,
            "pay_date": ev.pay.value if ev.pay is not None else obs,
            "notice_date": notice.isoformat(),
            "call_price": float(sheet.get("issuer_call.call_price")),
            "notional": float(sheet.get("general.notional")),
            "calls_left": left,
        }

    def upcoming(self) -> dict:
        """The next call date of each live issuer callable, with its notice date and call price."""
        valuation = self.desk.ctx.valuation
        rows = [r for t in self._callables() if (r := self._row(t, valuation)) is not None]
        return clean({"valuation": valuation, "calls": sorted(rows, key=lambda r: r["notice_date"])})

    def review(self, body: dict) -> dict:
        """A job: each note priced going on and called at its next date, on the market in use."""
        desk = self.desk
        trades = self._callables(body.get("trade_ids"))
        if not trades:
            raise ApiError("no live issuer callable to review", 422)
        fixings = desk._known_fixings(
            sorted({n for t in trades for n in desk._upfront(t).underlyings}), desk.ctx.valuation
        )
        frozen = desk._freeze(fixings=fixings, trades=trades)
        return desk._submit("call_review", {"options": body.get("options") or {}}, frozen)

    def run_review(self, p: dict, f: Frozen) -> dict:
        desk = self.desk
        ctx = desk.context_of(f)
        o = desk._options(p["options"])
        reviews = []
        trades = desk._trades_of(f)
        for i, t in enumerate(trades):
            progress.tick(i, len(trades), "notes")
            reviews.append(self._review_one(t, ctx, o, f))
        method = {"name": "monte_carlo", "model": "lv", "n_paths": o["n_paths"]}
        return envelope(desk._market(ctx), method, clean({"valuation": ctx.valuation, "reviews": reviews}))

    def _review_one(self, t, ctx: PricingContext, o: dict, f: Frozen) -> dict:
        desk = self.desk
        sheet = desk._upfront(t)
        qctx, _ = desk._context_for(sheet, ctx, f.basket)
        got = self._next(t, qctx.valuation)
        if got is None:
            return {"trade_id": t.trade_id, "note": "no call date to come"}
        product, timeline, k, _ = got
        T = max(product.horizon, 1 / 365)
        if isinstance(qctx, BasketContext):
            model = qctx.model("lv", T, list(sheet.underlyings))
        else:
            model = qctx.model("lv", T)
        fmt = wrapper(sheet, product)
        fixings = f.fixings_by_date()
        runs = {}
        for name, exercise in (("on", False), ("called", True)):
            runs[name] = price_many(
                at_call(product.program, k, exercise),
                model,
                qctx.market,
                [{}, {}, {}],
                fmt,
                o["n_paths"],
                DEFAULT_SEED,
                o["batch"],
                valuation=product.valuation,
                fixings=fixings,
                calendar=product.calendar,
                spot_factors=[1.0, 1 - BUMP, 1 + BUMP],
            )
        on, called = runs["on"][0], runs["called"][0]
        N = float(sheet.get("general.notional"))
        pairs = None
        if on.pairs is not None and called.pairs is not None:
            pairs = 100 * (on.pairs - called.pairs) / N
        gap = on.price - called.price
        se = float(np.std(pairs, ddof=1) / np.sqrt(len(pairs))) if pairs is not None else 0.0

        def delta(r) -> float:  # currency per 1 % of the spot, for the trade's quantity
            return t.quantity * (r[2].value.price - r[1].value.price) / 2

        decision = "call" if gap > 2 * se else "keep" if gap < -2 * se else "close"
        ev = product.program.events[k]
        return {
            "trade_id": t.trade_id,
            "call_date": ev.when.value,
            "pay_date": ev.pay.value if ev.pay is not None else ev.when.value,
            "going_on": {"value": on.price, "se": on.stderr, "delta": delta(runs["on"])},
            "called": {"value": called.price, "se": called.stderr, "delta": delta(runs["called"])},
            "gap": {"value": gap, "se": se},
            "decision": decision,
            "note": (
                "the note is worth more going on than called: the issuer pays less by calling"
                if decision == "call"
                else "the note is worth less going on: the issuer keeps it"
                if decision == "keep"
                else "within two standard errors: either way, at this precision"
            ),
        }

    def call(self, trade_id: str, body: dict) -> dict:
        """The issuer's call of a note at its next call date, recorded: status called, the amount kept on the trade."""
        desk = self.desk
        t = desk.repo.trade(trade_id)
        sheet = t.term_sheet()
        if t.status != "live" or sheet is None or sheet.mask.name != "callable":
            raise ApiError(f"{trade_id} is not a live issuer callable", 409)
        row = self._row(t, desk.ctx.valuation)
        if row is None:
            raise ApiError(f"{trade_id} has no call date to come", 409)
        if body.get("call_date") and body["call_date"] != row["call_date"]:
            raise ApiError(f"{trade_id}'s next call date is {row['call_date']}, not {body['call_date']}", 422)
        amount = t.quantity * row["notional"] * row["call_price"]
        call = {"date": row["call_date"], "pay_date": row["pay_date"], "price": row["call_price"], "amount": amount}
        product = {**t.product, "issuer_call": call}
        return trade_json(desk.repo.save_trade(dataclasses.replace(t, status="called", product=product)))


register("call_review", lambda desk, p, f: desk.calls.run_review(p, f))
