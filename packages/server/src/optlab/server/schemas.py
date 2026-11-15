"""Request bodies of API v1 (ADR-0005): typed, unknown keys refused, and bounds on everything that costs, so that no
parameter makes one request take the machine. Levels are fractions; dates are ISO 8601. The routers pass these to
the desk's services as plain dicts (`body(model)`), the same keys v0's page sent."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_PATHS = 2_000_000  # the "max" precision, for the compiled engine
MAX_SCRIPT = 20_000  # characters of a payoff script
MAX_SCENARIOS = 50
Precision = Literal["quick", "standard", "precise", "max"]
Model = Literal["bs", "lv", "heston", "slv"]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def body(model: BaseModel | None) -> dict[str, Any]:
    """The dict the desk's services take: the keys given, not the defaults the model filled."""
    return {} if model is None else model.model_dump(mode="json", exclude_unset=True, by_alias=True)


# --- structuring ---------------------------------------------------------------------------------------------------
class TermSheetIn(Body):
    type: Literal["autocall", "bullet"] = "autocall"
    values: dict[str, dict[str, Any]] = Field(default_factory=dict, description="section -> field -> value")


class PricingOptions(Body):
    precision: Precision = "standard"
    n_paths: int | None = Field(None, ge=1_000, le=MAX_PATHS)
    control: bool = True
    greeks: bool = False
    fast: bool = False
    slv_vega: bool = False
    control_forwards: bool = False
    model: Model | None = None
    sticky: Literal["moneyness", "strike", "model"] = "moneyness"
    costs: bool = True
    mix_band: bool = False


class QuoteIn(Body):
    sheet: TermSheetIn
    options: PricingOptions = Field(default_factory=lambda: PricingOptions.model_validate({}))


class ScriptIn(Body):
    script: str = Field(..., min_length=1, max_length=MAX_SCRIPT)


class ScriptPriceIn(ScriptIn):
    model: Model = "lv"
    n_paths: int = Field(50_000, ge=1_000, le=500_000)
    seed: int = Field(2026, ge=0, le=2**63 - 1)


# --- trading -------------------------------------------------------------------------------------------------------
class TradeIn(Body):
    sheet: TermSheetIn
    trade_id: str | None = Field(None, pattern=r"^[\w-]{1,64}$")
    quantity: float = Field(-1.0, ge=-1e9, le=1e9, description="signed: > 0 when the book holds the investor side")
    coupon_rate: float | None = Field(None, ge=-1.0, le=10.0)
    trade_date: dt.date | None = None
    strike_date: dt.date | None = None
    book: str = Field("default", max_length=64)
    counterparty: str = Field("", max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=20)


class TradeUpdate(Body):
    """What an edit may change; every edit is a new version of the trade."""

    quantity: float | None = Field(None, ge=-1e9, le=1e9)
    book: str | None = Field(None, max_length=64)
    counterparty: str | None = Field(None, max_length=128)
    status: Literal["live", "called", "matured", "cancelled"] | None = None


class ValuationIn(Body):
    options: PricingOptions = Field(default_factory=lambda: PricingOptions.model_validate({}))


class FixingsIn(Body):
    underlying: str = Field(..., pattern=r"^[A-Z0-9._-]{1,16}$")
    text: str | None = Field(None, max_length=500_000, description="pasted or CSV dates and levels")
    levels: dict[dt.date, float] | None = Field(None, max_length=20_000)
    real_spot: float | None = Field(None, gt=0, description="the real spot of the market's model level 100")
    source: str = Field("manual", max_length=32)


class FixingsFromLakeIn(Body):
    underlying: str = Field("SPX", pattern=r"^[A-Z0-9._-]{1,16}$")
    start: dt.date | None = None


class CostDefaultsIn(Body):
    groups: dict[str, Any] = Field(..., description="group of underlyings -> size bands -> lines (structured/defaults)")


# --- market ----------------------------------------------------------------------------------------------------------
class MarketInUse(Body):
    snapshot_id: str = Field(..., pattern=r"^[\w.+-]{1,128}$")


class MarketFromLakeIn(Body):
    underlying: str = Field("SPX", pattern=r"^[A-Z0-9._-]{1,16}$")
    date: dt.date
    leverage: bool = False
    t_max: float | None = Field(None, gt=0, le=10)


