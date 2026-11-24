"""What the domain routes answer, described for the OpenAPI document (session 2; api.md §10).

The services answer JSON-ready dicts in the library's shapes. These models *describe* those answers so the front's
types are generated, never written by hand: a route names its model in `responses` (`documented(Model)`), which only
changes the document, never what the route sends. Every model allows fields it does not name (a service may add one
without breaking the front), and `test_api_payloads.py` checks the real answers against them, so a description that
stops being true fails a test. A model is added when a screen reads the payload, and no field of an answer changes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import models_json_schema


class Doc(BaseModel):
    """A described answer: these fields at least."""

    model_config = ConfigDict(extra="allow")


def documented(
    model: type[BaseModel], description: str = "the answer", status: int = 200
) -> dict[int | str, dict[str, Any]]:
    """The `responses` of a route whose answer the model describes (the document only; the route sends its dict)."""
    return {status: {"model": model, "description": description}}


class Next(Doc):
    next: str | None = Field(None, description="the cursor of the next page, or null on the last one")


# --- market ------------------------------------------------------------------------------------------------------------
class Origin(Doc):
    """How much of a market was observed: each part's origin (observed, derived, reconstructed, synthetic, ...)."""

    surface: str | None = None
    layer: str | None = None
    source: str | None = None
    extrapolated_from: float | None = None
    carry: str | None = None
    discount: str | None = None
    heston: str | None = None
    leverage: str | None = None


class MarketContext(Doc):
    snapshot_id: str | None
    valuation: str = Field(..., description="the valuation date (ISO 8601)")
    underlying: str
    origin: Origin
    spot: float = Field(..., description="the model's spot: 100, real levels cross at the boundary")
    t_max: float = Field(..., description="the last calibrated maturity (years)")
    maturities: list[float]
    atm_vols: dict[str, float] = Field(..., description="ATM implied vol (fraction) by maturity in years")
    zero_rates: dict[str, float] = Field(..., description="zero rate (fraction) by maturity in years")
    carry: dict[str, float] = Field(..., description="carry (dividend and repo, fraction) by maturity in years")
    slv_mix: float | None = None
    heston: dict[str, float] | None = None


class Snapshot(Doc):
    snapshot_id: str
    underlying: str
    valuation: str
    asof: str | None = None
    source: str | None = None
    active: bool = False
    file: str | None = None


class SnapshotPage(Next):
    items: list[Snapshot]


class CoverageRow(Doc):
    dataset: str
    origin: str | None = None
    source: str | None = None
    n: int
    first: str | None = None
    last: str | None = None
    ingested: str | None = None


class LakeCoverage(Doc):
    available: bool
    coverage: list[CoverageRow] = []


class Instrument(Doc):
    id: str
    name: str
    asset_class: str
    currency: str
    place: str | None = None
    vol_index: str | None = None
    enabled: bool = True
    last: str | None = Field(None, description="the last close in the lake")
    n: int | None = Field(None, description="closes in the lake")
    origin: str | None = None


class Universe(Doc):
    available: bool
    instruments: list[Instrument] = []


class LakeQuality(Doc):
    available: bool
    issues: list[dict[str, Any]] = []
    recent_issues: list[dict[str, Any]] = []


# --- structuring -------------------------------------------------------------------------------------------------------
class Preset(Doc):
    name: str
    type: str
    label: str
    sheet: dict[str, Any] = Field(..., description="the term sheet, flat: section -> field -> value")


class PresetList(Doc):
    presets: list[Preset]
    masks: dict[str, Any]


class Headline(Doc):
    """A quote's headline: what it solved (a coupon, a price) as `{kind, value, se, unit, label}`."""

    kind: str
    value: float
    se: float | None = None
    unit: str
    label: str


class SheetGeneral(Doc):
    basket: str | None = None
    underlying: str | None = None
    underlyings: list[str] | None = None
    notional: float | None = None
    maturity_months: int | None = None
    strike_date: str | None = None
    payment_currency: str | None = None


class Sheet(Doc):
    """A term sheet as the library writes it (section -> field -> value; GET /structuring/schema has every field)."""

    type: str
    general: SheetGeneral


class QuoteItem(Doc):
    quote_id: str
    created_at: str
    sheet: Sheet
    headline: Headline | None = None


class QuotePage(Next):
    items: list[QuoteItem]


class Estimate(Doc):
    """A Monte Carlo number: its value and its standard error (null where the library gives none)."""

    value: float
    se: float | None = None


class FormField(Doc):
    name: str
    path: str = Field(..., description="section.field, the key of `bounds` and of a problem's `errors[].field`")
    label: str
    kind: str = Field(..., description="choice, number, int, date, bool, names")
    unit: str = Field("", description="what one unit of the value is: per annum, of initial, bp, vol points, months...")
    choices: list[Any] = []
    value: Any = None
    read_only: bool = False
    optional: bool = False


