"""Check the lake's FX against what it can be compared with, and measure what the quanto adjustment does: reports/fx_check.md.

    python scripts/lake_fx_check.py [--lake PATH] [--n-paths 100000]

Yahoo's futures and spot are read for this comparison only, never stored. The report holds gaps and statistics, not their levels.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from optlab.market import Market
from optlab.marketdata.derive.correl import closes_wide
from optlab.marketdata.derive.curves import CURVE_OF_CURRENCY
from optlab.marketdata.derive.fx import (
    fx_correlation_at,
    fx_vol_at,
    name_pair_series,
    quanto_link_at,
    realised_vol,
    spot_series,
)
from optlab.marketdata.derive.surfaces import surface_at
from optlab.marketdata.lake import Lake, sql_str
from optlab.marketdata.pit import basket_at, market_at
from optlab.marketdata.quality import open_issues
from optlab.marketdata.quotes import discount_function
from optlab.marketdata.sources import curl_get
from optlab.marketdata.sources.simple import fred_url, parse_fred
from optlab.marketdata.universe import pair_currencies
from optlab.models import LocalVolModel, time_grid
from optlab.structured.context import PricingContext
from optlab.structured.multiasset import BasketContext
from optlab.structured.presets import preset
from optlab.structured.quanto import integrated_vol
from optlab.structured.quote import quote

REPORT = Path(__file__).resolve().parents[1] / "reports" / "fx_check.md"
DATES = ("2020-03-23", "2022-07-01", "2026-11-04")
SEEDS = (2026, 2027, 2028, 2029, 2030)
FUTURES = {
    "EURUSD": ("6E", "EURUSD=X", False),
    "GBPUSD": ("6B", "GBPUSD=X", False),
    "USDJPY": ("6J", "JPY=X", True),
}  # root, spot, the contract is the inverse of the pair


def table(header, rows) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def source_gaps(lake: Lake) -> str:
    rows = []
    for pair in ("EURUSD", "USDJPY", "GBPUSD"):
        a, b = spot_series(lake, pair, source="ecb"), spot_series(lake, pair, source="fred")
        both = pd.concat({"a": a, "b": b}, axis=1, sort=True).dropna()
        gap = 1e4 * np.log(both["a"] / both["b"]).abs()
        rows.append(
            (
                pair,
                len(both),
                f"{gap.median():.0f}",
                f"{gap.quantile(0.99):.0f}",
                f"{gap.max():.0f}",
                f"{np.log(both['a']).diff().corr(np.log(both['b']).diff()):.3f}",
                f"{np.log(both['a']).diff(5).corr(np.log(both['b']).diff(5)):.3f}",
            )
        )
    return table(
        ("Pair", "Days", "Median gap (bp)", "99 % (bp)", "Largest (bp)", "Correlation, 1 day", "Correlation, 5 days"),
        rows,
    )


def imm_date(year: int, month: int) -> pd.Timestamp:
    """Last trading day of a CME currency future: two business days before the third Wednesday."""
    first = pd.Timestamp(year, month, 1)
    wednesday = first + pd.Timedelta(days=(2 - first.weekday()) % 7 + 14)
    return wednesday - pd.offsets.BDay(2)


def history(ticker: str) -> pd.Series:
    import yfinance as yf

    h = yf.Ticker(ticker).history(start="2015-01-01", auto_adjust=False)
    if h.empty:
        return pd.Series(dtype=float)
    out = h["Close"]
    out.index = out.index.tz_localize(None).normalize()
    return out


def futures_gaps(lake: Lake, pair: str) -> tuple[pd.DataFrame, int]:
    """Each quarterly CME contract of a pair over Yahoo's spot, against the same carry from our curves at its exact maturity, in the pair's
    own quotation. `6E=F` and the like are no use for this: they follow the nearest monthly contract, so the maturity is about two weeks
    and the hours between the two closes swamp the carry. From three months out they do not."""
    root, spot_ticker, inverted = FUTURES[pair]
    spot = history(spot_ticker)
    curves = {}
    base, quote = pair_currencies(pair)
    for currency in (base, quote):
        frame = lake.read(
            "discount_curves",
            where=f"curve = '{CURVE_OF_CURRENCY[currency]}'",
            columns="obs_date, tenor, zero_rate",
            order="obs_date, tenor",
        )
        frame["obs_date"] = pd.to_datetime(frame["obs_date"])
        curves[currency] = {d: (g["tenor"].to_numpy(), g["zero_rate"].to_numpy()) for d, g in frame.groupby("obs_date")}
    rows, contracts = [], 0
    for year in range(2020, 2032):
        for code, month in (("H", 3), ("M", 6), ("U", 9), ("Z", 12)):
            fut = history(f"{root}{code}{year % 100:02d}.CME")
            if fut.empty:
                continue
            contracts += 1
            expiry = imm_date(year, month)
            for day, f in fut.items():
                T = (expiry - day).days / 365.0
                if T < 0.25 or day not in spot.index or day not in curves[base] or day not in curves[quote]:
                    continue
                forward = 1.0 / f if inverted else f
                rows.append(
                    (
                        day,
                        T,
                        np.log(forward / spot[day]) / T,
                        np.interp(T, *curves[quote][day]) - np.interp(T, *curves[base][day]),
                    )
                )
    frame = pd.DataFrame(rows, columns=["day", "T", "market", "cip"]).set_index("day")
    frame["gap"] = 1e4 * (frame["market"] - frame["cip"])
    return frame, contracts


def cip_against_futures(lake: Lake) -> tuple[str, str, str]:
    def line(pair, label, g):
        return (
            pair,
            label,
            len(g),
            f"{1e4 * g['market'].median():.0f}",
            f"{1e4 * g['cip'].median():.0f}",
            f"{g['gap'].median():+.0f}",
            f"{g['gap'].abs().quantile(0.9):.0f}",
            f"{g['market'].corr(g['cip']):.2f}",
        )

    head = (
        "Days",
        "Futures carry (bp/yr)",
        "CIP on our curves (bp/yr)",
        "Median gap (bp/yr)",
        "90 % of absolute gap (bp/yr)",
        "Correlation",
    )
    buckets = (
        ("3 to 6 months", 0.25, 0.5),
        ("6 to 12 months", 0.5, 1.0),
        ("1 to 2 years", 1.0, 2.0),
        ("2 to 5 years", 2.0, 5.5),
    )
    by_t, by_year, notes = [], [], []
    for pair in FUTURES:
        frame, contracts = futures_gaps(lake, pair)
        by_t += [
            line(pair, label, frame[frame["T"].between(lo, hi, inclusive="left")])
            for label, lo, hi in buckets
            if frame["T"].between(lo, hi, inclusive="left").any()
        ]
        far = frame[frame["T"] >= 0.5]
        by_year += [line(pair, str(y), g) for y, g in far.groupby(far.index.year)]
        notes.append(
            f"{pair}: {contracts} contracts, {len(frame)} contract-days, {frame.index.min().date()} to {frame.index.max().date()}; from six months out, "
            f"median gap {far['gap'].median():+.0f} bp a year, correlation {far['market'].corr(far['cip']):.2f}."
        )
    return (
        table(("Pair", "Time to expiry", *head), by_t),
        table(("Pair", "Year, 6 months and more", *head), by_year),
        "\n".join(f"- {n}" for n in notes),
    )


def monthly_mean(rows: pd.DataFrame) -> pd.Series:
    return rows.set_index(pd.to_datetime(rows["obs_date"]))["rate"].astype(float).resample("MS").mean()


def new_curves(lake: Lake) -> str:
    """Coverage of the two curves, and the ten-year point against the OECD's monthly long rates through FRED, an independent compilation."""
    rows = []
    for label, curve, points, series in (
        ("Yen", "JPY.DISC", "JPY.JGB", "IRLTLT01JPM156N"),
        ("Sterling", "GBP.DISC", "GBP.GLC", "IRLTLT01GBM156N"),
    ):
        pillars = lake.read("discount_curves", where=f"curve = '{curve}'", columns="obs_date, tenor")
        by_day = pillars.groupby("obs_date")["tenor"]
        first = by_day.apply(lambda t: np.sort(t.to_numpy())[1] if (t < 2 / 365).any() and len(t) > 1 else np.nan)
        overnight = by_day.apply(lambda t: bool((t < 2 / 365).any()))
        ten = lake.read("rate_points", where=f"curve = '{points}' AND tenor = 10", columns="obs_date, rate")
        oecd = parse_fred(curl_get(fred_url(series)), series)
        both = pd.concat(
            {
                "ours": monthly_mean(ten),
                "oecd": oecd.set_index(pd.to_datetime(oecd["obs_date"]))["value"].astype(float),
            },
            axis=1,
            sort=True,
        ).dropna()
        gap = 1e2 * (both["ours"] - both["oecd"])
        rows.append(
            (
                label,
                f"{pd.Timestamp(pillars['obs_date'].min()).date()} to {pd.Timestamp(pillars['obs_date'].max()).date()}",
                by_day.ngroups,
                f"{100 * overnight.mean():.1f} %",
                f"{12 * first.median():.0f} months",
                f"{12 * first.quantile(0.95):.0f} months",
                len(both),
                f"{gap.median():+.0f}",
                f"{gap.abs().max():.0f}",
            )
        )
    return table(
        (
            "Curve",
            "Period",
            "Days",
            "Days with an overnight rate",
            "Next pillar, median",
            "95 %",
            "Months against the OECD",
            "Median gap at 10 years (bp)",
            "Largest (bp)",
        ),
        rows,
    )


