"""FX in the lake: parsers on small fixtures, loads without network, forwards, volatility, correlation and the quanto market at a date."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from optlab.core.instruments import enabled, enabled_names, instrument, pair_currencies
from optlab.marketdata.derive.carry import build_carry, cip_forward
from optlab.marketdata.derive.curves import build_discount_curves
from optlab.marketdata.derive.fx import (
    build_fx_correl,
    build_fx_forwards,
    build_fx_vols,
    forward_rows,
    fx_correlation_at,
    fx_vol_at,
    name_pair_series,
    quanto_link_at,
    quoted_pair,
    realised_vol,
    spot_series,
)
from optlab.marketdata.derive.surfaces import ShapePrior, build_reconstructed
from optlab.marketdata.ingest import load_fx
from optlab.marketdata.lake import Lake
from optlab.marketdata.pit import basket_at, market_at, truncated_copy
from optlab.marketdata.quality import check_fx
from optlab.marketdata.quotes import discount_function
from optlab.marketdata.sources.fx import ecb_pairs, parse_ecb_fx, parse_fred_fx
from optlab.marketdata.synthetic import synthetic_lake
from optlab.structured import preset, quote
from optlab.structured.context import PricingContext

DAY = dt.date(2020, 6, 15)
HEADER = "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS,TITLE\n"


def ecb_csv(currency, rows):
    return (
        HEADER
        + "".join(f'EXR.D.{currency}.EUR.SP00.A,D,{currency},EUR,SP00,A,{d},{v},A,"ECB, 2.15 pm"\n' for d, v in rows)
    ).encode()


def fred_csv(series, rows):
    return ("observation_date," + series + "\n" + "".join(f"{d},{v}\n" for d, v in rows)).encode()


# --- parsers ---


def test_ecb_rates_parse_and_a_missing_day_is_dropped():
    out = parse_ecb_fx(ecb_csv("USD", [("2020-01-02", 1.1193), ("2020-01-03", ""), ("2020-01-06", 1.1194)]), "USD")
    assert list(out["value"]) == [1.1193, 1.1194] and out["obs_date"].iloc[0] == dt.date(2020, 1, 2)


def test_ecb_refuses_html_another_series_and_absurd_levels():
    with pytest.raises(ValueError, match="HTML"):
        parse_ecb_fx(b"<html>maintenance</html>", "USD")
    with pytest.raises(ValueError, match="holds"):
        parse_ecb_fx(ecb_csv("GBP", [("2020-01-02", 0.85)]), "USD")
    with pytest.raises(ValueError, match="EURUSD: 1 rates outside"):
        parse_ecb_fx(ecb_csv("USD", [("2020-01-02", 119.3)]), "USD")


def test_crosses_are_derived_and_need_both_rates():
    per_euro = {
        "USD": parse_ecb_fx(ecb_csv("USD", [("2020-01-02", 1.10), ("2020-01-03", 1.20)]), "USD"),
        "JPY": parse_ecb_fx(ecb_csv("JPY", [("2020-01-02", 121.0)]), "JPY"),
        "GBP": parse_ecb_fx(ecb_csv("GBP", [("2020-01-02", 0.85), ("2020-01-03", 0.90)]), "GBP"),
    }
    rows = ecb_pairs(per_euro)
    usd = rows[rows["pair"] == "EURUSD"]
    jpy = rows[rows["pair"] == "USDJPY"]
    gbp = rows[rows["pair"] == "GBPUSD"].sort_values("obs_date")
    assert set(usd["origin"]) == {"observed"} and set(jpy["origin"]) == set(gbp["origin"]) == {"derived"}
    assert len(usd) == 2 and len(jpy) == 1  # no yen on the 3rd: no cross, nothing carried over
    assert jpy["rate"].iloc[0] == pytest.approx(121.0 / 1.10, rel=1e-12)
    assert gbp["rate"].iloc[1] == pytest.approx(1.20 / 0.90, rel=1e-12)


def test_h10_parses_in_the_pair_convention():
    out = parse_fred_fx(fred_csv("DEXJPUS", [("2020-01-02", 108.28), ("2020-01-03", "")]), "USDJPY")
    assert list(out["pair"]) == ["USDJPY"] and out["rate"].iloc[0] == 108.28
    with pytest.raises(ValueError, match="outside"):
        parse_fred_fx(fred_csv("DEXUSEU", [("2020-01-02", 108.28)]), "EURUSD")


# --- the registry ---


def test_pairs_are_enabled_but_not_offered_as_underlyings():
    assert {i.id for i in enabled("fx")} == {"EURUSD", "USDJPY", "GBPUSD"}
    assert not set(enabled_names()) & {"EURUSD", "USDJPY", "GBPUSD"} and "SPX" in enabled_names()
    assert pair_currencies("USDJPY") == ("USD", "JPY") and instrument("USDJPY").currency == "JPY"


def test_a_pair_is_found_in_either_direction():
    assert quoted_pair("EUR", "USD") == ("EURUSD", 1.0)
    assert quoted_pair("USD", "EUR") == ("EURUSD", -1.0)
    with pytest.raises(KeyError, match="no enabled pair"):
        quoted_pair("EUR", "CHF")


# --- a synthetic lake with both curves and a euro index ---


def rate_rows(days, seed=3):
    """(ECB rows per currency, H.10 rows per series), with the EUR returns correlated to the SX5E returns of `fx_world`."""
    rng = np.random.default_rng(seed)
    eurusd = 1.1 * np.exp(np.cumsum(0.005 * rng.standard_normal(len(days))))
    return (
        eurusd,
        120.0 * np.exp(np.cumsum(0.004 * rng.standard_normal(len(days)))),
        0.87 * np.exp(np.cumsum(0.004 * rng.standard_normal(len(days)))),
    )


def fetchers(days, eurusd, eurjpy, eurgbp):
    iso = [d.date().isoformat() for d in days]
    by_ccy = {"USD": eurusd, "JPY": eurjpy, "GBP": eurgbp}
    by_series = {"DEXUSEU": eurusd * 1.0005, "DEXJPUS": eurjpy / eurusd, "DEXUSUK": eurusd / eurgbp}

    def ecb(url, headers=None):
        return ecb_csv(url.split("/EXR/D.")[1][:3], zip(iso, by_ccy[url.split("/EXR/D.")[1][:3]]))

    def fred(url):
        series = url.split("id=")[1].split("&")[0]
        return fred_csv(series, zip(iso, by_series[series]))

    return ecb, fred


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("fx")
    lake, days, _ = synthetic_lake(root / "lake")
    rng = np.random.default_rng(17)
    eurusd, eurjpy, eurgbp = rate_rows(days)
    # SX5E co-moves with EURUSD by construction: returns with a known sign
    r_fx = np.diff(np.log(eurusd), prepend=np.log(eurusd[0]))
    sx5e = 3500.0 * np.exp(np.cumsum(-0.8 * r_fx + 0.006 * rng.standard_normal(len(days))))
    lake.ingest(
        "prices_eod",
        pd.DataFrame(
            {
                "instrument": "SX5E",
                "obs_date": days,
                "close": sx5e,
                "open": sx5e,
                "high": sx5e,
                "low": sx5e,
                "adj_close": sx5e,
            }
        ),
        "yahoo",
        "observed",
    )
    wiggle = 1 + 0.1 * np.sin(np.arange(len(days)) / 20)
    lake.ingest(
        "vol_indices",
        pd.DataFrame({"index_name": "V2TX", "obs_date": days, "close": 22.0 * wiggle}),
        "stoxx",
        "observed",
    )
    lake.ingest(
        "rate_points",
        pd.concat(
            [
                pd.DataFrame({"curve": "EUR.AAA", "tenor": t, "obs_date": days, "rate": r})
                for t, r in ((0.25, -0.4), (1.0, -0.3), (5.0, 0.0), (10.0, 0.2))
            ]
        ),
        "ecb",
        "observed",
    )
    build_discount_curves(lake, "2019-01-01")
    build_carry(lake, [instrument("SX5E")])
    build_reconstructed(lake, ("SX5E",), "2019-01-01", prior=ShapePrior(-0.7, 1.0, 0.3, 0.05, source="test"))
    ecb, fred = fetchers(days, eurusd, eurjpy, eurgbp)
    first = load_fx(lake, fetch=ecb, fred_fetch=fred, pause=lambda: None)
    build_fx_forwards(lake)
    build_fx_vols(lake)
    build_fx_correl(lake, ("SPX", "SX5E"))
    return root, lake, days, first, (ecb, fred), (eurusd, sx5e)


@pytest.mark.slow
def test_the_load_labels_its_origins_and_is_idempotent(world):
    _, lake, days, first, (ecb, fred), _ = world
    assert sum(r["n_new"] for r in first) == 6 * len(days)
    again = load_fx(lake, fetch=ecb, fred_fetch=fred, pause=lambda: None)
    assert sum(r["n_new"] + r["n_changed"] for r in again) == 0
    rows = lake.read("fx_spots", columns="pair, source, origin")
    origin = {(p, s): set(g["origin"]) for (p, s), g in rows.groupby(["pair", "source"])}
    assert (
        origin[("EURUSD", "ecb")] == {"observed"}
        and origin[("USDJPY", "ecb")] == {"derived"}
        and origin[("GBPUSD", "ecb")] == {"derived"}
    )
    assert all(origin[(p, "fred")] == {"observed"} for p in ("EURUSD", "USDJPY", "GBPUSD"))
    assert any(m["name"] == "EXR_USD.csv" for m in lake.manifest())


def test_a_day_of_forwards_matches_covered_parity_on_the_lakes_curves(world):
    _, lake, _, _, _, _ = world
    rows = lake.read("fx_forwards", where=f"pair = 'EURUSD' AND obs_date = DATE '{DAY}'", order="tenor")
    assert set(rows["origin"]) == {"derived"} and len(rows) == 7
    S = float(spot_series(lake, "EURUSD", DAY).iloc[-1])
    T = rows["tenor"].to_numpy()
    Dd, Df = discount_function(lake, "USD", DAY)(T), discount_function(lake, "EUR", DAY)(T)
    np.testing.assert_allclose(rows["forward"], cip_forward(S, Dd, Df), rtol=1e-12)
    np.testing.assert_allclose(rows["forward"] - rows["points"], S, rtol=1e-12)
    np.testing.assert_allclose(
        np.log(rows["forward"] / S) / T, -(np.log(Dd) - np.log(Df)) / T, rtol=1e-10
    )  # the rate differential
    assert (rows["forward"] > S).all()  # dollar rates are above euro rates in this lake


def test_a_flat_curve_gives_the_exact_forward_and_a_gap_in_the_curves_is_skipped():
    day = pd.Timestamp("2020-01-02")
    flat = lambda r: {day: (np.array([1.0, 5.0]), np.array([r, r]))}  # noqa: E731
    rows = forward_rows(pd.Series({day: 1.1, day + pd.Timedelta(days=30): 1.2}), flat(0.01), flat(0.03), tenors=(2.0,))
    assert len(rows) == 1  # 30 days later, the last curve is too old
    assert rows["forward"].iloc[0] == pytest.approx(1.1 * np.exp(0.02 * 2.0), rel=1e-12)


def test_pairs_without_a_curve_have_no_forward_and_say_so(world):
    _, lake, _, _, _, _ = world
    assert set(lake.read("fx_forwards", columns="pair")["pair"]) == {"EURUSD"}
    issues = lake.read("quality_issues", where="dataset = 'fx_forwards' AND check_name = 'no_curve'")
    assert set(issues["item"]) == {"USDJPY", "GBPUSD"} and all("JPY" in d or "GBP" in d for d in issues["detail"])


@pytest.mark.slow
def test_the_volatility_carries_its_origin_and_is_the_realised_one(world):
    _, lake, _, _, _, (eurusd, _) = world
    vol, origin, method = fx_vol_at(lake, "EURUSD", DAY, 120)
    assert (origin, method) == ("derived", "realized")
    assert vol == pytest.approx(0.005 * np.sqrt(252), rel=0.2)
    s = spot_series(lake, "EURUSD", DAY)
    assert vol == pytest.approx(float(realised_vol(s, 120).iloc[-1]), rel=1e-12)
    with pytest.raises(KeyError, match="no EURUSD volatility"):
        fx_vol_at(lake, "EURUSD", dt.date(2019, 1, 10), 120)


def test_the_correlation_has_the_sign_of_the_construction_and_flips_with_the_pair(world):
    _, lake, _, _, _, _ = world
    corr, origin = fx_correlation_at(lake, "SX5E", "EURUSD", DAY, 120)
    assert origin == "derived" and corr < -0.2
    link = quanto_link_at(lake, "SX5E", "USD", DAY)
    assert link.rho == pytest.approx(corr, rel=1e-12) and not link.meta["inverted"]
    inverse = quanto_link_at(lake, "SPX", "EUR", DAY)
    spx_corr, _ = fx_correlation_at(lake, "SPX", "EURUSD", DAY, 120)
    assert inverse.rho == pytest.approx(-spx_corr, rel=1e-12) and inverse.meta["inverted"]
    assert inverse.vol_fx == link.vol_fx
    assert (
        link.meta["vol_origin"] == "derived"
        and link.meta["rho_origin"] == "derived"
        and link.meta["vol_method"] == "realized"
    )


def test_fx_correlation_uses_multi_day_returns(world):
    _, lake, days, _, _, _ = world
    closes = lake.read("prices_eod", where="instrument = 'SX5E'", columns="obs_date, close", order="obs_date")
    wide = pd.DataFrame({"SX5E": closes["close"].to_numpy()}, index=pd.to_datetime(closes["obs_date"]))
    spots = spot_series(lake, "EURUSD")
    s = name_pair_series(wide, spots, "SX5E", "EURUSD", 60)
    r = np.log(wide.join(spots.rename("EURUSD"), how="inner")).diff(3).dropna()
    assert s.iloc[-1] == pytest.approx(r["SX5E"].iloc[-60:].corr(r["EURUSD"].iloc[-60:]), rel=1e-10)


def test_quality_flags_a_jump_a_stale_series_and_a_disagreement():
    days = [dt.date(2020, 1, d) for d in (6, 7, 8)]
    spots = pd.DataFrame(
        {
            "pair": "EURUSD",
            "source": ["ecb"] * 3 + ["fred"] * 3,
            "obs_date": days * 2,
            "rate": [1.1, 1.1, 1.2, 1.1, 1.1, 1.1],
        }
    )
    issues = {(i["check_name"], i["detail"].split(":")[0]) for i in check_fx(spots, dt.date(2020, 1, 9))}
    assert ("large_move", "ecb") in issues and any(c == "sources_disagree" for c, _ in issues)
    assert any(i["check_name"] == "stale" for i in check_fx(spots, dt.date(2020, 3, 1)))


# --- the quanto market at a date ---


def test_a_quanto_market_has_the_payment_curve_the_adjusted_forward_and_its_labels(world):
    _, lake, _, _, _, _ = world
    native = market_at(lake, "SX5E", DAY)
    quanto = market_at(lake, "SX5E", DAY, payment="USD")
    assert native.provenance["payment"] == "EUR" and "quanto" not in native.provenance
    assert quanto.provenance["payment"] == "USD" and quanto.provenance["discount"]["curve"] == "USD"
    meta = quanto.provenance["quanto"]
    assert meta["vol_origin"] == "derived" and meta["pair"] == "EURUSD" and meta["rho"] < 0
    assert native.snapshot_id != quanto.snapshot_id and native.spot_scale == quanto.spot_scale
    ctx_n, ctx_q = PricingContext.from_snapshot(native), PricingContext.from_snapshot(quanto)
    assert ctx_q.payment_currency == "USD" and ctx_n.payment_currency == "EUR"
    disc = discount_function(lake, "USD", DAY)
    for T in (0.5, 1.0, 2.0):
        assert float(ctx_q.market.D(T)) == pytest.approx(float(disc(T)), rel=1e-6)
        # rho < 0: the forward goes up, by about rho sigma_X int sigma_S
        ratio = float(ctx_q.market.F(T) / ctx_n.market.F(T))
        assert ratio > 1 and np.log(ratio) == pytest.approx(
            -meta["rho"] * meta["vol_fx"] * float(ctx_n.surface.iv(0.0, T)) * T, rel=0.25
        )


def test_a_native_market_is_the_same_market_as_before(world):
    _, lake, _, _, _, _ = world
    assert market_at(lake, "SPX", DAY).snapshot_id == market_at(lake, "SPX", DAY, payment="USD").snapshot_id


def test_a_worst_of_on_the_lake_is_adjusted_on_the_foreign_name_only(world):
    _, lake, _, _, _, _ = world
    ctx, snaps, meta = basket_at(lake, ["SPX", "SX5E"], DAY)
    assert ctx.payment_currency == "USD" and meta["payment"] == "USD" and set(meta["quanto"]) == {"SX5E"}
    assert float(ctx.markets["SX5E"].D(1.0)) == pytest.approx(float(ctx.markets["SPX"].D(1.0)), rel=1e-6)
    assert "quanto" not in snaps["SPX"].provenance
    in_euros, _, meta_eur = basket_at(lake, ["SPX", "SX5E"], DAY, payment="EUR")
    assert (
        in_euros.payment_currency == "EUR"
        and set(meta_eur["quanto"]) == {"SPX"}
        and meta_eur["quanto"]["SPX"]["inverted"]
    )
    sheet = preset(
        "phoenix",
        **{
            "general.basket": "worst_of",
            "general.underlyings": ("SPX", "SX5E"),
            "general.strike_date": DAY,
            "general.calendar": "weekend",
        },
    )
    q = quote(sheet, ctx, model="lv", control=None, n_paths=4000, batch=4000, seed=5)
    assert not any("not adjusted for FX" in w for w in q.warnings)
    with pytest.raises(ValueError, match="pays in USD, this market was built to pay in EUR"):
        quote(sheet, in_euros, model="lv", control=None, n_paths=4000, batch=4000, seed=5)


@pytest.mark.slow
def test_erasing_the_future_changes_no_quanto_market(world, tmp_path):
    _, lake, _, _, _, _ = world
    cut = truncated_copy(lake, tmp_path / "cut", DAY)
    assert pd.to_datetime(cut.read("fx_spots")["obs_date"]).max().date() <= DAY
    a, b = market_at(lake, "SX5E", DAY, payment="USD"), market_at(cut, "SX5E", DAY, payment="USD")
    assert a.snapshot_id == b.snapshot_id
    # and recomputing the derived tables from the truncated data gives the same inputs
    fresh = Lake(tmp_path / "fresh")
    for table in ("prices_eod", "fx_spots"):
        rows = cut.read(table)
        for (source, origin), g in rows.groupby(["source", "origin"]):
            fresh.ingest(table, g, source, origin)
    build_fx_vols(fresh)
    build_fx_correl(fresh, ("SPX", "SX5E"))
    assert fx_vol_at(fresh, "EURUSD", DAY)[0] == pytest.approx(fx_vol_at(lake, "EURUSD", DAY)[0], rel=1e-12)
    assert fx_correlation_at(fresh, "SX5E", "EURUSD", DAY)[0] == pytest.approx(
        fx_correlation_at(lake, "SX5E", "EURUSD", DAY)[0], rel=1e-12
    )


def test_a_lake_without_fx_refuses_a_quanto_rather_than_assuming(tmp_path):
    lake, _, _ = synthetic_lake(tmp_path / "lake", "2019-06-01", "2020-07-01")
    with pytest.raises(KeyError, match="no EURUSD volatility"):
        market_at(lake, "SPX", DAY, payment="EUR")
