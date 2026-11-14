"""The desk behind the API: every route is a method here, JSON in and JSON out, no HTTP inside.

Levels and rates cross the API as fractions (0.6 = 60 %, 0.08 = 8 % p.a.), exactly as in the library; the page
shows and takes percent and converts at its edge.

A computation is a job (ADR-0006): the submitting method validates the request and freezes what the desk holds
(`tasks.Frozen`: the market in use, the typed settings, the trades and their fixings), and its `run_<kind>` twin,
called by the worker, rebuilds the same objects from the row and computes. So switching snapshots never changes a run
in progress, and a job can run in another process (`python -m optlab.server.worker`).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np

from optlab import native
from optlab.core import engines, progress
from optlab.core.config import DEFAULT_SEED
from optlab.core.instruments import _BY_ID
from optlab.core.log import get_logger
from optlab.core.market import Market
from optlab.core.snapshot import MarketSnapshot
from optlab.marketdata.derive.correl import correlation_at
from optlab.marketdata.lake import Lake
from optlab.marketdata.names import NameMarkets
from optlab.server.fixings import parse_fixings
from optlab.server.jobs import JobQueue
from optlab.server.persistence import Database
from optlab.server.persistence.desk import DeskRepository
from optlab.server.persistence.replays import ReplayRepository
from optlab.server.persistence.snapshots import SnapshotRepository
from optlab.server.serialize import book_json, clean, events_json, quote_json, result_json, trade_json
from optlab.server.settings import Settings
from optlab.server.tasks import Frozen, envelope, freeze_fixings, market_of, register
from optlab.structured import MASKS, PRESETS, PricingContext, TermSheet, TermSheetError, describe, preset, quote
from optlab.structured.compiler import compile_sheet
from optlab.structured.defaults import STARTING, merged, parameters, resolve, validate_table
from optlab.structured.lifecycle import events
from optlab.structured.multiasset import BasketContext
from optlab.structured.portfolio import Book
from optlab.structured.pricer import price
from optlab.structured.reserves import frozen, remaining
from optlab.structured.schema import SECTIONS, json_schema
from optlab.structured.script.lexer import ScriptError
from optlab.structured.script.parser import parse
from optlab.structured.script.validate import validate
from optlab.structured.trade import Trade

PRECISION = {
    "quick": (20_000, 10_000),
    "standard": (100_000, 25_000),
    "precise": (200_000, 25_000),
    "max": (2_000_000, 25_000),
}  # max: for the compiled engine (the page offers it only then)
FIELD = re.compile(r"^([a-z_]+\.[a-z_]+)\b")
# Names the lake cannot give: flat Black-Scholes markets rescaled to 100, with these defaults until the user sets them
# (Market screen). The rate defaults to the 1-year zero rate of the market in use.
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
        raise ApiError(str(exc), 422, m.group(1) if m else None) from exc
    except (TypeError, ValueError) as exc:
        raise ApiError(str(exc), 422) from exc


def _pair(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


class Desk:
    def __init__(
        self,
        store=None,
        calibration=None,
        workers: int | None = None,
        max_jobs: int | None = None,
        lake=None,
        engine: str | None = None,
        settings: Settings | None = None,
        run_jobs: bool = True,
    ):
        """`engine`: where Monte Carlo paths come from (numpy, cpu, gpu, auto; see optlab.core.engines), for every job of this
        process. None keeps the library's default. `store` (the database file), `calibration` and `lake` override
        `settings` (`Settings.load()` when not given), from which every other path comes. `run_jobs`: run the queue's
        jobs in this process (the embedded worker, `max_jobs` at a time) unless another worker already serves this
        database; False for an api that leaves them to `python -m optlab.server.worker`."""
        from optlab.server.risk_api import RiskApi

        if engine is not None:
            engines.set_default(engine)
        self.settings = settings or Settings.load()
        self.db = Database(store or self.settings.database)
        self.repo = DeskRepository(self.db)
        self.replays = ReplayRepository(self.db)
        self.lake = Lake(lake or self.settings.lake)
        self.names = NameMarkets(self.lake)
        self.shared = False  # listening beyond this machine: raw quotes and levels stay private
        self.market_store = SnapshotRepository(self.db)
        self.calibration = Path(calibration) if calibration else self.settings.calibration
        self.ctx = PricingContext.from_calibration(self.calibration)
        self._contexts: dict[str, PricingContext] = {}  # markets of earlier submissions, as they were
        self.files: dict[str, Path] = {}  # snapshot id -> calibration file (its model-risk table)
        self._register(self.calibration)
        for path in sorted(self.settings.results.glob("*_calibration.json")):
            if path.resolve() != self.calibration.resolve():
                self._register(path)
        self.jobs = JobQueue(self.db)
        self.workers = workers
        self.max_jobs = max_jobs or self.settings.job_concurrency
        self.started = dt.datetime.now(dt.UTC)
        self.public_url = None  # set when a tunnel is open
        self.lan_blockers: list[str] = []  # what stops other machines, found at start (Windows firewall)
        self.risk = RiskApi(self)
        self.worker: Any = None
        if run_jobs:
            self.start_worker()

    # --- jobs ---
    def start_worker(self) -> bool:
        """Run the queue in this process (the embedded worker); False when another worker serves this database."""
        from optlab.server.worker import Worker

        worker = Worker(self, role="embedded", concurrency=self.max_jobs)
        if worker.start():
            self.worker = worker
            return True
        return False

    def close(self) -> None:
        """Stop the embedded worker (its running jobs are asked to stop, then marked interrupted) and release the
        database file."""
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self.db.close()

    def _freeze(self, fixings: dict | None = None, trades: list | None = None, costs: bool = False) -> Frozen:
        """What a job needs of the desk as it is now: the market in use, the typed basket settings, and, when asked,
        the cost table's version, the fixings and the trades (by version)."""
        table = self.repo.defaults() if costs else None
        return Frozen(
            snapshot_id=self.ctx.snapshot_id,
            basket=self.repo.setting("basket", {}) or {},
            costs=table["version"] if table else None,
            fixings=freeze_fixings(fixings),
            trades=None if trades is None else [[t.trade_id, t.version] for t in trades],
        )

    def _submit(self, kind: str, params: dict, frozen: Frozen | None = None) -> dict:
        job_id, reused = self.jobs.submit(kind, clean(params), (frozen or Frozen()).to_json())
        return {"job": job_id, "reused": reused}

    def context_of(self, frozen: Frozen) -> PricingContext:
        """The market a job was submitted on: the one in use, one used before (the same object), or the stored
        snapshot."""
        sid = frozen.snapshot_id
        if sid is None or sid == self.ctx.snapshot_id:
            return self.ctx
        ctx = self._contexts.get(sid)
        if ctx is None:
            try:
                ctx = PricingContext.from_snapshot(self.market_store.get(sid))
            except KeyError as exc:
                raise ApiError(str(exc).strip("'\""), 404) from exc
            self._keep_context(ctx)
        return ctx

    def _keep_context(self, ctx: PricingContext) -> None:
        if ctx.snapshot_id:
            kept = list(self._contexts.items())[-3:]  # a few markets, with their recalibrated leverages
            self._contexts = {**dict(kept), ctx.snapshot_id: ctx}

    def _trades_of(self, frozen: Frozen) -> list[Trade]:
        return [self.repo.trade(trade_id, int(version)) for trade_id, version in frozen.trades or []]

    @staticmethod
    def _names_of(trades) -> list[str]:
        names: list[str] = []
        for t in trades:
            sheet = t.term_sheet()
            observed = (
                sheet.underlyings if sheet is not None else tuple(validate(parse(t.product["script"])).underlying_names)
            )
            names += [n for n in observed if n not in names]
        return names

    def _market(self, ctx: PricingContext) -> dict:
        return market_of(ctx.snapshot_id, self._origin(ctx.snapshot_id), ctx.valuation)

    def job(self, job_id: str) -> dict:
        """A job seen from Python: its state, and once done its result with the market and the method beside it."""
        try:
            j = self.jobs.get(job_id)
        except KeyError as exc:
            raise ApiError(str(exc), 404) from exc
        env = j.get("result") or {}
        return clean({**j, "result": env.get("result"), "market": env.get("market"), "method": env.get("method")})

    def cancel(self, job_id: str) -> dict:
        try:
            state = self.jobs.cancel(job_id)
        except KeyError as exc:
            raise ApiError(str(exc), 404) from exc
        return {"ok": True, "state": state}

    @property
    def last_book(self) -> dict | None:
        """The newest book valuation on the market in use (the book report and the desk snapshot show it)."""
        env = self.jobs.latest("book", lambda c: c.get("snapshot_id") == self.ctx.snapshot_id)
        return None if env is None else env["result"]

    def _register(self, path: Path) -> None:
        """Keep a calibration file's market in the snapshot store (immutable, by content), so the desk can switch
        to it later; files without a snapshot time (the simulated market) take the current valuation date."""
        try:
            try:
                snap = MarketSnapshot.from_calibration_file(path, provenance=False)
            except ValueError:
                snap = MarketSnapshot.from_calibration_file(
                    path, valuation=self.ctx.valuation, source="simulated", provenance=False
                )
            self.files[self.market_store.put(snap)] = Path(path)
        except (OSError, KeyError, ValueError) as exc:
            LOG.warning("calibration %s not registered: %s", path, exc)

    # --- status and market ---
    def health(self) -> dict:
        return {
            "ok": True,
            "snapshot_id": self.ctx.snapshot_id,
            "valuation": self.ctx.valuation.isoformat(),
            "origin": self._origin(self.ctx.snapshot_id),
            "workers": self.workers,
            "jobs": self.jobs.active(everyone=True),
            "worker": None if self.worker is None else self.worker.worker_id,
            "store": str(self.db.path),
            "started": self.started.isoformat(timespec="seconds"),
            "public_url": self.public_url,
            "lan_blockers": self.lan_blockers,
            "engine": native.summary(),
        }

    def context(self) -> dict:
        c = self.ctx
        tenors = [0.25, 0.5, 1.0, 1.5, 2.0]
        return clean(
            {
                "snapshot_id": c.snapshot_id,
                "valuation": c.valuation,
                "underlying": c.underlying,
                "origin": self._origin(c.snapshot_id),
                "spot": c.market.spot,
                "t_max": c.t_max,
                "maturities": c.surface.maturities,
                "atm_vols": {f"{T:g}": c.atm_vol(T) for T in tenors},
                "zero_rates": {f"{T:g}": float(c.market.zero_rate(T)) for T in tenors},
                "carry": {f"{T:g}": float(c.market.carry_rate(T)) for T in tenors},
                "slv_mix": c.slv.mix,
                "heston": c.heston.to_dict(),
            }
        )

    def _origin(self, snapshot_id: str | None) -> dict:
        """How much of the active market was observed, from its provenance."""
        try:
            snap = self.market_store.get(str(snapshot_id).split("+")[0])
        except KeyError:
            return {"surface": "unknown", "source": None}
        prov = snap.provenance or {}
        if "surface" in prov:
            return {
                "surface": prov["surface"]["origin"],
                "layer": prov["surface"]["layer"],
                "source": snap.source,
                "extrapolated_from": prov["surface"]["extrapolated_from"],
                "carry": prov["carry"]["origin"],
                "discount": prov["discount"]["origin"],
                "heston": prov["heston"]["origin"],
                "leverage": prov["leverage"]["origin"],
            }
        return {
            "surface": "synthetic" if snap.source == "simulated" else "observed",
            "layer": "calibration",
            "source": snap.source,
        }

    def snapshots(self) -> dict:
        rows = self.market_store.list()
        for r in rows:
            r["active"] = r["snapshot_id"] == self.ctx.snapshot_id
            r["file"] = self.files[r["snapshot_id"]].name if r["snapshot_id"] in self.files else None
        return {
            "snapshots": sorted(rows, key=lambda r: (r["valuation"], r["asof"]), reverse=True),
            "active": self.ctx.snapshot_id,
        }

    def use_snapshot(self, body: dict) -> dict:
        sid = str(body.get("snapshot_id") or "")
        try:
            snap = self.market_store.get(sid)
        except KeyError as exc:
            raise ApiError(str(exc).strip("'\""), 404) from exc
        self._keep_context(self.ctx)  # jobs submitted on it still run on it
        self.ctx = PricingContext.from_snapshot(snap)
        LOG.info("desk now on market %s (valuation %s)", self.ctx.snapshot_id, self.ctx.valuation)
        return self.context()

    def _context_of(self, snapshot_id: str | None) -> PricingContext:
        """The market in use, or another stored snapshot read without switching the desk to it."""
        if snapshot_id in (None, "", "in-use", self.ctx.snapshot_id):
            return self.ctx
        try:
            return PricingContext.from_snapshot(self.market_store.get(str(snapshot_id)))
        except KeyError as exc:
            raise ApiError(str(exc).strip("'\""), 404) from exc

    def smile(self, T: float, snapshot_id: str | None = None) -> dict:
        ctx, T = self._context_of(snapshot_id), float(T)
        F = float(ctx.market.F(T))
        k = np.linspace(-0.6, 0.3, 61)
        return clean({"T": T, "forward": F, "moneyness": np.exp(k), "iv": ctx.surface.iv(k, T)})

    def term_structure(self, snapshot_id: str | None = None) -> dict:
        ctx = self._context_of(snapshot_id)
        T = np.linspace(0.05, ctx.t_max, 120)
        return clean(
            {
                "T": T,
                "atm_vol": [ctx.atm_vol(t) for t in T],
                "forward": ctx.market.F(T) / ctx.market.spot,
                "discount": ctx.market.D(T),
            }
        )

    def model_risk(self) -> dict:
        path = self.files.get(self.ctx.snapshot_id) if self.ctx.snapshot_id else None
        if path is None or not path.exists():
            return {"exotics": {}, "errors": [], "note": "no model-risk table stored with this snapshot"}
        cal = json.loads(path.read_text(encoding="utf-8"))
        return clean(
            {
                "exotics": cal.get("exotics", {}),
                "errors": cal.get("errors", []),
                "note": cal.get("meta", {}).get("note"),
            }
        )

    # --- the data lake ---
    def _private(self, what: str) -> None:
        if self.shared:
            raise ApiError(f"{what} stay on the owner's machine: this desk is shared beyond it", 403)

    def data_overview(self) -> dict:
        from optlab.core.instruments import UNIVERSE
        from optlab.marketdata.quality import open_issues

        lake = self.lake
        if not lake.has("instruments") and not lake.has("prices_eod"):
            return {"available": False, "hint": "run scripts/lake_update.py"}
        cov = lake.coverage()
        cov = cov[~cov["dataset"].isin(("ingestion_runs",))]
        issues = open_issues(lake, order="obs_date DESC")
        last = {}
        if lake.has("prices_eod"):
            prices = lake.read("prices_eod", columns="instrument, obs_date, origin")
            last = {
                str(name): {"last": g["obs_date"].max(), "n": len(g), "origin": g["origin"].iloc[0]}
                for name, g in prices.groupby("instrument")
            }
        if lake.has("fx_spots"):
            rates = lake.read("fx_spots", where="source = 'ecb'", columns="pair, obs_date, origin")
            last.update(
                {
                    str(name): {"last": g["obs_date"].max(), "n": len(g), "origin": g["origin"].iloc[0]}
                    for name, g in rates.groupby("pair")
                }
            )
        by_check = (
            issues.groupby(["dataset", "check_name", "severity"]).size().reset_index(name="n") if len(issues) else None
        )
        return clean(
            {
                "available": True,
                "shared": self.shared,
                "lake": None if self.shared else str(lake.root),
                "coverage": cov.astype({"first": str, "last": str, "ingested": str}).to_dict("records"),
                "instruments": [
                    {
                        "id": i.id,
                        "name": i.name,
                        "asset_class": i.asset_class,
                        "currency": i.currency,
                        "place": i.place,
                        "vol_index": i.vol_index,
                        "enabled": i.enabled,
                        **last.get(i.id, {}),
                    }
                    for i in UNIVERSE
                ],
                "issues": by_check.to_dict("records") if by_check is not None else [],
                "recent_issues": issues.head(30)[["dataset", "item", "check_name", "severity", "detail", "obs_date"]]
                .astype({"obs_date": str})
                .to_dict("records")
                if len(issues)
                else [],
                "raw_files": len(lake.manifest()),
            }
        )

    def data_vol_index(self, name: str, start: str | None = None) -> dict:
        self._private("Index levels")
        where, params = "index_name = ?", [name.upper()]
        if start:
            where, params = where + " AND obs_date >= ?", [*params, dt.date.fromisoformat(start)]
        rows = self.lake.read(
            "vol_indices", where=where, columns="obs_date, close, origin, source", order="obs_date", params=params
        )
        return clean(
            {
                "name": name,
                "dates": rows["obs_date"].astype(str).tolist(),
                "close": rows["close"].tolist(),
                "origin": rows["origin"].iloc[0] if len(rows) else None,
                "source": rows["source"].iloc[0] if len(rows) else None,
            }
        )

    def data_baskets(self) -> dict:
        """The latest CvC and PvP pseudo-quotes, at one year: origin synthetic, in % of notional."""
        if not self.lake.has("basket_spreads"):
            return {"rows": [], "day": None}
        rows = self.lake.read("basket_spreads", where="tenor = 1.0", order="obs_date DESC, basket, kind, strike")
        if rows.empty:
            return {"rows": [], "day": None}
        day = rows["obs_date"].iloc[0]
        last = rows[rows["obs_date"] == day]
        keep = ["basket", "kind", "strike", "price", "price_se", "basket_vol", "rho", "implied_rho", "origin"]
        return clean({"day": str(day)[:10], "rows": last[keep].to_dict("records")})

    def market_from_lake(self, body: dict) -> dict:
        """A market at a past date (a job: the leverage takes seconds), stored like any snapshot; /api/market/use prices on it."""
        name, day = str(body.get("underlying", "SPX")), body.get("date")
        try:
            day = dt.date.fromisoformat(str(day))
        except ValueError:
            raise ApiError("a date such as 2020-03-23 is needed", 422, "date") from None
        params = {
            "underlying": name,
            "date": day,
            "leverage": bool(body.get("leverage", True)),
            "t_max": float(body.get("t_max", 3.0)),
        }
        return self._submit("market", params)

    def run_market(self, p: dict, f: Frozen) -> dict:
        from optlab.marketdata.pit import market_at

        name, day = p["underlying"], dt.date.fromisoformat(p["date"])
        progress.step(f"market of {name} on {day}")
        snap = market_at(self.lake, name, day, leverage="calibrate" if p["leverage"] else "flat", T_max=p["t_max"])
        sid = self.market_store.put(snap)
        out = clean(
            {
                "snapshot_id": sid,
                "valuation": day,
                "underlying": name,
                "provenance": snap.provenance,
                "source": snap.source,
            }
        )
        method = {"name": "point_in_time", "leverage": "calibrate" if p["leverage"] else "flat"}
        return envelope(market_of(sid, self._origin(sid), day), method, out)

    def fixings_from_lake(self, body: dict) -> dict:
        """Real closes of the lake as desk fixings, in the active market's scale."""
        from optlab.marketdata.pit import real_close, real_fixings

        und = str(body.get("underlying", self.ctx.underlying)).upper()
        ctx = self.ctx
        start = body.get("start") or (ctx.valuation - dt.timedelta(days=800)).isoformat()
        real = real_fixings(self.lake, und, start, ctx.valuation)
        if not real:
            raise ApiError(f"no close of {und} in the lake between {start} and {ctx.valuation}", 404)
        snap = self.market_store.get(str(ctx.snapshot_id).split("+")[0])
        scale = snap.spot_scale if und == ctx.underlying else None
        if not scale:
            scale = real_close(self.lake, und, ctx.valuation)[0] / float(ctx.market.spot)
        levels = {d: v / scale for d, v in real.items()}
        return {
            "ok": True,
            "stored": self.repo.put_fixings(und, levels, "lake"),
            "first": min(levels),
            "last": max(levels),
            "origin": "observed",
        }

    # --- Vol Fitting Lab ---
    def lab_days(self) -> dict:
        from optlab.marketdata.vollab import DEFAULTS, MODELS, lab_days

        return clean(
            {
                "available": self.lake.has("option_quotes"),
                "sets": lab_days(self.lake),
                "models": MODELS,
                "defaults": DEFAULTS,
                "shared": self.shared,
            }
        )

    def lab_quotes(self, name: str, day: str, source: str | None = None) -> dict:
        from optlab.marketdata.vollab import quote_table

        self._private("Option quotes")
        try:
            dt.date.fromisoformat(day)
            return clean(quote_table(self.lake, name, day, source))
        except (KeyError, ValueError) as exc:
            raise ApiError(str(exc).strip("'\""), 404) from exc

    def lab_fit(self, body: dict) -> dict:
        from optlab.marketdata.vollab import MODELS

        name, day, source = str(body.get("underlying", "SPX")), str(body.get("day", "")), body.get("source")
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            raise ApiError("a day such as 2022-09-15 is needed", 422, "day") from None
        models = tuple(m for m in (body.get("models") or MODELS) if m in MODELS)
        if not models:
            raise ApiError("choose at least one model", 422)
        settings = {k: body[k] for k in ("n_particles", "n_paths", "mix", "seed") if k in body}
        params = {"underlying": name, "day": day, "source": source, "models": list(models), "settings": settings}
        return self._submit("fit", params)

    def run_fit(self, p: dict, f: Frozen) -> dict:
        from optlab.marketdata.vollab import public_summary, run_fit

        progress.step(f"fit of {p['underlying']} on {p['day']}")
        out = clean(
            public_summary(
                run_fit(self.lake, p["underlying"], p["day"], p["source"], tuple(p["models"]), p["settings"])
            )
        )
        method = {"name": "fit", "models": p["models"], **p["settings"]}
        return envelope(market_of(None, "observed", p["day"]), method, out)

    def lab_runs(self, underlying: str | None = None) -> dict:
        from optlab.marketdata.vollab import list_runs

        return clean({"runs": list_runs(self.lake, underlying)})

    def lab_reproduce(self, calibration_id: str) -> dict:
        return self._submit("reproduce", {"calibration_id": calibration_id})

    def run_reproduce(self, p: dict, f: Frozen) -> dict:
        from optlab.marketdata.vollab import reproduce

        progress.step(f"fit {p['calibration_id']} again")
        out = clean(reproduce(self.lake, p["calibration_id"]))
        day = out.get("day") if isinstance(out, dict) else None
        return envelope(market_of(None, "observed", day), {"name": "fit", "reproduce": True}, out)

    # --- replay ---
    def replay_runs(self) -> dict:
        runs = []
        stored = [(stem, data, "desk") for stem, data in self.replays.all()]
        files = [
            (path.stem, json.loads(path.read_text(encoding="utf-8")), "versioned")
            for path in sorted(self.settings.results.glob("replay*.json"))
        ]
        for stem, data, kind in stored + files:
            for key, r in data.items() if kind == "versioned" else (("run", data),):
                runs.append(
                    {
                        "id": f"{stem}/{key}" if kind == "versioned" else stem,
                        "kind": kind,
                        "trade_id": r.get("trade_id"),
                        "underlyings": r.get("underlyings"),
                        "n_valuations": r.get("n_valuations"),
                        "strike": (r.get("inception") or {}).get("strike"),
                        "events": len(r.get("events") or []),
                        "seconds": (r.get("seconds") or {}).get("total"),
                    }
                )
        return clean({"available": self.lake.has("prices_eod"), "runs": runs, "presets": list(PRESETS)})

    def replay_get(self, run_id: str) -> dict:
        stem, _, key = run_id.partition("/")
        if not re.fullmatch(r"[\w.-]+", stem):
            raise ApiError("unknown run", 404)
        if key:  # a versioned file of results/ holds several runs; the id names one of those files, never a path
            path = {f.stem: f for f in self.settings.results.glob("replay*.json")}.get(stem)
            data = json.loads(path.read_text(encoding="utf-8")).get(key) if path is not None else None
        else:
            data = self.replays.get(stem)
        if data is None:
            raise ApiError(f"no replay run {run_id}", 404)
        return clean(data)

    def replay_run(self, body: dict) -> dict:
        table = self.repo.defaults()
        self._replay_inputs(body, table)  # refuses a bad request now
        return self._submit("replay", body, Frozen(costs=table["version"] if table else None))

    def run_replay(self, p: dict, f: Frozen) -> dict:
        from optlab.marketdata.replay import replay, result_json, strike_trade

        sheet, strike, end, cfg, trade_id, costs = self._replay_inputs(
            p, self.repo.defaults(f.costs) if f.costs is not None else None
        )
        progress.step(f"replay of {trade_id} ({cfg.model})")
        trade, info = strike_trade(self.lake, trade_id, sheet, strike, float(p.get("quantity", 1.0)), cfg, costs=costs)
        out = clean(result_json(replay(self.lake, trade, end, cfg, info)))
        self.replays.save(f"replay-{trade_id}-{cfg.model}", out)
        method = {"name": "replay", "model": cfg.model, "n_paths": cfg.n_paths, "every": cfg.reval_every}
        return envelope(market_of(None, "observed", strike), method, out)

    def _replay_inputs(self, body: dict, table: dict | None):
        """(sheet, strike date, end, config, trade id, cost inputs) of a replay request, or the 422 that says why not."""
        from optlab.marketdata.replay import ReplayConfig

        name = str(body.get("preset", "barrier_reverse_convertible"))
        if name not in PRESETS:
            raise ApiError(f"unknown preset {name!r}", 422, "preset")
        changes = {k: v for k, v in (body.get("changes") or {}).items() if k.count(".") == 1}
        underlying = str(body.get("underlying", "SPX"))
        try:
            sheet = preset(name, **{"general.underlying": underlying, **changes})
            strike = dt.date.fromisoformat(str(body.get("strike", "")))
            end = dt.date.fromisoformat(body["end"]) if body.get("end") else None
        except (TermSheetError, ValueError) as exc:
            raise ApiError(str(exc), 422) from exc
        cfg = ReplayConfig(
            model=str(body.get("model", "lv")),
            n_paths=int(body.get("n_paths", 20_000)),
            batch=10_000,
            reval_every=max(int(body.get("every", 5)), 1),
            leverage_every=max(int(body.get("leverage_every", 1)), 1),
            greeks=bool(body.get("greeks", True)),
        )
        if cfg.model not in ("lv", "slv"):
            raise ApiError("the replay runs under lv or slv", 422, "model")
        trade_id = f"{name}-{underlying}-{strike}".replace("_", "-")
        costs = (
            resolve(table, sheet).inputs()
            if body.get("costs", True) and cfg.model == "lv" and not resolve(table, sheet).missing
            else None
        )
        return sheet, strike, end, cfg, trade_id, costs

    # --- other underlyings, for worst-ofs ---
    def basket_settings(self) -> dict:
        s = self.repo.setting("basket", {})
        r = float(self.ctx.market.zero_rate(1.0))
        return clean(
            {
                "names": s.get("names", {}),
                "corr": s.get("corr", {}),
                "calibrated": self.ctx.underlying,
                "defaults": {**BASKET_DEFAULTS, "r": r},
            }
        )

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
            raise ApiError(str(exc), 422) from exc
        settings = {"names": names, "corr": corr}
        try:  # the full matrix must be a correlation matrix (positive definite)
            self._basket_ctx((self.ctx.underlying, *names), settings)
        except ValueError as exc:
            raise ApiError(f"the correlations do not form a valid matrix: {exc}", 422) from exc
        self.repo.put_setting("basket", settings)
        return self.basket_settings()

    def _lake_context(self, name: str, day, payment: str | None = None) -> tuple | None:
        """(context, snapshot) of a name from the lake at a date, on its quanto market when `payment` is another currency,
        or None when the lake cannot build it."""
        if not self.lake.has("prices_eod"):
            return None
        try:
            return self.names.context(name, day, payment)
        except KeyError as exc:
            LOG.info("no market of %s on %s from the lake: %s", name, day, exc)
            return None

    @staticmethod
    def _name_row(market, atm_vol: float, source: str, origin: dict | None = None) -> dict:
        """What a price used for one name, as the page shows it: rates and vol at one year, never a real level."""
        r = float(market.zero_rate(1.0))
        return {
            "vol": atm_vol,
            "q": r - float(market.carry_rate(1.0)),
            "r": r,
            "source": source,
            "set": source == "input",
            "origin": (origin or {}).get("surface"),
            "layer": (origin or {}).get("layer"),
            "carry_origin": (origin or {}).get("carry"),
            "extrapolated_from": (origin or {}).get("extrapolated_from"),
        }

    @staticmethod
    def _snapshot_origin(snap) -> dict:
        p = snap.provenance
        return {
            "surface": p["surface"]["origin"],
            "layer": p["surface"]["layer"],
            "carry": p["carry"]["origin"],
            "extrapolated_from": p["surface"]["extrapolated_from"],
        }

    def _basket_correlation(self, known, day) -> tuple[dict, dict | None]:
        """{pair: correlation} measured on the lake for the names it holds, with how it was measured."""
        known = list(known)
        if len(known) < 2:
            return {}, None
        try:
            matrix, meta = correlation_at(self.lake, known, day)
        except KeyError as exc:
            LOG.info("no correlation of %s on %s from the lake: %s", ", ".join(known), day, exc)
            return {}, None
        return {
            _pair(a, known[j]): float(matrix[i, j]) for i, a in enumerate(known) for j in range(i + 1, len(known))
        }, meta

    @staticmethod
    def _quanto(name: str, payment: str | None) -> bool:
        """Whether a name paid in `payment` needs a quanto market."""
        native = _BY_ID[name].currency if name in _BY_ID else None
        return bool(payment and native and native != payment)

    def _basket_ctx(
        self, names, settings: dict | None = None, ctx: PricingContext | None = None, payment: str | None = None
    ):
        """Each name on its own market: the one in use for its name, the lake's at the same date for the others, and flat
        Black-Scholes for what the lake cannot give. A name typed in the Market screen stays what was typed. Correlations come
        the same way: typed, else measured on the lake, else the default. Returns (context, what the page shows)."""
        ctx = ctx or self.ctx
        s = settings if settings is not None else self.repo.setting("basket", {})
        typed, day = s.get("names", {}), ctx.valuation
        r_default = float(ctx.market.zero_rate(1.0))
        names = tuple(dict.fromkeys(names))
        order = (ctx.underlying, *(n for n in names if n != ctx.underlying)) if ctx.underlying in names else names
        markets, vols, shown, measured, contexts = {}, {}, {}, [], {}
        unadjusted = False  # a name in another currency without its quanto market (typed or flat inputs)
        for n in order:
            quanto = self._quanto(n, payment)
            active = n == ctx.underlying and not quanto
            got = None if active or n in typed else self._lake_context(n, day, payment if quanto else None)
            unadjusted |= quanto and got is None
            if active:
                markets[n], vols[n], contexts[n] = ctx.market, ctx.surface, ctx
                shown[n] = self._name_row(ctx.market, ctx.atm_vol(1.0), "active", self._origin(ctx.snapshot_id))
                measured.append(n)
            elif got is not None:
                c, snap = got
                markets[n], vols[n], contexts[n] = c.market, c.surface, c
                shown[n] = self._name_row(c.market, c.atm_vol(1.0), "lake", self._snapshot_origin(snap))
                measured.append(n)
            else:
                p = {**BASKET_DEFAULTS, "r": None, **typed.get(n, {})}
                r = r_default if p["r"] is None else p["r"]
                markets[n], vols[n] = Market.flat(100.0, r, p["q"]), p["vol"]
                shown[n] = {
                    "vol": p["vol"],
                    "q": p["q"],
                    "r": r,
                    "set": n in typed,
                    "source": "input" if n in typed else "default",
                    "origin": "synthetic",
                    "layer": "flat",
                }
        lake_rho, meta = self._basket_correlation(measured, day) if self.lake.has("prices_eod") else ({}, None)
        corr, rho, rho_source = np.eye(len(order)), {}, {}
        for i, a in enumerate(order):
            for j in range(i + 1, len(order)):
                key = _pair(a, order[j])
                if key in s.get("corr", {}):
                    value, rho_source[key] = s["corr"][key], "input"
                elif key in lake_rho:
                    value, rho_source[key] = lake_rho[key], "lake"
                else:
                    value, rho_source[key] = BASKET_DEFAULTS["corr"], "default"
                corr[i, j] = corr[j, i] = rho[key] = value
        held = payment if payment and not unadjusted else None
        return BasketContext(order, markets, vols, corr, day, contexts, self.names.correlations, held), {
            "kind": "basket",
            "names": shown,
            "corr": rho,
            "corr_source": rho_source,
            "correlation": meta,
            "calibrated": ctx.underlying,
        }

    def _context_for(self, sheet: TermSheet, ctx: PricingContext, settings: dict | None = None):
        names = sheet.underlyings
        first = names[0]  # read before the length test, which pyright narrows to an empty tuple
        if len(names) > 1:
            return self._basket_ctx(names, settings, ctx=ctx, payment=sheet.payment_currency)
        return self._single_ctx(first, ctx, sheet.payment_currency)

    def _single_ctx(self, name: str, ctx: PricingContext, payment: str | None = None):
        """A name on the market in use when it is that name's, else on the lake's at the same date (its quanto market when it
        pays in another currency). Without one the market in use comes back, and the quote says what is missing."""
        quanto = self._quanto(name, payment)
        if name == ctx.underlying and not quanto:
            return ctx, None
        got = self._lake_context(name, ctx.valuation, payment if quanto else None)
        if got is None:
            return ctx, None
        c, snap = got
        return c, {
            "kind": "single",
            "names": {name: self._name_row(c.market, c.atm_vol(1.0), "lake", self._snapshot_origin(snap))},
            "corr": {},
            "calibrated": ctx.underlying,
        }

    # --- term sheets ---
    def presets(self) -> dict:
        out = []
        for name, (mask, _) in PRESETS.items():
            out.append(
                {
                    "name": name,
                    "type": mask,
                    "label": name.replace("_", " ").title(),
                    "sheet": preset(name).to_dict(json_safe=True),
                }
            )
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
        costs = resolve(self.repo.defaults(), near).as_json() if near is not None else None
        bounds = {}
        for s in MASKS[mask].sections:
            for f in SECTIONS[s].fields:
                bounds[f"{s}.{f.name}"] = {"min": f.minimum, "max": f.maximum, "default": f.default}
        return clean(
            {
                "type": mask,
                "sheet": sheet.to_dict(json_safe=True) if sheet else None,
                "form": shown,
                "bounds": bounds,
                "error": error,
                "costs": costs,
            }
        )

    def schema(self) -> dict:
        return json_schema()

    # --- defaults of the hedging costs ---
    def cost_defaults(self) -> dict:
        """The table in use, what is still a starting value, the fields of the form and the saved versions."""
        stored = self.repo.defaults()
        return clean(
            {
                "groups": merged(stored),
                "starting": STARTING,
                "version": stored["version"] if stored else None,
                "recorded_at": stored["recorded_at"] if stored else None,
                "versions": self.repo.default_versions(),
                "fields": [
                    {
                        "name": f.name,
                        "label": f.label,
                        "unit": f.unit,
                        "kind": f.kind,
                        "min": f.minimum,
                        "max": f.maximum,
                    }
                    for f in parameters()
                ],
            }
        )

    def put_cost_defaults(self, body: dict) -> dict:
        try:
            groups = validate_table(body.get("groups") or {})
        except (TypeError, KeyError, ValueError) as exc:
            raise ApiError(str(exc), 422) from exc
        self.repo.save_defaults(groups)
        return self.cost_defaults()

    def _cost_inputs(self, sheet: TermSheet, table: dict | None = None, frozen: bool = False):
        """The costs of a sheet, or an error naming what the trader must enter (above the last tier, or a name without a
        group). `frozen`: `table` is a job's version of the cost table (None: the starting values), else the latest."""
        got = resolve(table if frozen else self.repo.defaults(), sheet)
        if got.missing:
            raise ApiError(
                " ".join(got.alerts) + f" Missing: {', '.join(got.missing)}.", 422, f"costs.{got.missing[0]}"
            )
        return got.inputs()

    # --- quotes ---
    def _options(self, opts: dict) -> dict:
        n_paths, batch = PRECISION.get(opts.get("precision", "standard"), PRECISION["standard"])
        return {
            "n_paths": int(opts.get("n_paths") or n_paths),
            "batch": batch,
            "control": "default" if opts.get("control", True) else None,
            "with_greeks": bool(opts.get("greeks", False)),
            "fast": bool(opts.get("fast", False)),
            "slv_vega": bool(opts.get("slv_vega", False)),
            "control_forwards": bool(opts.get("control_forwards", False)),
            "model": opts.get("model") or None,
            "sticky": opts.get("sticky", "moneyness"),
            "costs": bool(opts.get("costs", True)),
            "mix_band": (0.0, 0.25, 0.5, 0.75) if opts.get("mix_band") else None,
            "control_paths": min(int(opts.get("n_paths") or n_paths), 100_000),
        }

    def _quote(self, sheet: TermSheet, ctx: PricingContext, o: dict, fixings, costs=None, settings: dict | None = None):
        qctx, basket = self._context_for(sheet, ctx, settings)
        q = quote(
            sheet,
            qctx,
            model=o["model"],
            control=o["control"],
            n_paths=o["n_paths"],
            batch=o["batch"],
            control_paths=o["control_paths"],
            fixings=fixings,
            with_greeks=o["with_greeks"],
            slv_vega=o["slv_vega"],
            workers=self.workers,
            control_forwards=o["control_forwards"],
            fast=o["fast"],
            sticky=o["sticky"],
            mix_band=None if isinstance(qctx, BasketContext) else o["mix_band"],
            costs=costs,
        )
        return q, basket

    def quote(self, body: dict) -> dict:
        sheet = _sheet(body.get("sheet") or {}, strict=True)
        options = body.get("options") or {}
        if self._options(options)["costs"]:
            self._cost_inputs(sheet)  # what the trader must enter is refused now, not in the job
        frozen = self._freeze(fixings=self._fixings_for(sheet, self.ctx), costs=True)
        return self._submit("quote", {"sheet": body.get("sheet") or {}, "options": options}, frozen)

    def run_quote(self, p: dict, f: Frozen) -> dict:
        sheet = _sheet(p["sheet"], strict=True)
        ctx = self.context_of(f)
        o = self._options(p["options"])
        table = self.repo.defaults(f.costs) if f.costs is not None else None
        costs = self._cost_inputs(sheet, table, frozen=True) if o["costs"] else None
        progress.step(f"quote of a {sheet.mask.label}")
        q, basket = self._quote(sheet, ctx, o, f.fixings_by_date(), costs, f.basket)
        out = quote_json(q)
        out["sheet"] = sheet.to_dict(json_safe=True)
        out["options"] = clean(o)
        out["basket"] = clean(basket)
        out["quote_id"] = self.repo.save_quote(out["sheet"], out)
        method = {"name": q.decision.method, "model": q.decision.model, "n_paths": q.decision.n_paths}
        return envelope(self._market(ctx), method, out)

    def _fixings_for(self, sheet: TermSheet, ctx: PricingContext | None = None) -> dict | None:
        ctx = ctx or self.ctx
        strike = sheet.get("general.strike_date")
        if strike is None or strike >= ctx.valuation:
            return None
        return self._known_fixings(sheet.underlyings, ctx.valuation)

    def _known_fixings(self, names, valuation, stored: dict | None = None) -> dict | None:
        """The stored fixings of a product's names up to a date: the store holds other names, and later dates when the market
        in use is an older one. `stored`: the fixings a job froze (else the store's)."""
        stored = self.repo.fixings() if stored is None else stored
        out = {
            n: {d: v for d, v in stored[n].items() if dt.date.fromisoformat(str(d)) <= valuation}
            for n in names
            if n in stored
        }
        return {n: levels for n, levels in out.items() if levels} or None

    def quotes(self) -> dict:
        return {"quotes": self.repo.quotes()}

    def saved_quote(self, quote_id: str) -> dict:
        try:
            return self.repo.quote(quote_id)
        except KeyError as exc:
            raise ApiError(str(exc), 404) from exc

    # --- trades and book ---
    def trades(self) -> dict:
        return {"trades": [trade_json(t) for t in self.repo.trades()]}

    def trade(self, trade_id: str) -> dict:
        """One trade: its latest version, every version, its term sheet as a form, the quote it came from."""
        t = self.repo.trade(trade_id)
        sheet = t.term_sheet()
        quote_id = next((tag[6:] for tag in t.tags if tag.startswith("quote:")), None)
        return clean(
            {
                "trade": trade_json(t),
                "versions": self.repo.versions(trade_id),
                "quote_id": quote_id,
                "reserve": remaining(t, self.ctx.valuation),
                "form": describe(sheet.mask.name, sheet) if sheet is not None else None,
                "script": None if sheet is not None else t.product.get("script"),
            }
        )

    def value_trade(self, trade_id: str, body: dict) -> dict:
        """Mark one trade to the current market (a job): its price as % of notional and its value in currency,
        with the past fixings of a running trade."""
        t = self.repo.trade(trade_id)
        sheet = self._upfront(t)
        frozen = self._freeze(fixings=self._fixings_for(sheet, self.ctx), trades=[t])
        return self._submit("value", {"trade_id": trade_id, "options": body.get("options") or {}}, frozen)

    @staticmethod
    def _upfront(t: Trade) -> TermSheet:
        sheet = t.term_sheet()
        if sheet is None:
            raise ApiError("a script trade is valued with the book", 422)
        if sheet.get("wrapper.solve") != "upfront":
            sheet = sheet.replace(**{"wrapper.solve": "upfront"})
        return sheet

    def run_value(self, p: dict, f: Frozen) -> dict:
        (t,) = self._trades_of(f)
        sheet = self._upfront(t)
        ctx = self.context_of(f)
        o = self._options(p["options"])
        progress.step(f"value of {t.trade_id}")
        q, basket = self._quote(sheet, ctx, o, f.fixings_by_date(), settings=f.basket)
        N = float(sheet.get("general.notional"))
        r = q.result
        out = clean(
            {
                "trade_id": t.trade_id,
                "quantity": t.quantity,
                "notional": N,
                "unit_price": r.price,
                "unit_stderr": r.stderr,
                "value": t.quantity * N * r.price / 100,
                "stderr": abs(t.quantity) * N * r.stderr / 100,
                "valuation": ctx.valuation,
                "reserve": remaining(t, ctx.valuation),
                "snapshot_id": ctx.snapshot_id,
                "result": result_json(r),
                "quote": quote_json(q),
                "basket": basket,
            }
        )
        method = {"name": q.decision.method, "model": q.decision.model, "n_paths": q.decision.n_paths}
        return envelope(self._market(ctx), method, out)

    def create_trade(self, body: dict) -> dict:
        sheet = _sheet(body.get("sheet") or {}, strict=True)
        rate = body.get("coupon_rate")
        if rate is not None and sheet.get("coupon.type") not in (None, "none"):
            sheet = sheet.replace(**{"coupon.rate": float(rate), "wrapper.solve": "upfront"})
        elif sheet.get("wrapper.solve") == "coupon":
            sheet = sheet.replace(**{"wrapper.solve": "upfront"})
        trade_date = dt.date.fromisoformat(body.get("trade_date") or self.ctx.valuation.isoformat())
        trade = Trade.from_term_sheet(
            body.get("trade_id") or self.repo.new_trade_id(sheet.mask.name[:3].upper()),
            sheet,
            float(body.get("quantity", 1.0)),
            trade_date,
            body.get("strike_date"),
            book=body.get("book", "default"),
            counterparty=body.get("counterparty", ""),
            tags=tuple(body.get("tags", ())),
        )
        return trade_json(self.repo.save_trade(dataclasses.replace(trade, reserves=self._reserves_of_quote(trade))))

    def _reserves_of_quote(self, trade: Trade) -> dict | None:
        """The lines of the quote a trade came from, frozen: its tag names the quote, and a quote without costs leaves no reserve."""
        quote_id = next((tag[6:] for tag in trade.tags if tag.startswith("quote:")), None)
        if quote_id is None:
            return None
        try:
            desk = self.repo.quote(quote_id)["result"].get("desk")
        except KeyError:
            return None
        if not desk:
            return None
        sheet = trade.term_sheet()
        assert sheet is not None  # noqa: S101 (narrows the type for pyright: a quote is a term sheet's)
        return frozen(desk, trade.strike_date, compile_sheet(sheet, trade.strike_date).maturity, quote_id)

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

    def _positions_by_market(
        self, ctx: PricingContext, trades: list | None = None, stored: dict | None = None
    ) -> tuple[dict, dict]:
        """Live trades as ({(single name, payment currency): positions}, {payment currency: (worst-of positions, their names)}):
        trades paying in different currencies cannot share a market. `trades` and `stored` (fixings): a job's frozen
        ones, else the store's."""
        singles, baskets = {}, {}
        for t in self.repo.trades() if trades is None else trades:
            if t.status != "live":
                continue
            sheet = t.term_sheet()
            observed = (
                sheet.underlyings if sheet is not None else tuple(validate(parse(t.product["script"])).underlying_names)
            )
            fix = (
                self._known_fixings(observed, ctx.valuation, stored)
                if t.strike_date < ctx.valuation and sheet is not None
                else None
            )
            pay = sheet.payment_currency if sheet is not None else None
            first = observed[0]  # read before the length test, which pyright narrows to an empty tuple
            if len(observed) > 1:
                positions, names = baskets.setdefault(pay, ([], []))
                positions.append(t.to_position(fix))
                names += [n for n in observed if n not in names]
            else:
                singles.setdefault((first, pay), []).append(t.to_position(fix))
        return singles, baskets

    def price_book(self, body: dict) -> dict:
        """Live trades in groups, each on its own shared simulation: one group per single name, on that name's market and
        under the policy (SLV for path-dependent products), and the worst-ofs under the basket context. The groups use
        different seeds, so their errors add in quadrature."""
        return self._submit("book", {"options": body.get("options") or {}}, self.freeze_book())

    def freeze_book(self) -> Frozen:
        """The book as a job needs it: every trade by version, the fixings of their names, the market in use."""
        trades = self.repo.trades()
        if not any(t.status == "live" for t in trades):
            raise ApiError("no live trade in the book", 422)
        stored = self.repo.fixings()
        return self._freeze(fixings={n: stored[n] for n in self._names_of(trades) if n in stored}, trades=trades)

    def run_book(self, p: dict, f: Frozen) -> dict:
        o = self._options(p["options"])
        greeks = bool(p["options"].get("greeks", True))
        ctx = self.context_of(f)
        trades = self._trades_of(f)
        singles, baskets = self._positions_by_market(ctx, trades, f.fixings_by_date() or {})
        if not singles and not baskets:
            raise ApiError("no live trade in the book", 422)
        reserves = {t.trade_id: remaining(t, ctx.valuation) for t in trades if t.status == "live"}
        t0 = time.perf_counter()
        groups = []
        key = lambda x: (x[0][0] != ctx.underlying, x[0][0], str(x[0][1]))  # noqa: E731
        for k, ((name, pay), positions) in enumerate(sorted(singles.items(), key=key)):
            gctx, shown = self._single_ctx(name, ctx, pay)
            if gctx.underlying != name:
                raise ValueError(
                    f"{', '.join(p.name for p in positions)}: no market of {name} on {ctx.valuation}, from the lake or in use"
                )
            groups.append((name, Book(positions), gctx, shown, DEFAULT_SEED + k, name))
        for pay, (positions, names) in sorted(baskets.items(), key=lambda x: str(x[0])):
            bctx, shown = self._basket_ctx(names, f.basket, ctx=ctx, payment=pay)
            label = "worst-of" if len(baskets) == 1 else f"worst-of ({pay})"
            groups.append((label, Book(positions), bctx, shown, DEFAULT_SEED + len(groups), None))
        parts = []
        steps = len(groups) * (2 if greeks else 1)
        for k, (label, book, gctx, shown, seed, name) in enumerate(groups):
            forced = None if name is None else o["model"]
            progress.step(f"price {label}", k * (steps // len(groups)), steps)
            res = book.price(
                gctx, model=forced, n_paths=o["n_paths"], seed=seed, batch=o["batch"], workers=self.workers
            )
            g = None
            if greeks:
                progress.step(f"Greeks of {label}", 2 * k + 1, steps)
                g = book.greeks(
                    gctx,
                    model=forced,
                    n_paths=o["n_paths"],
                    seed=seed,
                    batch=o["batch"],
                    slv_vega=o["slv_vega"],
                    workers=self.workers,
                )
            parts.append((label, res, g, shown, name))
        out = _combine_books(parts)
        for pos in out["positions"]:
            pos["reserve"] = clean(reserves.get(pos["name"]))
        out["reserve"] = float(sum(r["amount"] for r in reserves.values() if r))
        out["reserve_initial"] = float(sum(r["initial"] for r in reserves.values() if r))
        out["value_net"] = out["value"] - out["reserve"]
        out["no_reserve"] = sorted(k for k, r in reserves.items() if r is None)
        out["seconds"] = time.perf_counter() - t0  # prices and Greeks; each group's own time is its price
        method = {"name": out["method"], "model": out["model"], "n_paths": out["n_paths"], "groups": len(groups)}
        return envelope(self._market(ctx), method, out)

    # --- fixings and lifecycle ---
    def fixings(self, underlying: str | None = None) -> dict:
        return clean({"fixings": self.repo.fixings(underlying)})

    def put_fixings(self, body: dict) -> dict:
        und = str(body.get("underlying", "SPX")).upper()
        levels, skipped = dict(body.get("levels") or {}), []
        if body.get("text"):  # pasted lines or a CSV file, parsed here so that the page and the tests share it
            parsed, skipped = parse_fixings(body["text"])
            levels.update(parsed)
        if not levels:
            raise ApiError("no line of the form 2026-07-27 104.2", 422)
        scale = float(body.get("real_spot") or 0)
        ctx = self.ctx
        if scale:  # real index levels: divide by real spot / model spot (every market is rescaled to 100)
            ratio = scale / ctx.market.spot
            levels = {d: float(v) / ratio for d, v in levels.items()}
        for d in levels:
            if dt.date.fromisoformat(str(d)) > ctx.valuation:
                raise ApiError(f"fixing on {d} is after the valuation date {ctx.valuation}", 422)
        n = self.repo.put_fixings(und, levels, body.get("source", "manual"))
        return {"ok": True, "stored": n, "skipped": skipped}

    def delete_fixing(self, underlying: str, date: str) -> dict:
        self.repo.delete_fixing(underlying, date)
        return {"ok": True}

    def trade_events(self, trade_id: str) -> dict:
        ctx = self.ctx
        trade = self.repo.trade(trade_id)
        sheet = trade.term_sheet()
        assert sheet is not None  # noqa: S101 (narrows the type for pyright: the trades screen lists term sheets' events)
        compiled = compile_sheet(sheet, ctx.valuation)
        upcoming = [
            {"observation": ev.when.value, "payment": ev.pay.value if ev.pay else None}
            for ev in compiled.program.events[1:]
            if dt.date.fromisoformat(ev.when.value) > ctx.valuation
        ]
        past, missing = [], None
        if trade.strike_date < ctx.valuation:
            try:
                past = events_json(
                    events(trade, self._known_fixings(sheet.underlyings, ctx.valuation) or {}, ctx.valuation)
                )
            except KeyError as exc:  # names the dates, or only the underlying when it has no fixing at all
                missing = str(exc).strip("'\"")
                if missing in sheet.underlyings:
                    missing = f"no fixing of {missing}"
        return clean(
            {
                "trade_id": trade_id,
                "past": past,
                "upcoming": upcoming,
                "missing": missing,
                "maturity": compiled.maturity,
                "strike_date": compiled.strike_date,
                "underlyings": sheet.underlyings,
            }
        )

    def upcoming(self, days: int = 45) -> dict:
        ctx = self.ctx
        horizon = ctx.valuation + dt.timedelta(days=int(days))
        rows = []
        for t in self.repo.trades():
            if t.status != "live":
                continue
            sheet = t.term_sheet()
            if sheet is None:
                continue
            compiled = compile_sheet(sheet, ctx.valuation)
            for ev in compiled.program.events[1:]:
                d = dt.date.fromisoformat(ev.when.value)
                if ctx.valuation <= d <= horizon:
                    rows.append(
                        {
                            "trade_id": t.trade_id,
                            "observation": ev.when.value,
                            "payment": ev.pay.value if ev.pay else None,
                            "final": ev is compiled.program.events[-1],
                        }
                    )
        return {"events": sorted(rows, key=lambda r: r["observation"]), "until": horizon.isoformat()}

    # --- script lab ---
    def script_check(self, body: dict) -> dict:
        try:
            program = validate(parse(body.get("script", "")))
        except ScriptError as exc:
            return {"ok": False, "message": exc.message, "line": exc.line, "col": exc.col}
        return {
            "ok": True,
            "underlyings": program.underlying_names,
            "params": program.param_values,
            "events": len(program.events),
        }

    def script_price(self, body: dict) -> dict:
        check = self.script_check(body)
        if not check["ok"]:
            raise ApiError(f"line {check['line']}, col {check['col']}: {check['message']}", 422)
        if len(check["underlyings"]) > 1:
            raise ApiError("the script lab prices single-name scripts; worst-ofs go through the term sheet", 422)
        params = {
            "script": body["script"],
            "model": (body.get("model") or "lv").lower(),
            "n_paths": int(body.get("n_paths") or 40_000),
            "seed": int(body.get("seed", 2026)),
        }
        return self._submit("script", params, self._freeze())

    def run_script(self, p: dict, f: Frozen) -> dict:
        ctx = self.context_of(f)
        model, n_paths = p["model"], p["n_paths"]
        progress.step(f"script under {model}")
        res = price(
            p["script"],
            ctx.model(model, 2.0),
            ctx.market,
            n_paths=n_paths,
            seed=p["seed"],
            batch=min(n_paths, 25_000),
            valuation=ctx.valuation,
            workers=self.workers,
        )
        out = clean(
            {
                "price": res.price,
                "stderr": res.stderr,
                "seconds": res.seconds,
                "model": model,
                "n_paths": n_paths,
                "expected_life": res.expected_life,
                "warnings": res.warnings,
                "notional": res.notional,
            }
        )
        method = {"name": "monte_carlo", "model": model, "n_paths": n_paths, "seed": p["seed"]}
        return envelope(self._market(ctx), method, out)


for _kind, _method in (
    ("market", "run_market"),
    ("fit", "run_fit"),
    ("reproduce", "run_reproduce"),
    ("replay", "run_replay"),
    ("quote", "run_quote"),
    ("value", "run_value"),
    ("book", "run_book"),
    ("script", "run_script"),
):
    register(_kind, lambda desk, p, f, _m=_method: getattr(desk, _m)(p, f))


def _combine_books(parts) -> dict:
    """One book result from groups priced on independent simulations: values add, errors add in quadrature;
    deltas and vegas are reported per underlying (a worst-of moves with every name). A part is (label, result, greeks,
    what the markets were, its one name or None for a basket)."""
    positions, groups, warnings = [], [], []
    by_name: dict = {}
    pos_delta = []
    for label, res, g, shown, name in parts:
        b = book_json(res)
        for p in b["positions"]:
            p["group"] = label
        positions += b["positions"]
        warnings += b["warnings"]
        groups.append(
            {
                "label": label,
                "value": b["value"],
                "stderr": b["stderr"],
                "method": b["method"],
                "model": b["model"],
                "reason": b["reason"],
                "n_paths": b["n_paths"],
                "seconds": b["seconds"],
                "positions": len(b["positions"]),
                "basket": clean(shown),
                "note": g.get("note") if g else None,
            }
        )
        if g is None:
            pos_delta += [None] * len(b["positions"])
            continue
        per_name = (
            {name: {"delta": g["delta"], "vega": g.get("vega")}}
            if name is not None
            else {n: {"delta": g["delta"][n], "vega": (g.get("vega") or {}).get(n)} for n in g["delta"]}
        )
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
    names = {
        n: {
            "delta": r["delta"],
            "delta_se": float(np.sqrt(r["delta_var"])),
            "vega": None if r["no_vega"] else r["vega"],
            "vega_se": None if r["no_vega"] else float(np.sqrt(r["vega_var"])),
            "vega_missing": r["no_vega"],
        }
        for n, r in by_name.items()
    }
    value = float(sum(g["value"] for g in groups))
    stderr = float(np.sqrt(sum(g["stderr"] ** 2 for g in groups)))
    one = groups[0] if len(groups) == 1 else None
    note = next((g["note"] for g in groups if g["note"]), None)
    return clean(
        {
            "value": value,
            "stderr": stderr,
            "positions": positions,
            "groups": groups,
            "warnings": warnings,
            "method": one["method"] if one else "monte_carlo",
            "model": one["model"] if one else " + ".join(g["model"] for g in groups),
            "n_paths": max(g["n_paths"] for g in groups),
            "seconds": sum(g["seconds"] for g in groups),
            "snapshot_id": getattr(parts[0][1], "snapshot_id", None),
            "independent_stderr": float(np.sqrt(sum(p["stderr"] ** 2 for p in positions))),
            "greeks": {"by_name": names, "positions": pos_delta, "note": note, "units": dict(parts[0][2]["units"])}
            if parts[0][2] is not None
            else None,
            "skipped": [],
        }
    )