class OtherUnderlyingsIn(Body):
    names: dict[str, dict[str, float | None]] = Field(
        default_factory=dict, max_length=8, description="name -> {vol, q, r}, flat markets rescaled to 100"
    )
    corr: dict[str, float] = Field(default_factory=dict, max_length=64, description='"A|B" -> correlation')


# --- risk ------------------------------------------------------------------------------------------------------------
class RiskOptions(Body):
    precision: Precision = "standard"
    n_paths: int | None = Field(None, ge=1_000, le=MAX_PATHS)
    model: Literal["slv"] | None = None
    smoothing: float = Field(0.005, ge=0.0, le=0.1)
    kxt: Literal["all", "single", "none"] = "single"
    curves: bool = True
    sticky_strike: bool = True
    realistic: bool = True
    cross: bool = True
    theta: bool = True


class Bumps(Body):
    spot: float = Field(0.01, gt=0, le=0.5)
    spot_large: float = Field(0.05, gt=0, le=0.5)
    vol: float = Field(0.01, gt=0, le=0.2)
    vol_relative: float = Field(0.01, gt=0, le=0.5)
    rate_bp: float = Field(10.0, gt=0, le=500)
    corr: float = Field(0.01, gt=0, le=0.5)
    skew: float = Field(0.01, gt=0, le=0.5)
    roll_days: int = Field(1, ge=1, le=30)


class GreeksIn(Body):
    options: RiskOptions = Field(default_factory=lambda: RiskOptions.model_validate({}))
    bumps: Bumps | None = None


class LadderSpec(Body):
    points: list[float] | None = Field(None, min_length=1, max_length=41)
    rules: list[Literal["sticky_moneyness", "sticky_strike", "realistic"]] | None = Field(None, max_length=3)
    h_spot: float = Field(0.01, gt=0, le=0.5)
    h_vol: float = Field(0.01, gt=0, le=0.2)
    rate_bp: float = Field(10.0, gt=0, le=500)
    tenors: bool = True
    greeks: bool = True


class LadderIn(Body):
    options: RiskOptions = Field(default_factory=lambda: RiskOptions.model_validate({}))
    ladder: LadderSpec | None = None


class ScenariosIn(Body):
    scenarios: list[dict[str, Any]] = Field(..., min_length=1, max_length=MAX_SCENARIOS)
    note: str = Field("", max_length=2_000)


class ScenarioRunIn(Body):
    options: RiskOptions = Field(default_factory=lambda: RiskOptions.model_validate({}))
    names: list[str] | None = Field(None, max_length=MAX_SCENARIOS)
    grid: bool = True


class VarIn(Body):
    options: RiskOptions = Field(default_factory=lambda: RiskOptions.model_validate({}))
    days: int = Field(500, ge=20, le=2_500)
    stressed: bool = True
    ten_day: bool = True
    workers: int | None = Field(None, ge=1, le=12)


class EodIn(Body):
    from_: dt.date = Field(..., alias="from")
    to: dt.date
    options: RiskOptions = Field(default_factory=lambda: RiskOptions.model_validate({}))
    greeks: bool = True

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- quant -------------------------------------------------------------------------------------------------------------
class FitIn(Body):
    underlying: str = Field("SPX", pattern=r"^[A-Z0-9._-]{1,16}$")
    day: dt.date
    source: str | None = Field(None, max_length=32)
    models: list[Literal["ssvi", "svi", "heston"]] | None = Field(None, max_length=3)


class ReplayIn(Body):
    preset: str = Field("barrier_reverse_convertible", max_length=64)
    underlying: str = Field("SPX", pattern=r"^[A-Z0-9._-]{1,16}$")
    strike: dt.date
    end: dt.date | None = None
    changes: dict[str, Any] = Field(default_factory=dict, max_length=50)
    model: Literal["lv", "slv"] = "lv"
    n_paths: int = Field(20_000, ge=1_000, le=200_000)
    every: int = Field(5, ge=1, le=60)
    leverage_every: int = Field(1, ge=1, le=60)
    greeks: bool = True
    costs: bool = True
    quantity: float = Field(1.0, ge=-1e9, le=1e9)


# --- auth and admin --------------------------------------------------------------------------------------------------
class LoginIn(Body):
    name: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=1024)


