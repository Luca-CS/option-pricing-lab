"""Routes of the risk engine (`optlab.risk`): Greeks, ladder, scenarios, VaR and the explained P&L, on the desk's book.

A thin adapter: it builds the risk groups the way the Book screen values the book (one group per single name and payment
currency on that name's market, the worst-ofs together, the same seeds), runs `optlab.risk` as jobs of the desk's queue,
and keeps every run in the desk's database (the risk tables). As every job (ADR-0006), a submission freezes the book
(trades by version, their fixings, the market in use, the basket settings) and its `run_<kind>` twin rebuilds the groups
from them in the worker. JSON in and out; amounts in the payment currency from the desk's side, per position also in %
of its notional.
"""

from __future__ import annotations

import datetime as dt
import os
import time

from optlab.core import engines, progress
from optlab.core.config import DEFAULT_SEED
from optlab.core.log import get_logger
from optlab.risk import RiskGroup, RiskSettings
from optlab.risk import scenarios as sc
from optlab.risk.greeks import Bumps, GreekOptions, compute
from optlab.risk.ladder import LadderOptions, env_with_betas, ladder, stored_betas
from optlab.risk.store import code_version, run_key
from optlab.server.api import PRECISION, ApiError
from optlab.server.persistence.risk import RiskRepository
from optlab.server.serialize import clean
from optlab.server.tasks import Frozen, envelope, register
from optlab.structured.portfolio import Book

LOG = get_logger(__name__)


