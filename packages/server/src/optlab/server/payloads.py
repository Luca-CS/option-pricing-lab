"""What the domain routes answer, described for the OpenAPI document (api.md §10).

The services answer JSON-ready dicts in the library's shapes. These models *describe* those answers so the front's
types are generated, never written by hand: a route names its model in `responses` (`documented(Model)`), which only
changes the document, never what the route sends. Every model allows fields it does not name (a service may add one
without breaking the front), and `test_api_payloads.py` checks the real answers against them, so a description that
stops being true fails a test. A model is added when a screen reads the payload, and no field of an answer changes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import models_json_schema


class Doc(BaseModel):
    """A described answer: these fields at least."""

    model_config = ConfigDict(extra="allow")


def documented(model: Any, description: str = "the answer", status: int = 200) -> dict[int | str, dict[str, Any]]:
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
    upfront: Estimate | float | None = None
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


class TradeVersion(Doc):
    version: int
    recorded_at: str
    deleted: bool = False
    quantity: float
    status: str
    book: str
    counterparty: str = ""


class ReserveNowLine(Doc):
    """A line of a trade's reserve today: what it froze at issue (`initial`) and what is left (`amount`), currency."""

    key: str
    kind: str
    label: str
    cost: Estimate | float = Field(..., description="% of nominal at issue ({value, se} in a valuation)")
    se: float | None = None
    amount: float
    initial: float


class ReserveNow(Doc):
    amount: float = Field(..., description="what is left today, currency")
    initial: float = Field(..., description="what the trade froze at issue, currency")
    fraction: float = Field(..., description="the share of the life gone, which released the reserve so far")
    method: str
    lines: list[ReserveNowLine] = []


class TradeDetail(Doc):
    trade: Trade
    versions: list[TradeVersion]
    quote_id: str | None = None
    reserve: ReserveNow | None = None
    form: SheetForm | None = None
    script: str | None = None


class PastEvent(Doc):
    date: str
    kind: str = Field(..., description="coupon_paid, coupon_missed, called, knocked_in, matured, ...")
    amount: float | None = None
    payment: str | None = None


class TradeEvents(Doc):
    trade_id: str
    past: list[PastEvent] = []
    upcoming: list[dict[str, Any]] = []
    missing: str | None = Field(default=None, description="why the past stops early: the fixing the trade lacks")
    maturity: str | None = None
    strike_date: str | None = None
    underlyings: list[str] = []


class Fixings(Doc):
    fixings: dict[str, dict[str, float]] = Field(..., description="name -> date -> level at the model's scale (100)")


class CostField(Doc):
    name: str
    label: str
    unit: str = ""
    kind: str = "number"
    min: float | None = None
    max: float | None = None


class CostTier(Doc):
    """One size band of a group: up to `max_notional`, each field's value (fractions, as the sheet's costs)."""

    max_notional: float | None = None


class CostGroup(Doc):
    label: str
    currency: str | None = None
    tiers: list[CostTier]


class CostDefaults(Doc):
    """The desk's cost table by group of underlyings and size band, its versions and the starting values."""

    groups: dict[str, CostGroup]
    starting: dict[str, CostGroup] = {}
    version: int | None = None
    recorded_at: str | None = None
    versions: list[dict[str, Any]] = []
    fields: list[CostField] = []


class TradeValuation(Doc):
    """A trade valued at the market in use (job `value`)."""

    trade_id: str
    valuation: str
    snapshot_id: str | None = None
    notional: float
    quantity: float
    unit_price: Estimate = Field(..., description="% of nominal")
    value: Estimate = Field(..., description="currency, desk side")
    result: Pricing
    reserve: ReserveNow | None = None
    quote: dict[str, Any] = {}


class BookPosition(Doc):
    name: str
    group: str
    quantity: float
    unit_price: Estimate
    value: Estimate
    reserve: ReserveNow | None = None
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    class_: str | None = Field(default=None, alias="class")


class BookGroup(Doc):
    label: str
    method: str
    model: str | None = None
    n_paths: int | None = None
    value: Estimate
    positions: int
    reason: str | None = None
    seconds: float | None = None
    note: str | None = None


class NameGreeks(Doc):
    delta: Estimate | None = None
    vega: Estimate | None = None
    vega_missing: list[Any] = []


