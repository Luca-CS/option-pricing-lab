"""Request bodies of API v1 (ADR-0005): typed, unknown keys refused, and bounds on everything that costs, so that no
parameter makes one request take the machine. Levels are fractions; dates are ISO 8601. The routers pass these to
the desk's services as plain dicts (`body(model)`), the same keys v0's page sent.

`body` passes the keys the client gave, and the desk applies its own default to a missing one: a default written here
is documentation of what the desk does (`test_api_defaults.py` checks each one whose desk default is not the
obvious one)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PATHS = 2_000_000  # the "max" precision, for the compiled engine
MAX_SCRIPT = 20_000  # characters of a payoff script
MAX_SCENARIOS = 50
Precision = Literal["quick", "standard", "precise", "max"]
Model = Literal["bs", "lv", "heston", "slv"]


class Body(BaseModel):
    # JSON has no NaN nor infinity, but Python's parser reads them: a NaN passes every bound (each comparison is false)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def body(model: BaseModel | None) -> dict[str, Any]:
    """The dict the desk's services take: the keys given, not the defaults the model filled."""
    return {} if model is None else model.model_dump(mode="json", exclude_unset=True, by_alias=True)


# --- structuring ---------------------------------------------------------------------------------------------------
class TermSheetIn(Body):
    type: Literal["autocall", "bullet", "variance", "accumulator", "callable"] = (
        "autocall"  # the masks of structured.schema (test_api_structuring)
    )
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
    n_paths: int = Field(40_000, ge=1_000, le=500_000)
    seed: int = Field(2026, ge=0, le=2**63 - 1)


# --- trading -------------------------------------------------------------------------------------------------------
class TradeIn(Body):
    sheet: TermSheetIn
    trade_id: str | None = Field(None, pattern=r"^[\w-]{1,64}$")
    quantity: float = Field(1.0, ge=-1e9, le=1e9, description="signed: > 0 when the book holds the investor side")
    coupon_rate: float | None = Field(None, ge=-1.0, le=10.0)
    trade_date: dt.date | None = None
    strike_date: dt.date | None = None
    book: str = Field("default", max_length=64)
    counterparty: str = Field("", max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=20)


class HedgeIn(Body):
    """An instrument booked as a trade (structured/hedges): live, or simulated (a what-if)."""

    instrument: dict[str, Any] = Field(
        ..., max_length=16, description="{type, its fields}: future, cash, irs, deposit, funding_swap, ..."
    )
    quantity: float = Field(
        ..., ge=-1e12, le=1e12, description="signed: > 0 when the desk is long; lots, units or notional"
    )
    status: Literal["live", "simulated"] = "live"
    fill: float | None = Field(
        None, ge=-1e12, le=1e12, description="cash paid per unit at the fill (negative: received); by type when omitted"
    )
    fees: float = Field(0.0, ge=0.0, le=1e12, description="currency, the whole trade")
    trade_id: str | None = Field(None, pattern=r"^[\w-]{1,64}$")
    trade_date: dt.date | None = None
    book: str = Field("default", max_length=64)
    counterparty: str = Field("", max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=20)


class HedgeUniverse(Body):
    """The candidates the optimizer may trade (bounded: at most a few dozen per name)."""

    futures: bool = True
    expiries: int = Field(2, ge=1, le=4, description="quarterly futures expiries")
    vanillas: bool = True
    listed: bool = Field(False, description="vanillas from the day's chain when it quotes them, else OTC")
    tenors: list[float] = Field([0.25, 0.5, 1.0], min_length=1, max_length=6, description="vanillas' expiries, years")
    rates: bool = True
    dividends: bool = True
    funding: bool = True
    max_lots: float = Field(500.0, gt=0, le=1e6, description="listed instruments, lots")
    max_notional: float = Field(1e8, gt=0, le=1e12, description="OTC instruments, currency")

    @model_validator(mode="after")
    def _tenors(self) -> HedgeUniverse:
        if any(not 1 / 52 <= t <= 10 for t in self.tenors):
            raise ValueError("a vanilla's tenor is between a week and ten years")
        return self