def correlation_sources(lake: Lake) -> str:
    """The same correlation on the ECB fix and on H.10: how much the hour of the fix matters."""
    closes = closes_wide(lake, ("SPX", "SX5E"))
    ecb, fred = spot_series(lake, "EURUSD", source="ecb"), spot_series(lake, "EURUSD", source="fred")
    rows = []
    for name in ("SPX", "SX5E"):
        a, b = name_pair_series(closes, ecb, name, "EURUSD", 120), name_pair_series(closes, fred, name, "EURUSD", 120)
        both = pd.concat({"a": a, "b": b}, axis=1, sort=True).dropna()
        rows.append(
            (
                name,
                f"{both['a'].median():+.2f}",
                f"{both['b'].median():+.2f}",
                f"{(both['a'] - both['b']).abs().median():.2f}",
                f"{(both['a'] - both['b']).abs().quantile(0.95):.2f}",
                f"{both['a'].min():+.2f} to {both['a'].max():+.2f}",
            )
        )
    return table(("Name", "Median, ECB", "Median, H.10", "Median difference", "95 % of difference", "Range, ECB"), rows)


def links(lake: Lake) -> tuple[str, str]:
    vols, rows = realised_vol(spot_series(lake, "EURUSD"), 120), []
    for day in DATES:
        for label, name, payment in (
            ("SX5E paid in USD", "SX5E", "USD"),
            ("SPX paid in EUR", "SPX", "EUR"),
            ("SPX paid in JPY", "SPX", "JPY"),
            ("SPX paid in GBP", "SPX", "GBP"),
        ):
            snap = market_at(lake, name, day, payment=payment)
            q = snap.provenance["quanto"]
            ctx, own = PricingContext.from_snapshot(snap), PricingContext.from_snapshot(market_at(lake, name, day))
            shift = {T: 1e4 * float(np.log(ctx.market.F(T) / own.market.F(T))) for T in (1.0, 2.0, 5.0)}
            rows.append(
                (
                    day,
                    label,
                    f"{q['rho']:+.2f}",
                    f"{100 * q['vol_fx']:.1f} %",
                    f"{100 * float(own.surface.iv(0.0, 1.0)):.1f} %",
                    f"{shift[1.0]:+.0f}",
                    f"{shift[2.0]:+.0f}",
                    f"{shift[5.0]:+.0f}",
                )
            )
    vol = f"EURUSD realised volatility, 120 days: median {100 * vols.median():.1f} %, range {100 * vols.min():.1f} to {100 * vols.max():.1f} %."
    return table(("Date", "Case", "rho", "FX vol", "ATM 1y", "Forward shift 1y (bp)", "2y (bp)", "5y (bp)"), rows), vol