class BookGreeks(Doc):
    by_name: dict[str, NameGreeks] = {}
    units: dict[str, Any] = {}
    note: str | None = None


class BookValuation(Doc):
    """The book valued in groups at the market in use (job `book`)."""

    value: Estimate
    value_net: float | None = Field(default=None, description="the value less the reserve left")
    reserve: float | None = None
    reserve_initial: float | None = None
    method: str
    model: str | None = None
    n_paths: int | None = None
    positions: list[BookPosition]
    groups: list[BookGroup]
    greeks: BookGreeks | None = None
    no_reserve: list[str] = []
    skipped: list[Any] = []
    warnings: list[str] = []
    snapshot_id: str | None = None
    seconds: float | None = None


# --- risk --------------------------------------------------------------------------------------------------------------
class RiskRunItem(Doc):
    run_id: str
    kind: str = Field(..., description="risk, ladder, scenarios, var, eod, pnl, pnl_detail")
    day: str
    run_key: str
    version: int
    code: str | None = None
    params: dict[str, Any] = {}
    created_at: str
    seconds: float | None = None


class RiskRunPage(Next):
    items: list[RiskRunItem]


class RiskGrid(Doc):
    strike_labels: list[str]
    tenor_labels: list[str]
    strikes: list[float] = []
    tenors: list[float] = []
    moneyness: str | None = None


class RiskPosition(Doc):
    name: str
    group: str | None = None
    quantity: float | None = None
    notional: float | None = None
    value: Estimate | float | None = None
    greeks: dict[str, Estimate] = Field({}, description="measure key 'greek|name|bucket' -> {value, se}")


class RiskRun(Doc):
    """A Greeks run (job `risk`, and its stored copy on GET /risk/runs/{run_id}): every measure of the book under keys
    "greek|name|bucket", their units, the strikes x tenors grid of the node vegas, and each position's own."""

    run_id: str
    kind: str | None = None
    book: dict[str, Estimate] = {}
    units: dict[str, Any] = {}
    grid: RiskGrid | None = None
    positions: list[RiskPosition] = []
    groups: list[dict[str, Any]] = []
    options: dict[str, Any] = {}
    bumps: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    seconds: float | None = None
    created_at: str | None = None


class LadderPosition(Doc):
    name: str
    group: str | None = None
    quantity: float | None = None
    values: dict[str, Estimate] = Field({}, description="'measure@rule@shift' -> {value, se}")


class LadderRun(Doc):
    """A spot ladder under the vol rules (job `ladder`): keys 'measure@rule@shift', shifts as fractions."""

    run_id: str
    points: list[float]
    rules: list[str]
    tenors: list[str] = []
    book: dict[str, Estimate]
    positions: list[LadderPosition] = []
    groups: list[dict[str, Any]] = []
    options: dict[str, Any] = {}
    seconds: float | None = None


class ScenarioResult(Doc):
    name: str
    description: str | None = None
    label: str | None = None
    shock: dict[str, Any] = {}
    book: Estimate = Field(..., description="the book's P&L under the scenario, currency")
    flags: dict[str, Any] = {}
    arbitrage: list[Any] = []


class ScenarioPosition(Doc):
    name: str
    group: str | None = None
    value: float | None = None
    pnl: dict[str, Estimate | dict[str, list[float]]] = Field(
        default={},
        description="scenario name -> the position's P&L; under `grid` (when asked), 'spot:vol' -> [value, se]",
    )


class ScenarioRun(Doc):
    """The scenario library run on the book (job `scenarios`)."""

    run_id: str
    scenarios: list[ScenarioResult]
    positions: list[ScenarioPosition] = []
    grid: dict[str, Any] | None = Field(default=None, description="spot x vol P&L grid: pnl[i][j] = [value, se]")
    version: int | None = None
    signed_by: str | None = None
    starting: bool = False
    seconds: float | None = None


class ScenarioDef(Doc):
    name: str
    description: str = ""
    label: str | None = None
    shock: dict[str, Any] = Field(
        default={},
        description="the shock's fields as `optlab.risk.shocks.Shock` reads them (moves as fractions, curves in bp)",
    )