class HedgeTargetIn(Body):
    key: str = Field(..., pattern=r"^[a-z_]+\|[\w*~.-]{1,32}\|[\w.@,-]{0,32}$", description='a Greek key, "delta|SPX|"')
    value: float = Field(0.0, ge=-1e12, le=1e12)
    tolerance: float = Field(..., gt=0, le=1e12)


class HedgeOptimizeIn(Body):
    options: RiskOptions = Field(default_factory=lambda: RiskOptions.model_validate({}))
    targets: list[HedgeTargetIn] | None = Field(None, max_length=64, description="none: every key of `families` to 0")
    families: list[Literal["delta", "gamma", "vega_tenor", "rho_tenor", "dividend_year", "funding_tenor"]] = Field(
        default_factory=lambda: ["delta", "vega_tenor", "rho_tenor", "dividend_year", "funding_tenor"], max_length=6
    )
    relative: float = Field(0.1, ge=0.0, le=1.0, description="default tolerance: this share of each Greek's size")
    floor: float = Field(1.0, ge=0.0, le=1e9, description="default tolerance at least, currency")
    universe: HedgeUniverse = Field(default_factory=lambda: HedgeUniverse.model_validate({}))
    grid: bool = True
    var_days: int = Field(250, ge=0, le=500, description="the VaR control's 1-day moves (0: none)")


class ProposalIn(Body):
    job_id: str = Field(..., pattern=r"^[\w-]{1,64}$")
    status: Literal["simulated", "live"] = "simulated"
    indexes: list[int] | None = Field(None, max_length=200, description="the proposal's rows to book (none: all)")


class MarkIn(Body):
    instrument: dict[str, Any] = Field(..., max_length=16)
    quantity: float = Field(1.0, ge=-1e12, le=1e12)


class TradeUpdate(Body):
    """What an edit may change; every edit is a new version of the trade."""

    quantity: float | None = Field(None, ge=-1e9, le=1e9)
    book: str | None = Field(None, max_length=64)
    counterparty: str | None = Field(None, max_length=128)
    status: Literal["live", "called", "matured", "cancelled", "closed"] | None = None


class ValuationIn(Body):
    options: PricingOptions = Field(default_factory=lambda: PricingOptions.model_validate({}))


class BookPricingOptions(PricingOptions):
    greeks: bool = Field(True, description="delta and vega by name, on by default for the book")
    include_simulated: bool = Field(False, description="the simulated what-ifs too")


class BookValuationIn(Body):
    options: BookPricingOptions = Field(default_factory=lambda: BookPricingOptions.model_validate({}))


class FixingsIn(Body):
    underlying: str = Field(..., pattern=r"^[A-Z0-9._-]{1,16}$")
    text: str | None = Field(None, max_length=500_000, description="pasted or CSV dates and levels")
    levels: dict[dt.date, float] | None = Field(None, max_length=20_000)
    real_spot: float | None = Field(None, gt=0, description="the real spot of the market's model level 100")
    source: str = Field("manual", max_length=32)


class FixingsFromLakeIn(Body):
    underlying: str | None = Field(
        None, pattern=r"^[A-Z0-9._-]{1,16}$", description="default: the underlying of the market in use"
    )
    start: dt.date | None = None


class CostDefaultsIn(Body):
    groups: dict[str, Any] = Field(..., description="group of underlyings -> size bands -> lines (structured/defaults)")
    hedges: dict[str, Any] | None = Field(
        None, max_length=8, description="the hedging rows (structured/hedges/costs); omitted: the last version's"
    )


# --- market ----------------------------------------------------------------------------------------------------------
class MarketInUse(Body):
    snapshot_id: str = Field(..., pattern=r"^[\w.+-]{1,128}$")