class UserIn(Body):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9._-]{1,31}$")
    password: str = Field(..., min_length=12, max_length=1024)
    roles: list[Literal["admin", "trader", "structurer", "risk", "quant", "viewer"]] = Field(..., min_length=1)


class PipelineUpdate(Body):
    enabled: bool | None = None
    days: str | None = Field(
        None,
        pattern=r"^(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun)){0,6}$",
        description="comma-separated days, e.g. mon,tue,wed,thu,fri",
    )
    at: str | None = Field(None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$", description="local time, HH:MM")
    params: dict[str, Any] | None = Field(
        None, description="the job's parameters (keys checked against the schedule's kind)"
    )


class UserUpdate(Body):
    roles: list[Literal["admin", "trader", "structurer", "risk", "quant", "viewer"]] | None = Field(None, min_length=1)
    disabled: bool | None = None
    password: str | None = Field(None, min_length=12, max_length=1024)


# --- responses that the server itself shapes (the domain payloads are described in docs/architecture/api.md) ---------
class Out(BaseModel):
    """What the server answers: exactly these fields (a key the service adds is dropped, not leaked)."""


class Ok(Out):
    ok: bool = True


class JobError(Out):
    type: str
    message: str


class MarketRef(Out):
    snapshot_id: str | None = Field(None, description="the stored snapshot the job priced on (null: none, e.g. a fit)")
    origin: dict[str, Any] | str | list[Any] | None = Field(None, description="how much of that market was observed")
    as_of: str | None = Field(None, description="the market's date (ISO 8601)")


class Method(BaseModel):
    """How a result was computed: `name` (monte_carlo, replication, full_revaluation, ...) and its parameters."""

    model_config = ConfigDict(extra="allow")
    name: str


class JobResult(Out):
    """Every job result (api.md §5): the market, the method, and the result, whose Monte Carlo numbers are
    `{value, se}`."""

    market: MarketRef | None = None
    method: Method | None = None
    result: dict[str, Any] | list[Any] | None = None


class Job(Out):
    id: str
    kind: str
    state: Literal["queued", "running", "done", "error", "cancelled"]
    progress: float | None = Field(None, ge=0, le=1, description="fraction done, while running and at the end")
    step: str | None = Field(None, description="what the job is doing")
    eta: float | None = Field(None, description="seconds left, estimated from the progress so far (null early on)")
    elapsed: float = Field(..., description="seconds since the job started (0 while queued)")
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    engine: str | None = Field(None, description="the Monte Carlo engine the worker ran it on")
    version: int = Field(..., description="+1 at every change: the id of its SSE events")
    cancel_requested: bool = False
    result: JobResult | None = Field(None, description="once done")
    error: JobError | None = None


class JobBrief(Out):
    id: str
    kind: str
    state: Literal["queued", "running"]
    progress: float | None = None
    step: str | None = None


class JobList(Out):
    jobs: list[JobBrief]


class CancelOut(Out):
    ok: bool = True
    state: Literal["queued", "running", "done", "error", "cancelled"] = Field(
        ..., description="cancelled at once when it was queued; running until it stops at its next batch"
    )


class PipelineJob(Out):
    id: str
    state: str
    finished_at: str | None = None
    error: JobError | None = None


class Pipeline(Out):
    name: str
    kind: str
    params: dict[str, Any]
    days: str
    at: str
    timezone: str
    enabled: bool
    last_run_at: str | None = None
    last_job_id: str | None = None
    next_run_at: str | None = Field(None, description="null when off")
    updated_at: str
    updated_by: str | None = None
    last_job: PipelineJob | None = None


class PipelineList(Out):
    pipelines: list[Pipeline]


class UserOut(Out):
    user_id: str
    name: str
    roles: list[str]
    disabled: bool
    locked_until: str | None = None
    created_at: str
    password_changed_at: str | None = None


class UserList(Out):
    users: list[UserOut]


class AuditEntry(Out):
    audit_id: int
    at: str
    user_id: str | None = Field(None, description="null for the system (a command, a migration, a schedule)")
    action: str
    target: str | None = None
    before_hash: str | None = Field(None, description="sha256 of the state before the write, when there was one")
    after_hash: str | None = None
    detail: dict[str, Any] | None = None


class AuditPage(Out):
    items: list[AuditEntry]
    next: int | None = Field(None, description="the `before` of the next page, or null on the last one")