class ScenarioVersion(Doc):
    version: int
    meta: dict[str, Any] = Field(default={}, description="signed_by, note")
    created_at: str | None = None


class ScenarioLibrary(Doc):
    """The signed scenario set in use (the starting values when nothing is saved) and its versions."""

    scenarios: list[ScenarioDef]
    version: int | None = None
    signed_by: str | None = None
    note: str | None = None
    created_at: str | None = None
    starting: bool = False
    history: list[ScenarioVersion] = []


class PnlDay(Doc):
    """One day of the explained P&L: the cascade of full revaluations, each step's [value, se], the Greeks' prediction
    of each step and what they leave unexplained."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    from_: str = Field(..., alias="from")
    to: str
    total: float
    steps: dict[str, list[float]] = Field({}, description="step -> [value, se], in the cascade's order")
    predicted: dict[str, float] = {}
    unexplained: dict[str, float] = {}
    release: float | None = Field(default=None, description="the reserve released that day")
    new_trades: list[Any] = []
    events: dict[str, Any] = {}


class PnlTrade(Doc):
    total: float
    steps: dict[str, float] = {}
    group: str | None = None


class PnlHistory(Doc):
    """The explained P&L cumulated from the EOD runs over a period (`note` says why it is empty)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    days: list[str] = []
    curve: list[float] = Field([], description="the cumulated P&L at the end of each day")
    daily: list[PnlDay] = []
    order: list[str] = Field([], description="the cascade's steps, in order")
    steps: dict[str, float] = {}
    predicted: dict[str, float] = {}
    unexplained: dict[str, float] = {}
    trades: dict[str, PnlTrade] = {}
    total: float | None = None
    runs: list[dict[str, Any]] = []
    eod: list[RiskRunItem] = []
    note: str | None = None


class VarTaylor(Doc):
    """The same VaR from the Greeks (delta, gamma, vega), against the full revaluation."""

    var: float | None = None
    es: float | None = None
    mean_abs_gap: float | None = None
    pnl: list[float] = []


class VarHorizon(Doc):
    """One VaR: a horizon (1 day, 10 days) or the stressed window, its distribution, the VaR day and what made it."""

    label: str
    horizon: int
    start: str
    end: str
    dates: list[str]
    pnl: list[Estimate] = Field(..., description="the book's P&L of each historical move, currency")
    var: Estimate = Field(..., description="the loss at the level, positive")
    es: float = Field(..., description="the mean loss beyond the ES level, positive")
    var_date: str = Field(..., description="the historical day whose move is the VaR")
    contributions: dict[str, dict[str, float]] = Field(
        default={}, description="'var' and 'es' -> position -> its P&L (on the VaR day; mean over the tail)"
    )
    factors: dict[str, Estimate] = Field(
        default={}, description="the VaR day's move one factor at a time, and the interaction"
    )
    taylor: VarTaylor | None = None
    n: int = 0
    seconds: float | None = None
    origins: dict[str, Any] = {}
    note: str | None = None
    window: dict[str, Any] | None = Field(default=None, description="the stressed window and how it was found")


class VarRun(Doc):
    """A historical VaR by full revaluation (job `var`): 1 day, 10 days and the stressed window."""

    run_id: str
    day: str
    names: list[str] = []
    currency: str | None = None
    level: float
    es_level: float
    factors: list[str] = []
    one_day: VarHorizon
    ten_day: VarHorizon | None = None
    stressed: VarHorizon | None = None
    origins: dict[str, Any] = {}
    scenarios: int | None = None
    seconds: float | None = None


class BacktestDay(Doc):
    day: str
    pnl: float
    exception: bool = Field(..., description="the loss went beyond the VaR")


