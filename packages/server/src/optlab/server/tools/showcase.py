"""Fill a separate desk store with a realistic book: a dozen products sold on past dates, each solved on the market of its own
strike date, with real closes as fixings and the market of the last date in the lake. Nothing touches the default store.

    optlab demo seed-showcase [--store PATH] [--day 2026-11-04] [--n-paths 20000]

It prints the command that opens the desk on this book. Trades already in the store are kept as they are.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from optlab.marketdata.derive.bank import wrapper_spreads
from optlab.marketdata.lake import Lake
from optlab.marketdata.pit import market_at, real_close
from optlab.marketdata.replay import ReplayConfig, filled_fixings, strike_trade
from optlab.server.api import Desk
from optlab.server.settings import Settings
from optlab.server.tools import say
from optlab.structured import preset
from optlab.structured.defaults import resolve

LOOK_BACK = ("2020-03-23", "2022-07-01")  # markets kept beside the current one: a crash, and a day with a real chain


@dataclass(frozen=True)
class Deal:
    trade_id: str
    preset: str
    names: tuple
    strike: str
    months: int
    notional: float
    counterparty: str
    changes: dict = field(default_factory=dict)
    quantity: float = -1.0  # the desk sold it
    coupon: float | None = None  # solved on the strike date's market when None


DEALS = (
    # Already called: a history in the Lifecycle screen, worth nothing today.
    Deal("SHOW-01", "phoenix", ("SPX",), "2024-11-15", 24, 10e6, "Private bank A"),
    # Struck before the fall of April 2025. A monthly coupon at 85 % was missed then paid back by its memory, no call before 2 years.
    Deal(
        "SHOW-02",
        "phoenix",
        ("SPX",),
        "2025-02-19",
        36,
        8e6,
        "Private bank B",
        {"coupon.frequency_months": 1, "coupon.barrier": 0.85, "autocall.first_call": 8, "downside.barrier": 0.65},
    ),
    # Same date, a daily barrier at 85 %: knocked in, and redeemed against the put at maturity if the index is below its strike.
    Deal(
        "SHOW-03",
        "barrier_reverse_convertible",
        ("SPX",),
        "2025-02-19",
        24,
        5e6,
        "Retail network C",
        {"downside.barrier": 0.85},
    ),
    Deal(
        "SHOW-04",
        "reverse_convertible",
        ("SPX",),
        "2026-03-16",
        12,
        3e6,
        "Retail network C",
        {"downside.strike": 0.95, "downside.barrier": 0.95},
    ),
    Deal("SHOW-05", "airbag", ("SPX",), "2025-06-16", 36, 4e6, "Insurer D"),
    Deal("SHOW-06", "capital_protected", ("SPX",), "2023-06-15", 60, 12e6, "Insurer D", {"upside.participation": 0.6}),
    Deal("SHOW-07", "athena", ("SPX",), "2026-02-17", 36, 6e6, "Private bank A", {"autocall.first_call": 4}),
    # Worst-ofs: each name on its own market from the lake.
    Deal(
        "SHOW-08",
        "phoenix",
        ("SPX", "NDX", "RUT"),
        "2025-09-15",
        36,
        7e6,
        "Family office E",
        {"autocall.first_call": 6, "coupon.barrier": 0.65, "downside.barrier": 0.55},
    ),
    Deal("SHOW-09", "phoenix", ("SPX", "SX5E"), "2026-03-16", 24, 5e6, "Private bank B", {"autocall.first_call": 4}),
    Deal(
        "SHOW-10",
        "barrier_reverse_convertible",
        ("SPX", "NDX"),
        "2026-06-15",
        18,
        2.5e6,
        "Family office E",
        {"downside.barrier": 0.65},
    ),
    # A swap with a floating funding leg, and a position the desk holds long.
    Deal(
        "SHOW-11",
        "phoenix",
        ("SPX",),
        "2025-12-15",
        36,
        15e6,
        "Hedge fund F",
        {
            "wrapper.format": "swap",
            "wrapper.funding": "floating",
            "wrapper.funding_frequency_months": 3,
            "autocall.first_call": 5,
        },
    ),
    Deal(
        "SHOW-12",
        "phoenix",
        ("SPX",),
        "2026-04-15",
        24,
        4e6,
        "Bought back from G",
        {"autocall.first_call": 4, "coupon.barrier": 0.75},
        quantity=0.5,
    ),
)


def sheet_of(lake: Lake, deal: Deal):
    """The term sheet of a deal, with the issuer's funding of its strike date."""
    who = (
        {"general.underlying": deal.names[0]}
        if len(deal.names) == 1
        else {"general.basket": "worst_of", "general.underlyings": list(deal.names)}
    )
    values = {**who, "general.maturity_months": deal.months, "general.notional": deal.notional, **deal.changes}
    spreads = wrapper_spreads(lake, deal.strike, deal.months / 12)
    if deal.changes.get("wrapper.format") == "swap":
        values["wrapper.funding_spread"] = spreads["wrapper.funding_spread"]
    else:
        values["wrapper.issuer_spread"] = spreads["wrapper.issuer_spread"]
    values["costs.side"] = "sell" if deal.quantity < 0 else "buy"
    return preset(deal.preset, **values)