class MarketFromLakeIn(Body):
    underlying: str = Field("SPX", pattern=r"^[A-Z0-9._-]{1,16}$")
    date: dt.date
    leverage: bool = Field(True, description="calibrate the SLV leverage (seconds); false: a flat leverage")
    t_max: float = Field(3.0, gt=0, le=10, description="the longest maturity of the market, in years")


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
    smoothing: float = Field(0.005, ge=0.0, le=0.1, description="Greeks and ladder; the other runs do not smooth")
    kxt: Literal["all", "single", "none"] = Field("single", description="Greeks: node vegas for which groups")
    curves: bool = Field(True, description="Greeks: rho, epsilon and repo per tenor")
    sticky_strike: bool = Field(True, description="Greeks: the sticky-strike variants")
    realistic: bool = Field(True, description="Greeks: the variants on the lake's spot-vol betas")
    cross: bool = Field(True, description="Greeks: vanna, volga, cross gamma, correlation")
    theta: bool = Field(True, description="Greeks: the roll of one day")
    include_simulated: bool = Field(
        False, description="Greeks, ladder, scenarios and VaR: the simulated what-ifs too (never the EOD)"
    )


class Bumps(Body):
    spot: float = Field(0.01, gt=0, le=0.5)
    spot_large: float = Field(0.05, gt=0, le=0.5)
    vol: float = Field(0.01, gt=0, le=0.2)
    vol_relative: float = Field(0.01, gt=0, le=0.5)
    rate_bp: float = Field(10.0, gt=0, le=500)
    corr: float = Field(0.01, gt=0, le=0.5)
    skew: float = Field(0.01, gt=0, le=0.5)
    roll_days: int = Field(1, ge=1, le=30)
    vol_shift: float = Field(
        0.0, ge=0, le=2.0, description="s of the shifted vega, in vol: 0 moves the wings in proportion"
    )


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

    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)


class PnlDetailIn(Body):
    """A day of the explained P&L opened: from the close of `from` to that of `to`."""

    from_: dt.date = Field(..., alias="from")
    to: dt.date
    rule: Literal["sticky_moneyness", "sticky_strike", "realistic"] = Field(
        "sticky_moneyness", description="the spot-vol rule of the spot step the node cascade starts from"
    )
    detail: Literal["node", "tenor"] = Field("node", description="the vol move node by node, or tenor by tenor")
    options: RiskOptions = Field(default_factory=lambda: RiskOptions.model_validate({}))

    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def _order(self) -> PnlDetailIn:
        if self.to <= self.from_:
            raise ValueError("`to` comes after `from`")
        return self


# --- quant -------------------------------------------------------------------------------------------------------------
class FitIn(Body):
    underlying: str = Field("SPX", pattern=r"^[A-Z0-9._-]{1,16}$")
    day: dt.date
    source: str | None = Field(None, max_length=32)
    models: list[Literal["ssvi", "svi", "heston"]] | None = Field(None, max_length=3)
    maturities: list[Annotated[float, Field(gt=0.01, le=10.0)]] | None = Field(
        None,
        min_length=1,
        max_length=12,
        description="target maturities in years: the closest expiry to each is fitted",
    )
    weights: Literal["spread", "equal"] | None = Field(
        None, description="spread: each quote by the inverse of its bid-ask in vol (the default); equal: alike"
    )
    moneyness: tuple[Annotated[float, Field(ge=0.2, le=3.0)], Annotated[float, Field(ge=0.2, le=3.0)]] | None = Field(
        None, description="(lo, hi) of K/F: only the quotes inside are fitted"
    )
    max_rel_spread: float | None = Field(
        None, gt=0.01, le=2.0, description="quotes wider than this, relative to mid, are dropped"
    )
    n_strikes_heston: int | None = Field(None, ge=6, le=80, description="strikes per maturity in Heston's fit")

    @model_validator(mode="after")
    def _window(self) -> FitIn:
        if self.moneyness is not None and self.moneyness[0] >= self.moneyness[1]:
            raise ValueError("moneyness: the low bound must be below the high one")
        return self


class ReplayHedgeIn(Body):
    """The desk's hedges along a replay (marketdata/hedged.py)."""

    instrument: Literal["future", "cash"] = "future"
    delta_every: int = Field(1, ge=1, le=60, description="business days between delta rebalances")
    threshold: float = Field(0.0, ge=0.0, le=1.0, description="a residual under this share of the trade's delta waits")
    vega: bool = False
    vega_every: int = Field(5, ge=1, le=60)
    vega_tenor: float = Field(0.25, ge=1 / 52, le=2.0, description="the at-the-money call's tenor, years")


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
    hedge: ReplayHedgeIn | None = Field(None, description="the same life with the desk's hedges, against naked")


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