class VarBacktest(Doc):
    """A VaR run's 1-day VaR against each stored day's explained P&L (one VaR, not a VaR per day)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    var_run: str | None = None
    var_day: str | None = None
    var: float | None = Field(default=None, description="the 1-day VaR, a positive loss, currency")
    level: float
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    days: list[BacktestDay] = []
    kupiec: dict[str, Any] | None = Field(
        default=None, description="Kupiec's test: exceptions, n, expected, rate, lr, p_value"
    )
    note: str | None = None


class EodPosition(Doc):
    trade_id: str
    group: str | None = None
    quantity: float | None = None
    value: Estimate
    notional: float | None = None
    reserve: float | None = Field(default=None, description="the reserve still held at the close")
    greeks: dict[str, Estimate] | None = None


class EodGroup(Doc):
    label: str
    method: str | None = None
    model: str | None = None
    n_paths: int | None = None
    currency: str | None = None
    value: Estimate
    positions: list[str] = []
    warnings: list[Any] = []


class EodRun(Doc):
    """A stored EOD run: the book's value and Greeks at a day's close, on that day's market from the lake."""

    run_id: str
    kind: Literal["eod"]
    day: str
    value: Estimate
    positions: list[EodPosition] = []
    groups: list[EodGroup] = []
    greeks: dict[str, Estimate] | None = Field(default=None, description='"greek|name|bucket" -> {value, se}')
    provenance: dict[str, Any] = Field(default={}, description="what each name's market was made of (origins only)")
    trades: list[dict[str, Any]] = Field(default=[], description="the trades alive that day: id, version, status")
    params: dict[str, Any] = {}
    code: str | None = None
    seconds: float | None = None
    created_at: str | None = None


class PnlRun(Doc):
    """A stored day of the explained P&L (kind `pnl`): the cascade from the day before's EOD run to this one's."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    run_id: str
    kind: Literal["pnl"]
    from_: str = Field(..., alias="from")
    to: str
    total: float
    steps: dict[str, list[float]] = {}
    predicted: dict[str, float] = {}
    unexplained: dict[str, float] = {}
    order: list[str] = []
    eod: list[str | None] = Field(default=[], description="the EOD runs of the two days")
    created_at: str | None = None


class PnlRuleSteps(Doc):
    """The spot, vol and skew steps of a day under one spot-vol rule, and what the Greeks of that rule predicted."""

    steps: dict[str, Estimate] = Field(default={}, description="spot, vol, skew: each a full revaluation, ± SE")
    predicted: dict[str, float] = Field(default={}, description="the rule's delta and gamma, vega per tenor, skew vega")
    unexplained: dict[str, float] = {}


class PnlNameDetail(Doc):
    """One name's vol move, opened: arrays by tenor, or (strikes, tenors) on the risk grid."""

    level: list[Estimate] = Field(default=[], description="per tenor: its at-the-money move on every strike")
    shape: list[Any] = Field(default=[], description="per tenor: its nodes beyond the level (± SE by tenor)")
    by_tenor: list[float] = Field(default=[], description="level + shape: which maturity made or lost")
    predicted_level: list[float] = Field(default=[], description="vega per tenor x the at-the-money move")
    moves: list[list[float]] = Field(default=[], description="the implied vol move at each node (strikes, tenors)")
    deltas: list[list[float]] = Field(default=[], description="each node's forward call delta after the spot step")
    nodes: list[list[Estimate]] | None = Field(default=None, description="node by node (strikes, tenors), ± SE")
    buckets: list[list[float]] | None = Field(default=None, description="the nodes by delta bucket (buckets, tenors)")
    skipped: list[int] = Field(default=[], description="tenors whose tent the book does not reach: zero, exactly")
    groups: list[str] = []


class PnlDetail(Doc):
    """A day of the explained P&L opened (job `pnl_detail`): the vol and skew steps node by node under one rule."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    run_id: str | None = None
    kind: Literal["pnl_detail"] | None = None
    from_: str = Field(..., alias="from")
    to: str
    rule: str
    detail: str
    rules: dict[str, PnlRuleSteps] = Field(default={}, description="by rule: the same total, split differently")
    unavailable: dict[str, str] = Field(default={}, description="a rule left out, and why")
    names: dict[str, PnlNameDetail] = {}
    residual: Estimate | None = Field(default=None, description="the part of the move the grid does not hold")
    vol_total: float = Field(..., description="vol + skew under the rule: the nodes and the residual add up to it")
    check: float = Field(..., description="vol_total less the nodes and the residual: zero but for rounding")
    grid: dict[str, Any] = {}
    pnl_run: str | None = None
    revaluations: int = 0
    seconds: float | None = None
    created_at: str | None = None


# a stored run (GET /risk/runs/{run_id}): its fields follow its kind (the job result's, plus kind and created_at)
StoredRun = RiskRun | LadderRun | ScenarioRun | VarRun | EodRun | PnlRun | PnlDetail


class EodDay(Doc):
    day: str
    run_id: str
    reused: bool = False
    value: float | None = None


class EodResult(Doc):
    """EOD runs over a period (job `eod`): one run per business day, reused when already stored."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    from_: str = Field(..., alias="from")
    to: str
    runs: list[EodDay]


