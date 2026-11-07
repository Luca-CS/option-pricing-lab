"""Routes of the risk engine (`optlab.risk`): Greeks, ladder, scenarios, VaR and the explained P&L, on the desk's book.

A thin adapter: it builds the risk groups the way the Book screen values the book (one group per single name and payment
currency on that name's market, the worst-ofs together, the same seeds), runs `optlab.risk` in the desk's job pool, and keeps
every run in the risk store beside the desk's store (`risk.sqlite`). JSON in and out; amounts in the payment currency from the
desk's side, per position also in % of its notional.
"""

from __future__ import annotations

import datetime as dt
import os
import time

from ..config import DEFAULT_SEED
from ..log import get_logger
from ..risk import RiskGroup, RiskSettings
from ..risk import scenarios as sc
from ..risk.greeks import Bumps, GreekOptions, compute
from ..risk.ladder import LadderOptions, env_with_betas, ladder, stored_betas
from ..risk.store import RiskStore, code_version, run_key
from ..structured.portfolio import Book
from .api import PRECISION, ApiError
from .serialize import clean

LOG = get_logger(__name__)


class RiskApi:
    def __init__(self, desk):
        self.desk = desk
        self.store = RiskStore(desk.repo.path.parent / "risk.sqlite")

    # --- the book as risk groups ---
    def groups(self) -> list:
        desk = self.desk
        ctx = desk.ctx
        singles, baskets = desk._positions_by_market(ctx)
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
            bctx, _ = desk._basket_ctx(names, ctx=ctx, payment=pay)
            label = "worst-of" if len(baskets) == 1 else f"worst-of ({pay})"
            out.append(RiskGroup(label, Book(positions), bctx, DEFAULT_SEED + len(out)))
        return out

    def _settings(self, opts: dict, smoothing: float = 0.0) -> RiskSettings:
        n_paths, batch = PRECISION.get(opts.get("precision", "standard"), PRECISION["standard"])
        model = opts.get("model") or None
        if model not in (None, "slv"):
            raise ApiError("the risk engine runs on the policy's base (local vol) or on SLV", 422, "model")
        return RiskSettings(n_paths=int(opts.get("n_paths") or n_paths), batch=batch, model=model, smoothing=smoothing,
                            workers=self.desk.workers)

    def _names(self, groups) -> list:
        from ..risk.var import group_names
        return sorted({n for g in groups for n in group_names(g)})

    def _betas(self, groups) -> dict:
        try:
            return stored_betas(self.store, self.desk.lake, self._names(groups), self.desk.ctx.valuation)
        except (KeyError, ValueError) as exc:
            LOG.warning("no spot-vol betas: %s", exc)
            return {}

    def _meta(self, groups, settings) -> dict:
        ctx = self.desk.ctx
        return {"snapshot_id": ctx.snapshot_id, "valuation": ctx.valuation.isoformat(), "origin": self.desk._origin(ctx.snapshot_id),
                "groups": [{"label": g.label, "positions": [p.name for p in g.book.positions], "seed": g.seed} for g in groups],
                "settings": settings.to_dict(), "code": code_version(), "engine": os.environ.get("OPTLAB_ENGINE")}

    def _save(self, kind: str, groups, settings, params: dict, payload: dict, seconds: float) -> str:
        meta = self._meta(groups, settings)
        key = run_key(kind=kind, meta=meta, params=params, at=time.time())  # interactive runs are kept, each its own version
        return self.store.save_run(kind, self.desk.ctx.valuation, key, {**params, **meta}, payload, seconds, meta["code"])

    # --- Greeks ---
    def run(self, body: dict) -> dict:
        opts = body.get("options") or {}
        groups = self.groups()
        settings = self._settings(opts, smoothing=float(opts.get("smoothing", 0.005)))
        bumps = Bumps.from_dict(body.get("bumps"))
        options = GreekOptions.from_dict(opts)

        def job():
            t0 = time.perf_counter()
            betas = self._betas(groups) if options.realistic else {}
            env = env_with_betas(None, betas)
            rep = compute(groups, bumps, options, settings, env).to_dict()
            payload = clean({**rep, "betas": betas, "meta": self._meta(groups, settings)})
            payload["run_id"] = self._save("risk", groups, settings, {"bumps": rep["bumps"], "options": rep["options"]}, payload,
                                           time.perf_counter() - t0)
            return payload

        return {"job": self.desk.jobs.submit("risk", job)}

    def runs(self, kind: str | None = None) -> dict:
        return clean({"runs": self.store.runs(kind, latest=False)[-100:][::-1]})

    def run_get(self, run_id: str) -> dict:
        try:
            r = self.store.run(run_id)
        except KeyError as exc:
            raise ApiError(str(exc).strip("'\""), 404)
        return clean({**r["payload"], "run_id": r["run_id"], "kind": r["kind"], "created_at": r["created_at"]})

    # --- ladder ---
    def ladder(self, body: dict) -> dict:
        opts = body.get("options") or {}
        groups = self.groups()
        settings = self._settings(opts, smoothing=float(opts.get("smoothing", 0.005)))
        try:
            lo = LadderOptions.from_dict(body.get("ladder"))
        except (TypeError, ValueError) as exc:
            raise ApiError(str(exc), 422)

        def job():
            t0 = time.perf_counter()
            betas = self._betas(groups) if "realistic" in lo.rules else {}
            rules = lo.rules if betas or "realistic" not in lo.rules else tuple(r for r in lo.rules if r != "realistic")
            import dataclasses
            rep = ladder(groups, dataclasses.replace(lo, rules=rules), settings, None, betas).to_dict()
            payload = clean({**rep, "meta": self._meta(groups, settings),
                             "note": None if rules == lo.rules else "no spot-vol betas in the lake: the realistic rule is left out"})
            payload["run_id"] = self._save("ladder", groups, settings, {"ladder": rep["options"]}, payload, time.perf_counter() - t0)
            return payload

        return {"job": self.desk.jobs.submit("ladder", job)}

    # --- scenarios ---
    def scenarios(self) -> dict:
        got = sc.load(self.store)
        return clean({**got, "scenarios": [s.to_dict() for s in got["scenarios"]], "history": sc.history(self.store)})

    def put_scenarios(self, body: dict) -> dict:
        try:
            version = sc.save(self.store, body.get("scenarios") or [], body.get("signed_by", ""), body.get("note", ""))
        except (TypeError, ValueError, KeyError) as exc:
            raise ApiError(str(exc).strip("'\""), 422)
        return {"ok": True, "version": version}

    def run_scenarios(self, body: dict) -> dict:
        opts = body.get("options") or {}
        groups = self.groups()
        settings = self._settings(opts)
        got = sc.load(self.store)
        chosen = [s for s in got["scenarios"] if not body.get("names") or s.name in body["names"]]

        def job():
            t0 = time.perf_counter()
            betas = self._betas(groups)
            usable = [s for s in chosen if s.shock.rule != "realistic" or betas]
            rep = sc.run(groups, usable, settings, env_with_betas(None, betas), bool(body.get("grid", True)),
                         version=got["version"]).to_dict()
            payload = clean({**rep, "starting": got["starting"], "signed_by": got["signed_by"], "meta": self._meta(groups, settings),
                             "skipped": [s.name for s in chosen if s not in usable]})
            payload["run_id"] = self._save("scenarios", groups, settings, {"version": got["version"]}, payload,
                                           time.perf_counter() - t0)
            return payload

        return {"job": self.desk.jobs.submit("scenarios", job)}

    # --- VaR ---
    def var(self, body: dict) -> dict:
        from ..risk.var import run_var
        opts = body.get("options") or {}
        groups = self.groups()
        settings = self._settings(opts)
        workers = int(body.get("workers") or max(1, min(5, (os.cpu_count() or 2) // 2)))
        days = int(body.get("days", 500))

        def job():
            t0 = time.perf_counter()
            rep = compute(groups, Bumps(), GreekOptions(kxt="none", sticky_strike=False, realistic=False, theta=False),
                          RiskSettings(n_paths=settings.n_paths, batch=settings.batch, smoothing=0.005))
            out = run_var(groups, self.desk.lake, self.desk.ctx.valuation, settings, days=days, ten_day=bool(body.get("ten_day", True)),
                          greeks_report=rep, workers=workers, store=self.store, with_stressed=bool(body.get("stressed", True)))
            payload = clean({**out, "meta": self._meta(groups, settings), "workers": workers})
            payload["run_id"] = self._save("var", groups, settings, {"days": days, "workers": workers}, payload, time.perf_counter() - t0)
            return payload

        return {"job": self.desk.jobs.submit("var", job)}

    # --- explained P&L ---
    def pnl(self, start: str | None, end: str | None) -> dict:
        from ..risk.pnl import cumulative
        runs = self.store.runs("pnl")
        if not runs:
            return {"daily": [], "days": [], "curve": [], "runs": [], "eod": clean(self.store.runs("eod")),
                    "note": "no stored P&L run: run the EOD backfill first"}
        start = start or runs[0]["params"].get("from") or runs[0]["day"]
        end = end or runs[-1]["day"]
        return clean({**cumulative(self.store, start, end), "eod": self.store.runs("eod", start, end)})

    def pnl_eod(self, body: dict) -> dict:
        from ..risk.pnl import backfill
        opts = body.get("options") or {}
        settings = self._settings(opts)
        start = body.get("from")
        end = body.get("to") or start
        if not start:
            raise ApiError("give the first date (from)", 422, "from")
        for d in (start, end):
            try:
                dt.date.fromisoformat(d)
            except (TypeError, ValueError):
                raise ApiError(f"not a date: {d!r}", 422)
        trades = self.desk.repo.trades()

        def job():
            out = backfill(self.desk.lake, trades, start, end, self.store, settings, greeks=bool(body.get("greeks", True)))
            return clean({"runs": out, "from": start, "to": end})

        return {"job": self.desk.jobs.submit("eod", job)}


def risk_routes(desk):
    """(method, regex, handler) of the risk routes, for `server.routes`; the RiskApi is kept on the desk as `desk.risk`."""
    r = getattr(desk, "risk", None)
    if r is None:
        r = desk.risk = RiskApi(desk)
    q1 = lambda q, k, d=None: q.get(k, [d])[0]  # noqa: E731
    return [
        ("POST", r"/api/risk/run", lambda m, q, b: r.run(b)),
        ("GET", r"/api/risk/runs", lambda m, q, b: r.runs(q1(q, "kind"))),
        ("GET", r"/api/risk/runs/([\w.-]+)", lambda m, q, b: r.run_get(m[1])),
        ("POST", r"/api/risk/ladder", lambda m, q, b: r.ladder(b)),
        ("GET", r"/api/risk/scenarios", lambda m, q, b: r.scenarios()),
        ("PUT", r"/api/risk/scenarios", lambda m, q, b: r.put_scenarios(b)),
        ("POST", r"/api/risk/scenarios/run", lambda m, q, b: r.run_scenarios(b)),
        ("POST", r"/api/risk/var", lambda m, q, b: r.var(b)),
        ("GET", r"/api/pnl", lambda m, q, b: r.pnl(q1(q, "from"), q1(q, "to"))),
        ("POST", r"/api/pnl/eod", lambda m, q, b: r.pnl_eod(b)),
    ]