class FormSection(Doc):
    name: str
    label: str
    fields: list[FormField]


class SheetForm(Doc):
    """The visible form of a sheet: the server decides which fields show (`visible_if`), the front draws them."""

    type: str
    label: str
    sections: list[FormSection]


class Bound(Doc):
    min: float | None = None
    max: float | None = None
    default: Any = None


class TermSheetFormOut(Doc):
    type: str
    sheet: dict[str, Any] = Field(..., description="the validated sheet, flat: section -> field -> value")
    form: SheetForm
    bounds: dict[str, Bound]
    error: str | None = None
    costs: dict[str, Any] = Field({}, description="the cost table's parameters that apply to this sheet")


class Pricing(Doc):
    """A Monte Carlo run of the quote: the price and how it was got."""

    price: Estimate
    expected_life: float | None = None
    method: str
    n_paths: int | None = None
    numerical_error: float | None = None
    seconds: float | None = None
    warnings: list[str] = []


class DeskLine(Doc):
    """One line of the desk price: a provision, a bump, a digital or an interaction, on the base's random numbers."""

    key: str
    kind: str = Field(..., description="model, bump, digital, interaction")
    label: str
    cost: Estimate = Field(..., description="% of nominal")
    coupon_bp: float | None = Field(None, description="what it costs the coupon, bp per annum")
    in_price: bool
    overridden: bool = False
    note: str = ""


class DeskPrice(Doc):
    base_price: Estimate
    final_price: Estimate
    coupon_base: float | None = Field(None, description="the coupon before the desk's lines (fraction per annum)")
    coupon_final: float | None = Field(None, description="the client's coupon (fraction per annum)")
    lines: list[DeskLine]
    side: str
    seconds: float | None = None


class Policy(Doc):
    """Why this method and model: the product's class, the base model and its provisions."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    class_: str = Field(..., alias="class")
    label: str
    reason: str
    description: str | None = None
    method: str
    model: str
    n_paths: int | None = None
    control: str | None = None
    provisions: list[str] = []
    forced: bool = False


class QuoteGreeks(Doc):
    delta: Estimate | None = None
    vega: Estimate | None = None
    units: dict[str, str] = {}
    method: str | None = None
    model: str | None = None
    note: str | None = None


class ScheduleRow(Doc):
    observation: str
    payment: str | None = None
    t: float
    p_end: float = Field(..., description="probability that the product ends at this date")
    p_redeemed: float | None = None
    flows: dict[str, float] = Field({}, description="expected flows at this date, % of nominal, by kind")


class Product(Doc):
    label: str
    type: str
    format: str | None = None
    maturity: str | None = None
    maturity_months: int | None = None
    strike_date: str | None = None
    underlyings: list[str] = []
    script: str | None = Field(None, description="the payoff script the sheet compiles to")


class QuoteResult(Doc):
    """A quote, as the job returns it (`result` of its envelope) and as it is stored."""

    quote_id: str | None = None
    snapshot_id: str | None = None
    headline: Headline
    coupon_rate: Estimate | None = None
    upfront: Estimate | None = None
    target: float | None = None
    margin: float | None = None
    result: Pricing
    control: Pricing | None = None
    control_gap_se: float | None = None
    desk: DeskPrice | None = None
    policy: Policy
    greeks: QuoteGreeks | None = None
    schedule: list[ScheduleRow] = []
    product: Product
    sheet: dict[str, Any]
    options: dict[str, Any] = {}
    seconds: float | None = None
    warnings: list[str] = []


class SavedQuote(Doc):
    quote_id: str
    created_at: str
    sheet: Sheet
    result: QuoteResult


# --- trading -----------------------------------------------------------------------------------------------------------
class TradeSummary(Doc):
    type: str
    underlyings: list[str]
    maturity_months: int | None = None
    coupon: str | None = None
    coupon_rate: float | None = Field(None, description="fraction per annum")
    downside: str | None = None
    barrier: float | None = Field(None, description="fraction of the initial level")
    format: str | None = None


class ReserveLine(Doc):
    key: str
    kind: str
    label: str
    cost: float = Field(..., description="% of nominal, as frozen at issue")
    se: float | None = None


class Reserves(Doc):
    """What the trade froze at issue: the desk price over the base, line by line (% of nominal)."""

    base_price: float
    client_price: float
    total: float
    issue: str
    end: str
    side: str
    method: str
    quote_id: str | None = None
    lines: list[ReserveLine] = []


class Trade(Doc):
    trade_id: str
    version: int
    book: str
    status: str = Field(..., description="live, called, matured, knocked_out, deleted, ...")
    quantity: float
    trade_date: str
    strike_date: str | None = None
    counterparty: str = ""
    tags: list[str] = []
    product: dict[str, Any]
    summary: TradeSummary | None = None
    reserves: Reserves | None = None


class TradePage(Next):
    items: list[Trade]


class UpcomingEvent(Doc):
    trade_id: str
    observation: str
    payment: str | None = None
    final: bool


class Upcoming(Doc):
    events: list[UpcomingEvent]
    until: str


# --- risk --------------------------------------------------------------------------------------------------------------
class RiskRunItem(Doc):
    run_id: str
    kind: str = Field(..., description="risk, ladder, scenarios, var, eod, pnl")
    day: str
    run_key: str
    version: int
    code: str | None = None
    params: dict[str, Any] = {}
    created_at: str
    seconds: float | None = None


class RiskRunPage(Next):
    items: list[RiskRunItem]


class PnlHistory(Doc):
    """The explained P&L cumulated from the EOD runs (`note` says why it is empty)."""

    daily: list[dict[str, Any]] = []
    days: list[Any] = []
    curve: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    eod: list[dict[str, Any]] = []
    note: str | None = None


# --- quant, admin ------------------------------------------------------------------------------------------------------
class Device(Doc):
    name: str
    compute_capability: str | None = None
    sm_count: int | None = None
    clock_mhz: float | None = None
    memory_gb: float | None = None
    free_gb: float | None = None
    integrated: bool | None = None
    watchdog: bool | None = None


class EngineInfo(Doc):
    engine: str = Field(..., description="the engine asked for: auto, gpu, cpu or numpy")
    compiled: bool
    cuda_build: bool
    devices: list[Device] = []
    cpu_threads: int | None = None
    resolved: str = Field(..., description="the engine that runs: gpu, cpu or numpy")


class GpuState(Doc):
    """A card as `nvidia-smi` saw it at the worker's last heartbeat."""

    name: str | None = None
    memory_total_mb: float | None = None
    memory_used_mb: float | None = None
    sm_clock_mhz: float | None = None
    temperature_c: float | None = None
    utilization: float | None = Field(None, description="fraction of the time the card was busy")