# --- quant, admin ------------------------------------------------------------------------------------------------------
class Smile(Doc):
    """A snapshot's implied vols at one maturity, against moneyness K / F(T)."""

    T: float
    forward: float = Field(..., description="F(T) at the model's spot of 100")
    moneyness: list[float]
    iv: list[float]


class TermStructure(Doc):
    """A snapshot's at-the-money vol, forward (over spot) and discount factor along the maturities."""

    T: list[float]
    atm_vol: list[float]
    forward: list[float]
    discount: list[float]


class OtherUnderlyings(Doc):
    """The names a worst-of may hold beside the calibrated one: their typed inputs and correlations."""

    names: dict[str, dict[str, Any]] = {}
    corr: dict[str, float] = Field(default={}, description='"A|B" -> correlation')
    calibrated: str | None = None
    defaults: dict[str, float] = {}


class VolIndex(Doc):
    name: str
    dates: list[str]
    close: list[float]
    origin: str | None = None
    source: str | None = None


class BasketQuote(Doc):
    """One pseudo-quote: the option on the basket against the same options on its members, at one year."""

    basket: str = Field(description="the members joined by '+'")
    kind: str = Field(description="cvc (a call on the basket against calls on the members) or pvp (puts)")
    strike: float = Field(description="a fraction of the spot")
    price: float | None = Field(None, description="long the basket, short the members: in % of notional, undiscounted")
    price_se: float | None = Field(None, description="in % of notional")
    basket_vol: float | None = None
    rho: float | None = Field(None, description="the average correlation the quote was simulated with")
    implied_rho: float | None = Field(None, description="the flat correlation that gives the basket's price")
    origin: str | None = None


class BasketQuotes(Doc):
    """The latest day's CvC and PvP pseudo-quotes (origin synthetic: no broker level); `day` is null without any."""

    day: str | None = None
    rows: list[BasketQuote] = []


class ModelRisk(Doc):
    """The calibration's exotics under each model (price ± SE), and its fit by maturity; `note` when there is none."""

    exotics: dict[str, dict[str, dict[str, Estimate]]] = Field(
        default={}, description="product -> model -> {price: {value, se}}"
    )
    errors: list[dict[str, Any]] = Field(
        default=[], description="by maturity: quotes, spread and each model's error, bp"
    )
    note: str | None = None


class VolLabSet(Doc):
    underlying: str
    source: str | None = None
    days: list[str] = []
    first: str | None = None
    last: str | None = None
    n: int | None = None
    quality: list[Any] = []


class VolLabDays(Doc):
    """The days with option quotes in the lake, by name and source, and the fit's models and settings."""

    available: bool
    sets: list[VolLabSet] = []
    models: list[str] = []
    defaults: dict[str, Any] = {}
    shared: bool = False


class VolLabSlice(Doc):
    T: float
    expiry: str | None = None
    moneyness: list[float]
    iv: list[float | None]
    iv_bid: list[float | None] = []
    iv_ask: list[float | None] = []


class VolLabQuotes(Doc):
    """A day's cleaned chain: by maturity, the mid implied vol and those of the bid and the ask."""

    underlying: str
    day: str
    source: str | None = None
    slices: list[VolLabSlice] = []
    origin: str | None = None


class VolLabRun(Doc):
    calibration_id: str
    underlying: str
    day: str
    model: str
    source: str | None = None
    rmse_bp: float | None = None
    within_spread: float | None = None
    seconds: float | None = None
    origin: str | None = None
    code_version: str | None = None
    ingested: str | None = None


class VolLabRuns(Doc):
    runs: list[VolLabRun] = []


class FitRow(Doc):
    T: float
    n: int | None = None
    rmse_bp: float | None = None
    bias_bp: float | None = None
    within_spread: float | None = Field(
        default=None, description="the share of quotes the model puts inside the spread"
    )
    median_half_spread_bp: float | None = None