def store_markets(desk: Desk, lake: Lake, day, look_back=LOOK_BACK, t_max: float = 3.0) -> str:
    """The market of the last date, in use from now on, and those kept beside it."""
    for past in look_back:
        desk.market_store.put(market_at(lake, "SPX", past, leverage="calibrate", T_max=t_max))
    snap = market_at(lake, "SPX", day, leverage="calibrate", T_max=t_max)
    sid = desk.market_store.put(snap)
    desk.use_snapshot({"snapshot_id": sid})
    return sid


def store_fixings(desk: Desk, lake: Lake, start, day, names) -> tuple[int, int]:
    """Real closes in the model's scale, where today's close is 100. A day with no close takes the last one."""
    levels, filled = filled_fixings(lake, names, start, day, "weekend")
    stored = 0
    for name in names:
        scale = real_close(lake, name, day)[0] / 100.0
        stored += desk.repo.put_fixings(name, {d: v / scale for d, v in levels[name].items()}, "lake")
    return stored, len(filled)


def seed(desk: Desk, lake: Lake, day, deals=DEALS, n_paths: int = 20_000, log=say, look_back=LOOK_BACK) -> list:
    """Markets, fixings, basket settings and trades. Returns the ids of the trades added."""
    day = pd.Timestamp(day).date()
    sid = store_markets(desk, lake, day, look_back)
    log(f"market in use: {sid}")
    names = sorted({n for d in deals for n in d.names} | {"SPX"})
    start = min(pd.Timestamp(d.strike).date() for d in deals)
    stored, filled = store_fixings(desk, lake, start, day, names)
    log(f"fixings: {stored} real closes of {', '.join(names)}, {filled} days without a close took the day before")
    known = {t.trade_id for t in desk.repo.trades()}
    cfg = ReplayConfig(model="lv", n_paths=n_paths, greeks=False)
    added = []
    for deal in deals:
        if deal.trade_id in known:
            log(f"{deal.trade_id}: already in the store")
            continue
        t0 = time.perf_counter()
        sheet = sheet_of(lake, deal)
        trade, info = strike_trade(
            lake,
            deal.trade_id,
            sheet,
            deal.strike,
            deal.quantity,
            cfg,
            deal.coupon,
            costs=resolve(desk.repo.defaults(), sheet).inputs(),
        )
        tags = ("showcase", f"preset:{deal.preset}")
        desk.repo.save_trade(dataclasses.replace(trade, counterparty=deal.counterparty, tags=tags, book="showcase"))
        if any(e["kind"] == "called" for e in desk.trade_events(deal.trade_id)["past"]):
            desk.update_trade(deal.trade_id, {"status": "called"})
        added.append(deal.trade_id)
        coupon = "no coupon" if info["coupon"] is None else f"coupon {100 * info['coupon']:.2f} %"
        held = trade.reserves["total"] if trade.reserves else 0.0
        log(
            f"{deal.trade_id}: {deal.preset} on {'/'.join(deal.names)}, struck {deal.strike}, {coupon}, reserve {held:.3f} %, {time.perf_counter() - t0:.1f} s"
        )
    return added


def default_store() -> Path:
    return Settings.load().data / "showcase" / "optlab.sqlite"


def main(store: str | Path | None = None, day: str | None = None, n_paths: int = 20_000, log=say) -> str:
    """Seed the showcase's database (`store`, by default `<data>/showcase/optlab.sqlite`) on the lake's market of `day`
    (by default its last SPX close); returns the snapshot left in use."""
    settings = Settings.load()
    lake = Lake(settings.lake)
    last = lake.read("prices_eod", where="instrument = 'SPX'", columns="max(obs_date) AS d")["d"].iloc[0]
    when = pd.Timestamp(day or last).date()
    desk = Desk(Path(store or default_store()), settings=settings, run_jobs=False)
    try:
        seed(desk, lake, when, n_paths=n_paths, log=log)
        sid = desk.ctx.snapshot_id
        for t in desk.repo.trades():
            events = desk.trade_events(t.trade_id)
            kinds = pd.Series([e["kind"] for e in events["past"]], dtype=str).value_counts().to_dict()
            log(
                f"{t.trade_id}: {t.counterparty}, past events {kinds}, {len(events['upcoming'])} to come"
                + (f", missing: {events['missing']}" if events["missing"] else "")
            )
    finally:
        desk.close()
    log("\nOpen the desk on this book:")
    log(f'optlab serve --store "{desk.db.path}" --market {sid} --open')
    return str(sid)