def lv_error(lake: Lake, day: str, n_pairs: int = 100_000, T: float = 2.0, seed: int = 7) -> str:
    """The adjustment is a deterministic drift. Under local vol the true one depends on the state: x gains -rho sigma_X (sigma_LV(t, x) -
    sigma_ATM(t)) per unit of time relative to the quanto forward. Measured on common draws, in bp of spot."""
    snap = market_at(lake, "SX5E", day, payment="USD")
    ctx = PricingContext.from_snapshot(snap)
    q = snap.provenance["quanto"]
    rho, vol_fx = q["rho"], q["vol_fx"]
    model = LocalVolModel(ctx.surface)
    grid = time_grid(np.array([T]), 1 / 52, 1 / 500)
    atm = np.diff(integrated_vol(grid, ctx.surface)) / np.diff(grid)
    rng = np.random.default_rng(seed)
    x_det, x_state = np.zeros(2 * n_pairs), np.zeros(2 * n_pairs)
    for i in range(1, len(grid)):
        t0, h = grid[i - 1], grid[i] - grid[i - 1]
        z = rng.standard_normal(n_pairs)
        z = np.concatenate([z, -z])
        v_d = model.inst_var(t0, x_det, np.zeros_like(x_det))
        v_s = model.inst_var(t0, x_state, np.zeros_like(x_state))
        x_det += -0.5 * v_d * h + np.sqrt(v_d * h) * z
        x_state += (-0.5 * v_s - rho * vol_fx * (np.sqrt(v_s) - atm[i - 1])) * h + np.sqrt(v_s * h) * z
    F, D = float(ctx.market.F(T)), float(ctx.market.D(T))
    F_own = float(PricingContext.from_snapshot(market_at(lake, "SX5E", day)).market.F(T))
    s_det, s_state, s_own = F * np.exp(x_det), F * np.exp(x_state), F_own * np.exp(x_det)
    rows = []
    for label, pay in (
        ("Discounted forward", lambda s: s / 100),
        ("Put 100 %", lambda s: np.maximum(1 - s / 100, 0)),
        ("Put 80 %", lambda s: np.maximum(0.8 - s / 100, 0)),
        ("Digital put 70 %", lambda s: (s < 70.0).astype(float)),
    ):
        a, b = pay(s_state), pay(s_det)
        diff = 0.5 * ((a - b)[:n_pairs] + (a - b)[n_pairs:])
        gap, se = 1e4 * D * diff.mean(), 1e4 * D * diff.std(ddof=1) / np.sqrt(n_pairs)
        rows.append(
            (
                label,
                f"{100 * D * b.mean():.2f}",
                f"{1e4 * D * (b - pay(s_own)).mean():+.1f}",
                f"{gap:+.1f}",
                f"{se:.2f}",
            )
        )
    head = (
        "Payoff, 2 years, % of notional",
        "With the deterministic drift",
        "The adjustment itself (bp)",
        "State-dependent minus deterministic (bp)",
        "SE (bp)",
    )
    return f"{day}: rho {rho:+.2f}, FX volatility {100 * vol_fx:.1f} %.\n\n" + table(head, rows)