class FitCurve(Doc):
    T: float
    moneyness: list[float]
    iv: list[float | None]


class ModelFit(Doc):
    calibration_id: str | None = None
    params: dict[str, Any] = {}
    by_maturity: list[FitRow] = []
    overall: dict[str, Any] = Field(default={}, description="n, rmse_bp, within_spread")
    seconds: float | None = None
    curves: list[FitCurve] = []
    arbitrage: dict[str, Any] | None = None


class VolFit(Doc):
    """A fit of a day's quotes (job `fit`): each model's parameters, its error by maturity against the spread, and
    its curves; each model stored as a replayable run."""

    underlying: str | None = None
    day: str | None = None
    source: str | None = None
    origin: str | None = None
    fits: dict[str, ModelFit] = {}
    maturities: list[float] = []
    quotes_hash: str | None = None


class VolFitReproduction(Doc):
    """A stored fit run again (job `reproduce`): the same quotes, how far the parameters moved, and both errors."""

    calibration_id: str
    same_quotes: bool
    largest_parameter_gap: float | None = None
    identical: bool
    stored_rmse_bp: float | None = None
    new_rmse_bp: float | None = None


class ReplayRunItem(Doc):
    id: str
    kind: str = Field(..., description="desk (run here) or versioned (results/ of the repository)")
    trade_id: str | None = None
    underlyings: list[str] | None = None
    n_valuations: int | None = None
    strike: str | None = None
    events: int | None = None
    seconds: float | None = None


class ReplayRuns(Doc):
    available: bool = Field(..., description="the lake holds closes to replay on")
    runs: list[ReplayRunItem] = []
    presets: list[str] = []


class ReplayDay(Doc):
    date: str
    value: float | None = None
    se: float | None = None
    cash: float | None = None
    reserve: float | None = None
    release: float | None = None
    pnl: float | None = None
    explain: dict[str, float] | None = Field(default=None, description="the day's P&L by Greek, and the reserve")
    residual: float | None = None
    greeks: dict[str, dict[str, float]] = {}
    perf: dict[str, float] = Field(default={}, description="each name's close over its strike")
    layer: dict[str, str] = {}


class ReplayEvent(Doc):
    date: str
    kind: str
    amount: float | None = None
    payment: str | None = None


class Replay(Doc):
    """A trade's life on real closes (job `replay`, and its stored copy): its value and cash each valuation day, its
    events, the P&L explained by the Greeks with what they leave, and the reserve released."""

    trade_id: str
    underlyings: list[str] = []
    n_valuations: int | None = None
    daily: list[ReplayDay] = []
    events: list[ReplayEvent] = []
    explain: dict[str, Any] = {}
    inception: dict[str, Any] = {}
    reconciliation: dict[str, Any] = {}
    reserves: dict[str, Any] = {}
    sheet: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    seconds: dict[str, Any] = {}


class ScriptCheck(Doc):
    """A script read and checked: what it holds, or the first error at its line and column."""

    ok: bool
    message: str | None = None
    line: int | None = None
    col: int | None = None
    underlyings: list[str] = []
    params: dict[str, float] = {}
    events: int | None = None


class ScriptPrice(Doc):
    """A script priced under a model (job `script`)."""

    price: Estimate
    model: str
    n_paths: int
    seconds: float | None = None
    expected_life: float | None = None
    notional: float | None = None
    warnings: list[Any] = []


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


class NetworkInfo(Doc):
    """Whether the desk listens beyond this machine, and what would stop the LAN from reaching it (read-only)."""

    lan: bool = Field(..., description="the LAN profile is on (OPTLAB_LAN)")
    blockers: list[str] = Field(default=[], description="what blocks incoming connections (firewall, network profile)")


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
    "value": TradeValuation,
    "book": BookValuation,
    "risk": RiskRun,
    "ladder": LadderRun,
    "scenarios": ScenarioRun,
    "eod": EodResult,
    "pnl_detail": PnlDetail,
    "var": VarRun,
    "script": ScriptPrice,
    "replay": Replay,
    "fit": VolFit,
    "reproduce": VolFitReproduction,
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