class RiskApi:
    def __init__(self, desk):
        self.desk = desk
        self.store = RiskRepository(desk.db)

    # --- the book as risk groups ---
    def groups(self, frozen: Frozen | None = None) -> list:
        """The book's groups on the market of a job (`frozen`), or on the desk as it is now."""
        desk = self.desk
        frozen = frozen or desk.freeze_book()
        ctx = desk.context_of(frozen)
        singles, baskets = desk._positions_by_market(ctx, desk._trades_of(frozen), frozen.fixings_by_date() or {})
        if not singles and not baskets:
            raise ApiError("no live trade in the book", 422)
        out = []
        key = lambda x: (x[0][0] != ctx.underlying, x[0][0], str(x[0][1]))  # noqa: E731
        for k, ((name, pay), positions) in enumerate(sorted(singles.items(), key=key)):
            gctx, _ = desk._single_ctx(name, ctx, pay)
            if gctx.underlying != name:
                raise ApiError(f"no market of {name} on {ctx.valuation}, from the lake or in use", 422)
            out.append(RiskGroup(name, Book(positions), gctx, DEFAULT_SEED + k))
        for pay, (positions, names) in sorted(baskets.items(), key=lambda x: str(x[0])):
            bctx, _ = desk._basket_ctx(names, frozen.basket, ctx=ctx, payment=pay)
            label = "worst-of" if len(baskets) == 1 else f"worst-of ({pay})"
            out.append(RiskGroup(label, Book(positions), bctx, DEFAULT_SEED + len(out)))
        return out

    def _settings(self, opts: dict, smoothing: float = 0.0) -> RiskSettings:
        n_paths, batch = PRECISION.get(opts.get("precision", "standard"), PRECISION["standard"])
        model = opts.get("model") or None
        if model not in (None, "slv"):
            raise ApiError("the risk engine runs on the policy's base (local vol) or on SLV", 422, "model")
        return RiskSettings(
            n_paths=int(opts.get("n_paths") or n_paths),
            batch=batch,
            model=model,
            smoothing=smoothing,
            workers=self.desk.workers,
        )

    def _names(self, groups) -> list:
        from optlab.risk.var import group_names

        return sorted({n for g in groups for n in group_names(g)})

    def _betas(self, groups, valuation) -> dict:
        try:
            return stored_betas(self.store, self.desk.lake, self._names(groups), valuation)
        except (KeyError, ValueError) as exc:
            LOG.warning("no spot-vol betas: %s", exc)
            return {}

    def _meta(self, groups, settings, ctx) -> dict:
        return {
            "snapshot_id": ctx.snapshot_id,
            "valuation": ctx.valuation.isoformat(),
            "origin": self.desk._origin(ctx.snapshot_id),
            "groups": [
                {"label": g.label, "positions": [p.name for p in g.book.positions], "seed": g.seed} for g in groups
            ],
            "settings": settings.to_dict(),
            "code": code_version(),
            "engine": engines.default_engine(),
        }

    def _save(self, kind: str, groups, settings, params: dict, payload: dict, seconds: float, ctx) -> str:
        meta = self._meta(groups, settings, ctx)
        key = run_key(kind=kind, meta=meta, params=params, at=time.time())  # interactive runs: each its own version
        return self.store.save_run(kind, ctx.valuation, key, {**params, **meta}, payload, seconds, meta["code"])

    def _submit(self, kind: str, body: dict, opts: dict, smoothing: float = 0.0) -> dict:
        self._settings(opts, smoothing)  # a bad model is refused now
        return self.desk._submit(kind, body, self.desk.freeze_book())

    def _start(self, f: Frozen, label: str):
        progress.step(f"{label}: the book's groups")
        groups = self.groups(f)
        return groups, self.desk.context_of(f)

    def _done(self, payload: dict, ctx, settings, method: dict) -> dict:
        method = {
            "name": method.pop("name"),
            "n_paths": settings.n_paths,
            "model": settings.model or "policy",
            **method,
        }
        return envelope(self.desk._market(ctx), method, payload)

    # --- Greeks ---
    def run(self, body: dict) -> dict:
        opts = body.get("options") or {}
        Bumps.from_dict(body.get("bumps"))
        GreekOptions.from_dict(opts)
        return self._submit("risk", body, opts, float(opts.get("smoothing", 0.005)))

    def run_greeks(self, body: dict, f: Frozen) -> dict:
        opts = body.get("options") or {}
        settings = self._settings(opts, smoothing=float(opts.get("smoothing", 0.005)))
        bumps = Bumps.from_dict(body.get("bumps"))
        options = GreekOptions.from_dict(opts)
        groups, ctx = self._start(f, "Greeks")
        t0 = time.perf_counter()
        betas = self._betas(groups, ctx.valuation) if options.realistic else {}
        env = env_with_betas(None, betas)
        progress.step("Greeks: revaluations", 1, 2)
        rep = compute(groups, bumps, options, settings, env).to_dict()
        payload = clean({**rep, "betas": betas, "meta": self._meta(groups, settings, ctx)})
        payload["run_id"] = self._save(
            "risk",
            groups,
            settings,
            {"bumps": rep["bumps"], "options": rep["options"]},
            payload,
            time.perf_counter() - t0,
            ctx,
        )
        return self._done(payload, ctx, settings, {"name": "full_revaluation", "smoothing": settings.smoothing})

    def runs(self, kind: str | None = None) -> dict:
        return clean({"runs": self.store.runs(kind, latest=False)[-100:][::-1]})

    def run_get(self, run_id: str) -> dict:
        try:
            r = self.store.run(run_id)
        except KeyError as exc:
            raise ApiError(str(exc).strip("'\""), 404) from exc
        return clean({**r["payload"], "run_id": r["run_id"], "kind": r["kind"], "created_at": r["created_at"]})

    # --- ladder ---
    def ladder(self, body: dict) -> dict:
        opts = body.get("options") or {}
        try:
            LadderOptions.from_dict(body.get("ladder"))
        except (TypeError, ValueError) as exc:
            raise ApiError(str(exc), 422) from exc
        return self._submit("ladder", body, opts, float(opts.get("smoothing", 0.005)))

    def run_ladder(self, body: dict, f: Frozen) -> dict:
        import dataclasses

        opts = body.get("options") or {}
        settings = self._settings(opts, smoothing=float(opts.get("smoothing", 0.005)))
        lo = LadderOptions.from_dict(body.get("ladder"))
        groups, ctx = self._start(f, "ladder")
        t0 = time.perf_counter()
        betas = self._betas(groups, ctx.valuation) if "realistic" in lo.rules else {}
        rules = lo.rules if betas or "realistic" not in lo.rules else tuple(r for r in lo.rules if r != "realistic")
        progress.step("ladder: revaluations", 1, 2)
        rep = ladder(groups, dataclasses.replace(lo, rules=rules), settings, None, betas).to_dict()
        payload = clean(
            {
                **rep,
                "meta": self._meta(groups, settings, ctx),
                "note": None if rules == lo.rules else "no spot-vol betas in the lake: the realistic rule is left out",
            }
        )
        payload["run_id"] = self._save(
            "ladder", groups, settings, {"ladder": rep["options"]}, payload, time.perf_counter() - t0, ctx
        )
        return self._done(payload, ctx, settings, {"name": "full_revaluation", "rules": list(rules)})

    # --- scenarios ---
    def scenarios(self) -> dict:
        got = sc.load(self.store)
        return clean({**got, "scenarios": [s.to_dict() for s in got["scenarios"]], "history": sc.history(self.store)})

    def put_scenarios(self, body: dict) -> dict:
        try:
            version = sc.save(self.store, body.get("scenarios") or [], body.get("signed_by", ""), body.get("note", ""))
        except (TypeError, ValueError, KeyError) as exc:
            raise ApiError(str(exc).strip("'\""), 422) from exc
        return {"ok": True, "version": version}

    def run_scenarios(self, body: dict) -> dict:
        """The set in use is frozen with the job (by value: a set saved meanwhile does not change the run)."""
        opts = body.get("options") or {}
        got = sc.load(self.store)
        chosen = [s for s in got["scenarios"] if not body.get("names") or s.name in body["names"]]
        frozen_set = {
            "scenarios": [s.to_dict() for s in chosen],
            "version": got["version"],
            "starting": got["starting"],
            "signed_by": got["signed_by"],
        }
        return self._submit("scenarios", {**body, "set": frozen_set}, opts)

    def run_scenario_set(self, body: dict, f: Frozen) -> dict:
        opts = body.get("options") or {}
        settings = self._settings(opts)
        got = body["set"]
        chosen = [sc.Scenario.from_dict(d) for d in got["scenarios"]]
        groups, ctx = self._start(f, "scenarios")
        t0 = time.perf_counter()
        betas = self._betas(groups, ctx.valuation)
        usable = [s for s in chosen if s.shock.rule != "realistic" or betas]
        progress.step("scenarios: revaluations", 1, 2)
        rep = sc.run(
            groups, usable, settings, env_with_betas(None, betas), bool(body.get("grid", True)), version=got["version"]
        ).to_dict()
        payload = clean(
            {
                **rep,
                "starting": got["starting"],
                "signed_by": got["signed_by"],
                "meta": self._meta(groups, settings, ctx),
                "skipped": [s.name for s in chosen if s not in usable],
            }
        )
        payload["run_id"] = self._save(
            "scenarios", groups, settings, {"version": got["version"]}, payload, time.perf_counter() - t0, ctx
        )
        return self._done(payload, ctx, settings, {"name": "full_revaluation", "set": got["version"]})

    # --- VaR ---
    def var(self, body: dict) -> dict:
        opts = body.get("options") or {}
        workers = int(body.get("workers") or max(1, min(5, (os.cpu_count() or 2) // 2)))
        return self._submit("var", {**body, "workers": workers}, opts)

    def run_var(self, body: dict, f: Frozen) -> dict:
        from optlab.risk.var import run_var

        opts = body.get("options") or {}
        settings = self._settings(opts)
        workers, days = int(body["workers"]), int(body.get("days", 500))
        groups, ctx = self._start(f, "VaR")
        t0 = time.perf_counter()
        progress.step("VaR: Greeks for the Taylor check", 1, 3)
        rep = compute(
            groups,
            Bumps(),
            GreekOptions(kxt="none", sticky_strike=False, realistic=False, theta=False),
            RiskSettings(n_paths=settings.n_paths, batch=settings.batch, smoothing=0.005),
        )
        progress.step("VaR: revaluations", 2, 3)
        out = run_var(
            groups,
            self.desk.lake,
            ctx.valuation,
            settings,
            days=days,
            ten_day=bool(body.get("ten_day", True)),
            greeks_report=rep,
            workers=workers,
            store=self.store,
            with_stressed=bool(body.get("stressed", True)),
        )
        payload = clean({**out, "meta": self._meta(groups, settings, ctx), "workers": workers})
        payload["run_id"] = self._save(
            "var", groups, settings, {"days": days, "workers": workers}, payload, time.perf_counter() - t0, ctx
        )
        method = {"name": "historical_full_revaluation", "days": days, "workers": workers}
        return self._done(payload, ctx, settings, method)

    # --- explained P&L ---
    def pnl(self, start: str | None, end: str | None) -> dict:
        from optlab.risk.pnl import cumulative

        runs = self.store.runs("pnl")
        if not runs:
            return {
                "daily": [],
                "days": [],
                "curve": [],
                "runs": [],
                "eod": clean(self.store.runs("eod")),
                "note": "no stored P&L run: run the EOD backfill first",
            }
        start = start or runs[0]["params"].get("from") or runs[0]["day"]
        end = end or runs[-1]["day"]
        return clean({**cumulative(self.store, start, end), "eod": self.store.runs("eod", start, end)})

    def pnl_eod(self, body: dict) -> dict:
        opts = body.get("options") or {}
        self._settings(opts)
        start = body.get("from")
        end = body.get("to") or start
        if not start:
            raise ApiError("give the first date (from)", 422, "from")
        for d in (start, end):
            try:
                dt.date.fromisoformat(d)
            except TypeError, ValueError:
                raise ApiError(f"not a date: {d!r}", 422) from None
        trades = self.desk.repo.trades()
        frozen = Frozen(trades=[[t.trade_id, t.version] for t in trades])
        return self.desk._submit("eod", {**body, "from": start, "to": end}, frozen)

    def run_eod(self, body: dict, f: Frozen) -> dict:
        from optlab.risk.pnl import backfill

        settings = self._settings(body.get("options") or {})
        trades = self.desk._trades_of(f)
        start, end = body["from"], body["to"]
        progress.step(f"EOD runs from {start} to {end}")
        out = backfill(self.desk.lake, trades, start, end, self.store, settings, greeks=bool(body.get("greeks", True)))
        market = {"snapshot_id": None, "origin": "lake (point in time, each day)", "as_of": end}
        method = {"name": "eod_backfill", "from": start, "to": end, "n_paths": settings.n_paths}
        return envelope(market, method, clean({"runs": out, "from": start, "to": end}))


for _kind, _method in (
    ("risk", "run_greeks"),
    ("ladder", "run_ladder"),
    ("scenarios", "run_scenario_set"),
    ("var", "run_var"),
    ("eod", "run_eod"),
):
    register(_kind, lambda desk, p, f, _m=_method: getattr(desk.risk, _m)(p, f))