class WorkerInfo(Doc):
    engine: EngineInfo | None = None
    gpu: list[GpuState] | None = Field(default=None, description="null where the worker's machine has no nvidia-smi")


class Backup(Doc):
    file: str
    bytes: int
    at: str


class LakeBackup(Doc):
    folder: str
    at: str


class WorkerRow(Doc):
    worker_id: str
    host: str | None = None
    pid: int | None = None
    role: str
    engine: str | None = None
    concurrency: int | None = None
    started_at: str
    heartbeat_at: str | None = None
    stopped_at: str | None = None
    alive: bool
    info: WorkerInfo = Field(default_factory=WorkerInfo)


class SystemInfo(Doc):
    ok: bool
    version: str
    snapshot_id: str | None = None
    valuation: str | None = None
    origin: Origin | None = None
    workers: list[WorkerRow] = []
    jobs: list[dict[str, Any]] = []
    job_counts: dict[str, int] = {}
    worker: str | None = None
    store: str
    started: str
    engine: EngineInfo
    database_revision: str | None = None
    settings: dict[str, Any] = {}
    backups: list[Backup] = []
    lake_backups: list[LakeBackup] = []


class JobRow(Doc):
    """A job of the queue as the admin's list shows it (the result is on GET /jobs/{id})."""

    id: str
    kind: str
    state: str
    user_id: str | None = None
    schedule: str | None = None
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    engine: str | None = None
    progress: float | None = None
    step: str | None = None
    error: dict[str, Any] | None = None


class JobRowPage(Next):
    items: list[JobRow]


# --- job results -------------------------------------------------------------------------------------------------------
# The `result` of a job's envelope by its kind. A job route answers 202 and the result comes later on GET /jobs/{id},
# whose `result.result` is open in the document: these schemas are added to the document's components (and listed
# under `x-job-results`) so the front types a finished job by its kind.
JOB_RESULTS: dict[str, type[BaseModel]] = {
    "quote": QuoteResult,
}


def describe_results(doc: dict[str, Any]) -> None:
    """Add the job results' schemas to the document, and the map kind -> schema under `x-job-results`."""
    _, top = models_json_schema(
        [(m, "serialization") for m in JOB_RESULTS.values()], ref_template="#/components/schemas/{model}"
    )
    schemas = doc.setdefault("components", {}).setdefault("schemas", {})
    for name, schema in top.get("$defs", {}).items():
        schemas.setdefault(name, schema)
    doc["x-job-results"] = {kind: {"$ref": f"#/components/schemas/{m.__name__}"} for kind, m in JOB_RESULTS.items()}
