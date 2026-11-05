"""Point in time: the anti-look-ahead test, and the snapshot's plug into the pricing contexts."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from _lake_fixture import derive_all, fill_observed, synthetic_lake

from optlab.marketdata.lake import Lake
from optlab.marketdata.pit import basket_at, context_at, market_at, real_close, real_fixings, truncated_copy
from optlab.marketdata.snapshot import MarketSnapshot, to_model_scale
from optlab.structured import preset, quote

DAY = dt.date(2020, 6, 15)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("lake")
    return (root, *synthetic_lake(root / "lake"))


def phoenix(strike):
    return preset("phoenix", **{"general.strike_date": strike, "general.calendar": "weekend"})


def price_on(lake, day, seed=5):
    ctx, snap = context_at(lake, "SPX", day)
    q = quote(phoenix(day), ctx, model="lv", control=None, n_paths=4000, batch=4000, seed=seed)
    return q.coupon_rate, q.result.price, snap


def test_erasing_the_future_changes_no_past_price(world, tmp_path):
    root, lake, days, paths = world
    cut = truncated_copy(lake, tmp_path / "cut", DAY)
    assert pd.to_datetime(cut.read("prices_eod")["obs_date"]).max().date() <= DAY
    assert pd.to_datetime(lake.read("prices_eod")["obs_date"]).max().date() > DAY
    a, b = market_at(lake, "SPX", DAY), market_at(cut, "SPX", DAY)
    assert a.snapshot_id == b.snapshot_id and a.spot_scale == b.spot_scale
    assert price_on(lake, DAY)[:2] == price_on(cut, DAY)[:2]


def test_rederiving_after_erasing_the_future_gives_the_same_market(world, tmp_path):
    """Stronger: the derived tables recomputed from the observed rows of the past alone are the same as those computed on
    the whole history, so no derivation uses a later date."""
    root, lake, days, paths = world
    past = days[days <= pd.Timestamp(DAY)]
    fresh = Lake(tmp_path / "fresh")
    fill_observed(fresh, past, {n: p[:len(past)] for n, p in paths.items()})
    derive_all(fresh, str(days[0].date()))
    a, b = market_at(lake, "SPX", DAY), market_at(fresh, "SPX", DAY)
    assert a.market == b.market and a.surface == b.surface and a.heston == b.heston
    assert a.provenance["carry"]["dividend_yield"] == b.provenance["carry"]["dividend_yield"]
    ctx_a, ctx_b = basket_at(lake, ["SPX", "NDX"], DAY)[0], basket_at(fresh, ["SPX", "NDX"], DAY)[0]
    assert (ctx_a.corr == ctx_b.corr).all()


def test_changing_a_future_price_changes_nothing_but_a_past_one_does(world, tmp_path):
    root, lake, days, paths = world
    twisted = {n: p.copy() for n, p in paths.items()}
    i = np.searchsorted(days, pd.Timestamp(DAY))
    twisted["SPX"][i + 1:] *= 3.0
    fresh = Lake(tmp_path / "twist")
    fill_observed(fresh, days, twisted)
    derive_all(fresh, str(days[0].date()))
    assert market_at(fresh, "SPX", DAY).snapshot_id == market_at(lake, "SPX", DAY).snapshot_id
    assert market_at(fresh, "SPX", DAY).spot_scale == market_at(lake, "SPX", DAY).spot_scale
    later = days[i + 2].date()
    assert market_at(fresh, "SPX", later).spot_scale == pytest.approx(3 * market_at(lake, "SPX", later).spot_scale)


def test_as_of_reads_what_the_lake_knew(tmp_path):
    lake = Lake(tmp_path)
    days = pd.bdate_range("2020-01-01", "2020-01-10")
    fill_observed(lake, days)
    close = real_close(lake, "SPX", days[-1])[0]
    stamp = pd.Timestamp.now(tz="UTC").tz_localize(None).ceil("s") + pd.Timedelta(seconds=1)
    import time
    time.sleep(3.2)
    restated = pd.DataFrame({"instrument": "SPX", "obs_date": [days[-1]], "close": [close * 2]})
    assert lake.ingest("prices_eod", restated, "yahoo", "observed")["n_changed"] == 1
    assert real_close(lake, "SPX", days[-1])[0] == pytest.approx(2 * close)
    assert real_close(lake, "SPX", days[-1], as_of=stamp)[0] == pytest.approx(close)


def test_snapshot_is_in_the_model_scale_and_says_where_each_part_comes_from(world):
    root, lake, days, paths = world
    snap = market_at(lake, "SPX", DAY)
    assert snap.market["spot"] == 100.0
    i = np.searchsorted(days, pd.Timestamp(DAY))
    assert snap.spot_scale == pytest.approx(paths["SPX"][i] / 100.0)
    p = snap.provenance
    assert p["spot"]["origin"] == "observed" and p["discount"]["origin"] == "derived"
    assert p["surface"] == {"origin": "reconstructed", "layer": "reconstructed", "date": str(DAY), "extrapolated_from": 1.0}
    assert p["carry"]["method"] == "total_return"
    assert snap.source == "lake:reconstructed"
    restored = MarketSnapshot.from_json(snap.to_json())
    assert restored.snapshot_id == snap.snapshot_id


def test_forward_and_discount_follow_the_curves(world):
    root, lake, days, paths = world
    ctx, snap = context_at(lake, "SPX", DAY)
    q = snap.provenance["carry"]["dividend_yield"]
    for T in (0.25, 1.0, 3.0):
        assert ctx.market.F(T) * ctx.market.D(T) == pytest.approx(100.0 * np.exp(-q * T), rel=1e-6)
    assert ctx.valuation == DAY and ctx.underlying == "SPX" and ctx.t_max == 5.0


def test_real_fixings_go_to_the_model_scale(world):
    root, lake, days, paths = world
    snap = market_at(lake, "SPX", DAY)
    real = real_fixings(lake, "SPX", days[0], DAY)
    assert max(real) == str(DAY)
    model = to_model_scale({"SPX": real}, snap.spot_scale)["SPX"]
    assert model[str(DAY)] == pytest.approx(100.0)


def test_a_seasoned_product_prices_on_a_past_market_with_real_fixings(world):
    root, lake, days, paths = world
    strike = dt.date(2020, 1, 15)
    ctx, snap = context_at(lake, "SPX", DAY)
    fixings = to_model_scale({"SPX": real_fixings(lake, "SPX", strike, DAY)}, snap.spot_scale)
    sheet = preset("phoenix", **{"general.strike_date": strike, "wrapper.solve": "upfront", "coupon.rate": 0.08,
                                 "autocall.trigger": 2.0})  # never called on these paths: its value is still to come
    q = quote(sheet, ctx, model="lv", control=None, n_paths=4000, batch=4000, fixings=fixings)
    assert 0 < q.result.price < 200


def test_basket_context_at_a_date(world):
    root, lake, days, paths = world
    ctx, snaps, meta = basket_at(lake, ["SPX", "NDX"], DAY)
    assert ctx.valuation == DAY and ctx.corr.shape == (2, 2) and -1 < ctx.corr[0, 1] < 1
    assert meta["origin"] == "derived" and 0 <= meta["lambda"] <= 1
    assert snaps["NDX"].provenance["carry"]["origin"] == "synthetic"
    assert snaps["SPX"].spot_scale != snaps["NDX"].spot_scale
    q = quote(preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": ("SPX", "NDX"),
                                   "general.strike_date": DAY}), ctx, control=None, n_paths=4000, batch=4000)
    assert q.coupon_rate > 0


def test_no_market_without_data_at_the_date(world):
    root, lake, days, paths = world
    with pytest.raises(KeyError):
        market_at(lake, "SPX", dt.date(2018, 1, 2))
    with pytest.raises(KeyError):
        market_at(lake, "SPX", days[-1] + pd.Timedelta(days=30))


def test_a_calibrated_leverage_comes_with_the_snapshot_and_reprices(world):
    root, lake, days, paths = world
    ctx, snap = context_at(lake, "SPX", DAY, leverage="calibrate", T_max=0.5, n_particles=20_000)
    assert snap.provenance["leverage"] == {"origin": "derived", "mode": "calibrate"}
    assert len(snap.slv["leverage"]["times"]) > 5
    q = quote(preset("barrier_reverse_convertible", **{"general.maturity_months": 6, "general.strike_date": DAY}), ctx, model="slv",
              control=None, n_paths=4000, batch=4000)
    assert q.coupon_rate > 0
