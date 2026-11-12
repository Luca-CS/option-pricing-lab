"""Quant: the calibration's model risk, the vol lab against real chains, replays of trades on real closes, the engine."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam

from optlab import native
from optlab.server.routers._common import ACCEPTED, desk, submit
from optlab.server.schemas import FitIn, ReplayIn, body
from optlab.server.web import access

router = APIRouter(prefix="/quant", tags=["quant"])
NAME = r"^[A-Z0-9._-]{1,16}$"


@router.get("/model-risk", **access())
def model_risk(request: Request):
    """Prices of the calibration's exotics under Black-Scholes, local vol, Heston and SLV."""
    return desk(request).model_risk()


@router.get("/vollab/days", **access())
def vollab_days(request: Request):
    """Days with option quotes in the lake, by name and source."""
    return desk(request).lab_days()


@router.get("/vollab/quotes", **access("quant"))
def vollab_quotes(
    request: Request,
    day: dt.date,
    underlying: str = Query("SPX", pattern=NAME),
    source: str | None = Query(None, max_length=32),
):
    """Bid and ask of a day's chain (personal-use data: refused when the LAN profile is on)."""
    return desk(request).lab_quotes(underlying, day.isoformat(), source)


@router.post("/vollab/fits", **access("quant"), status_code=202, responses=ACCEPTED)
def vollab_fit(f: FitIn, request: Request):
    """Fit SSVI, SVI and Heston against a day's quotes, stored as a replayable run."""
    return submit(request, "vollab_fit", desk(request).lab_fit(body(f)), target=f.underlying)


@router.get("/vollab/runs", **access())
def vollab_runs(request: Request, underlying: str | None = Query(None, pattern=NAME)):
    return desk(request).lab_runs(underlying)


@router.post("/vollab/runs/{run_id}/reproduce", **access("quant"), status_code=202, responses=ACCEPTED)
def vollab_reproduce(request: Request, run_id: str = PathParam(..., pattern=r"^[\w.-]{1,128}$")):
    """Run a stored fit again and say how far the new numbers are from the stored ones."""
    return submit(request, "vollab_reproduce", desk(request).lab_reproduce(run_id), target=run_id)


@router.get("/replay/runs", **access())
def replay_runs(request: Request):
    """Replays of trades on real closes: the desk's and the versioned ones."""
    return desk(request).replay_runs()


@router.post("/replay/runs", **access("quant"), status_code=202, responses=ACCEPTED)
def replay_run(r: ReplayIn, request: Request):
    """Replay a preset over its life on real closes (valuations, Greeks, events, P&L)."""
    return submit(request, "replay_run", desk(request).replay_run(body(r)), target=f"{r.preset}/{r.underlying}")


@router.get("/replay/runs/{run_id:path}", **access())
def replay_get(
    request: Request, run_id: str = PathParam(..., pattern=r"^[A-Za-z0-9_.-]{1,128}(/[A-Za-z0-9_.-]{1,128})?$")
):
    """A replay (ids are checked against the stored ones, never used as a path)."""
    return desk(request).replay_get(run_id)


@router.get("/engine", **access())
def engine(request: Request):
    """The Monte Carlo engines of this machine: the compiled one, its CUDA build, the devices and the CPU threads."""
    return native.summary()