def worst_of_effect(lake: Lake, day: str, n_paths: int) -> tuple[str, str]:
    """A Phoenix on SPX and SX5E: the coupon with the performances mixed as they were, and with the adjustment, on the same draws. Each
    seed gives one difference, and the spread of the differences gives their standard error."""
    d = pd.Timestamp(day).date()
    sheet = preset(
        "phoenix",
        **{
            "general.basket": "worst_of",
            "general.underlyings": ("SPX", "SX5E"),
            "general.strike_date": d,
            "general.calendar": "weekend",
        },
    )
    adjusted, _, _ = basket_at(lake, ["SPX", "SX5E"], d)
    own = PricingContext.from_snapshot(market_at(lake, "SX5E", d)).market
    t = np.asarray(own.to_dict()["pillars"], dtype=float)
    mixed_sx5e = Market(
        own.spot, t, discount_function(lake, "USD", d)(t), own.F(t)
    )  # dollar discounting, the euro forward
    mixed = BasketContext(("SPX", "SX5E"), {**adjusted.markets, "SX5E": mixed_sx5e}, adjusted.vols, adjusted.corr, d)
    in_eur, _, _ = basket_at(lake, ["SPX", "SX5E"], d, payment="EUR")

    def coupons(s, ctx):
        runs = [quote(s, ctx, model="lv", control=None, n_paths=n_paths, seed=seed) for seed in SEEDS]
        return np.array([1e4 * q.coupon_rate for q in runs]), float(np.mean([1e4 * q.coupon_rate_se for q in runs]))

    runs = {
        "USD, no adjustment (the old mix)": coupons(sheet, mixed),
        "USD, SX5E quanto": coupons(sheet, adjusted),
        "EUR, SPX quanto": coupons(sheet.replace(**{"general.payment_currency": "EUR"}), in_eur),
    }
    rows = [(label, f"{c.mean():.0f}", f"{se / np.sqrt(len(SEEDS)):.1f}") for label, (c, se) in runs.items()]
    diff = runs["USD, SX5E quanto"][0] - runs["USD, no adjustment (the old mix)"][0]
    note = f"The adjustment alone moves the coupon by {diff.mean():+.1f} bp a year, SE {diff.std(ddof=1) / np.sqrt(len(SEEDS)):.1f}, from {len(SEEDS)} seeds on common draws."
    return table(("Paid in", "Coupon (bp p.a.)", "SE (bp)"), rows), note


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lake", default=None)
    ap.add_argument("--n-paths", type=int, default=100_000)
    args = ap.parse_args()
    lake = Lake(args.lake)
    day = DATES[-1]
    by_t, by_year, futures_notes = cip_against_futures(lake)
    curve_table = new_curves(lake)
    adjust, vol_line = links(lake)
    effect, effect_note = worst_of_effect(lake, day, args.n_paths)
    lv = "\n\n".join(lv_error(lake, d) for d in ("2022-07-01", day))
    last_forwards = "; ".join(
        f"{p} {pd.to_datetime(lake.read('fx_forwards', where=f'pair = {sql_str(p)}', columns='obs_date')['obs_date']).max().date()}"
        for p in FUTURES
    )
    no_curve = open_issues(lake, "check_name = 'no_curve'")
    vol, vol_origin, vol_method = fx_vol_at(lake, "EURUSD", day)
    rho, rho_origin = fx_correlation_at(lake, "SX5E", "EURUSD", day)
    surface = surface_at(lake, "SX5E", day)
    link = quanto_link_at(lake, "SX5E", "USD", day)
    REPORT.write_text(
        f"""# FX in the lake and the quanto adjustment

Written by `scripts/lake_fx_check.py`, {dt.date.today().isoformat()}. Gaps and statistics only: no vendor's levels.

## What is loaded

| Item | Source | Origin |
|---|---|---|
| EURUSD spot | ECB reference rate, 14:15 CET | observed |
| USDJPY, GBPUSD spots | Crosses of two ECB rates of one fix | derived |
| The three spots, again | Fed H.10 noon rates through FRED, kept as a check | observed |
| Forwards of the three pairs, 1 month to 5 years | Covered parity on the desk's government curves | derived |
| Yen curve | Ministry of Finance JGB par yields, bootstrapped, with the Bank of Japan's overnight call rate as the shortest pillar | derived |
| Sterling curve | Bank of England fitted gilt spot rates (GLC), with SONIA as the shortest pillar | derived |
| FX volatility | Realised, 60, 120 and 250 days | derived, method `realized` |
| Name against pair correlation | Realised on overlapping 3-day returns | derived |

**Payment currencies.** A term sheet can pay in USD, EUR, JPY or GBP, and {len(no_curve)} `no_curve` issues are open. A name and its payment currency need a pair between them, and the registry holds EURUSD, USDJPY and GBPUSD. So a dollar name pays in any of the four and a euro name in EUR or USD: SX5E in yen or sterling is refused (`no enabled pair`), not computed through a cross.

**No implied FX volatility.** Cboe answers 403 on EVZ and Yahoo no longer serves it. A realised volatility leaves out the FX volatility premium, and the provenance of each quanto market says what it used through `vol_origin` and `vol_method`.

Latest forward dates: {last_forwards}. On {day}: EURUSD volatility {100 * vol:.1f} % ({vol_origin}, {vol_method}), SX5E against EURUSD {rho:+.2f} ({rho_origin}), SX5E surface `{surface.layer}` ({surface.origin}).

## Spots: ECB against H.10

{source_gaps(lake)}

The two sources are about four hours apart, which alone decorrelates one-day returns; five-day returns much less. The quality report lists the days above 2 %.

## Yen and sterling curves

{curve_table}

Below a year the yen curve is a straight line from the call rate to the one-year zero rate, as the Ministry's file starts at one year. The sterling short end only holds the maturities gilts reach that day. The ten-year comparison is a par yield for yen and a zero rate for sterling, against a monthly average of benchmark yields: it checks levels and units, not the bootstrap, which the tests check by repricing par bonds. The Bank of England's spot rates are continuous: integrating the forward sheet of its own workbook gives them to 0.0001 bp, against 8 bp if they were semi-annual.

## Forwards: covered parity against the CME future

Each quarterly contract over the spot, against the same carry from our curves at the contract's exact maturity, three months or more from expiry and in the pair's own quotation (a 6J contract is the inverse of USDJPY). Yahoo's two closes are hours apart and that noise is divided by the maturity, so the longer buckets are the ones to read.

{futures_notes}

{by_t}

{by_year}

A negative gap means the market's forward sits below ours, in the pair's quotation. Our curves are government curves, not OIS, and the forwards carry no cross-currency basis. The three pairs share these limits, and from a year out the yen and sterling gaps have the size of the euro's. Below a year this check says little for any pair: at 3 to 6 months the noise of the closes swamps the carry (correlation 0.08 to 0.33), so the straight line of the yen short end is not validated by it.

## Correlation: does the hour of the fix matter?

Rolling 120-day correlation with EURUSD on 3-day returns, on the ECB fix and on H.10.

{correlation_sources(lake)}

## The size of the adjustment

{vol_line}

{adjust}

A positive shift means a negative correlation between the name and X, the domestic price of its currency.

## A Phoenix on SPX and SX5E, {day}

Local vol, {args.n_paths:,} paths for each of {len(SEEDS)} seeds, each name on its own surface. SE is that of the mean.

{effect}

{effect_note}

## What the deterministic adjustment leaves out

The forward is adjusted with the at-the-money forward volatility. Under local vol the true quanto drift depends on the state, as the local volatility is higher below the forward. SX5E paid in USD, on common draws:

{lv}

Read the two middle columns together. In absolute terms the term left out is small, but on a put away from the money it is not small against the adjustment itself. The closed-form validation holds under Black-Scholes, where this term is zero.

## Limits

- rho and the FX volatility are constants over the life of a product, read on one date, with no term structure.
- Gilts and JGBs discount a yen or sterling payment at the government's rate, not at a funding rate. The Bank of England does publish an OIS curve, unused here as the dollar side has none.
- A vol bump keeps the adjustment of the base market: the vega misses about {abs(link.rho) * link.vol_fx * 0.01 * 2.0 * 1e4:.1f} bp of 2-year forward per vol point on SX5E in USD, on {day}.
- The pseudo CvC and PvP of `basket_spreads` stay in each name's own currency, as their docstring says.
- The Pricer and the Book still build worst-ofs on flat inputs (`Desk._basket_ctx`): a quote says the performances are not adjusted, and an explicit payment currency is refused until they read the lake's market.
""",
        encoding="utf-8",
    )
    print(f"written {REPORT}")


if __name__ == "__main__":
    main()
