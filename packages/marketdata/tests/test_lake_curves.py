"""Curves and carry: references are closed forms (a par bond prices to par, parity holds to 1e-8 on synthetic data)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from optlab.core.blackscholes import black_price
from optlab.core.instruments import instrument
from optlab.marketdata.derive.bank import ENTITY, BankParams, bank_spreads, build_bank, spread_at, wrapper_spreads
from optlab.marketdata.derive.carry import (
    build_carry,
    cip_forward,
    dividend_stream,
    dividend_yield_at,
    equity_forward,
    parity_carry_rows,
    parity_forward_discount,
    realised_yield,
    trailing_dividend_yield,
)
from optlab.marketdata.derive.curves import (
    EUR_CURVE,
    USD_CURVE,
    bootstrap_par,
    build_discount_curves,
    par_curve_rows,
    zero_curve_rows,
)
from optlab.marketdata.lake import Lake


def test_bootstrap_reprices_par_bonds_to_par():
    tenors = np.array([0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0])
    par = np.array([0.04, 0.041, 0.042, 0.043, 0.044, 0.045, 0.047])
    t, z = bootstrap_par(tenors, par)
    assert t.tolist() == tenors.tolist()
    for i in range(2, len(t)):
        grid = np.arange(0.5, t[i] + 1e-9, 0.5)
        zero = np.interp(grid, *bootstrap_par(np.append(tenors, grid), np.append(par, np.interp(grid, tenors, par))))
        price = par[i] / 2 * np.exp(-zero * grid).sum() + np.exp(-zero[-1] * grid[-1])
        assert price == pytest.approx(1.0, abs=1e-10)


def test_flat_par_curve_gives_the_equivalent_continuous_rate():
    t, z = bootstrap_par([1.0, 2.0, 5.0], [0.05, 0.05, 0.05])
    assert z == pytest.approx(2 * np.log(1.025), abs=1e-12)


def test_curve_rows_put_the_overnight_rate_first_and_are_consistent():
    treasury = pd.DataFrame({"obs_date": ["2020-03-02"] * 3, "tenor": [0.25, 1.0, 2.0], "rate": [1.0, 1.1, 1.2]})
    overnight = pd.DataFrame({"obs_date": [dt.date(2020, 3, 2)], "rate": [1.5]})
    usd = par_curve_rows(treasury, overnight, USD_CURVE)
    assert usd["curve"].iloc[0] == USD_CURVE and usd["tenor"].iloc[0] == pytest.approx(1 / 365)
    assert usd["discount"].to_numpy() == pytest.approx(np.exp(-usd["zero_rate"].to_numpy() * usd["tenor"].to_numpy()))
    aaa = pd.DataFrame({"obs_date": ["2020-03-02"] * 2, "tenor": [1.0, 2.0], "rate": [-0.5, -0.4]})
    eur = zero_curve_rows(aaa, overnight.iloc[:0], EUR_CURVE)
    assert eur["curve"].iloc[0] == EUR_CURVE and eur["zero_rate"].iloc[0] == pytest.approx(-0.005)


def test_covered_parity_is_exact():
    D_usd, D_eur = np.exp(-0.05 * 2.0), np.exp(-0.02 * 2.0)
    assert cip_forward(1.10, D_usd, D_eur) == pytest.approx(1.10 * np.exp(0.03 * 2.0), abs=1e-12)


def test_equity_forward_with_repo_and_a_dividend():
    r, repo = 0.04, 0.005
    disc = lambda t: np.exp(-r * np.asarray(t))  # noqa: E731
    F = equity_forward(100.0, [0.25, 1.0], disc, repo, dividends=[(0.5, 2.0)])
    assert F[0] == pytest.approx(100 * np.exp((r - repo) * 0.25), abs=1e-10)
    assert F[1] == pytest.approx(100 * np.exp(r - repo) - 2.0 * np.exp((r - repo) * 0.5), abs=1e-10)


def synthetic_chain(F=102.0, D=0.97, T=0.5, sigma=0.2):
    K = np.arange(80.0, 121.0, 2.0)
    calls = black_price(F, K, T, sigma, D, "call")
    puts = black_price(F, K, T, sigma, D, "put")
    return K, calls, puts


def test_put_call_parity_recovers_forward_and_discount_to_1e_8():
    K, calls, puts = synthetic_chain()
    D, F = parity_forward_discount(K, calls, puts)
    assert D == pytest.approx(0.97, abs=1e-8) and F == pytest.approx(102.0, abs=1e-8)


def test_parity_carry_from_a_chain_with_the_curve_given():
    T, D, F, S = 146 / 365, 0.98, 102.0, 100.0
    K, calls, puts = synthetic_chain(F, D, T)
    day = dt.date(2022, 7, 1)
    expiry = day + dt.timedelta(days=146)
    q = pd.concat(
        [
            pd.DataFrame(
                {"obs_date": day, "expiry": expiry, "strike": K, "cp": "C", "bid": calls - 0.01, "ask": calls + 0.01}
            ),
            pd.DataFrame(
                {"obs_date": day, "expiry": expiry, "strike": K, "cp": "P", "bid": puts - 0.01, "ask": puts + 0.01}
            ),
        ]
    )
    rows = parity_carry_rows(q, lambda t: D, S)
    assert rows["carry"].iloc[0] == pytest.approx(np.log(F / S) / T, abs=1e-8)
    assert rows["forward_ratio"].iloc[0] == pytest.approx(F / S, abs=1e-8)


def test_realised_yield_reads_a_steady_dividend_and_only_the_past():
    days = pd.bdate_range("2019-01-01", "2021-12-31")
    t = (days - days[0]).days / 365.0
    price = pd.Series(100 * np.exp(0.05 * t), index=days)
    tr = pd.Series(100 * np.exp(0.07 * t), index=days)
    y = realised_yield(price, tr)
    assert y.iloc[-1] == pytest.approx(0.02, abs=1e-6) and y.index[0] >= days[0] + pd.Timedelta(days=364)
    cut = realised_yield(price[:400], tr[:400])
    assert cut.iloc[-1] == pytest.approx(y.loc[cut.index[-1]], abs=1e-12)  # later days change nothing


def test_trailing_dividends_are_a_rolling_year():
    days = pd.bdate_range("2020-01-01", "2021-06-30")
    price = pd.Series(100.0, index=days)
    div = pd.DataFrame({"obs_date": ["2020-03-02", "2020-06-01", "2021-03-01"], "value": [1.0, 1.0, 2.0]})
    y = trailing_dividend_yield(div, price)
    assert y.loc["2020-02-28"] == 0.0 and y.loc["2020-07-01"] == pytest.approx(0.02)
    assert y.loc["2021-03-01"] == pytest.approx(0.04)  # the March 2020 one is still in the 365-day window
    assert y.loc["2021-03-02"] == pytest.approx(0.03)
    assert y.loc["2021-06-30"] == pytest.approx(0.02)


def credit():
    days = pd.bdate_range("2015-01-01", "2020-12-31")
    return pd.Series(0.02 + 0.01 * np.sin(np.arange(len(days)) / 100), index=days)


def test_bank_spreads_are_reproducible_and_never_change_the_past():
    a = bank_spreads(credit())
    b = bank_spreads(credit())
    pd.testing.assert_frame_equal(a, b)
    shorter = bank_spreads(credit()[:1000])
    merged = shorter.merge(a, on=["obs_date", "tenor"], suffixes=("_s", "_a"))
    assert len(merged) == len(shorter)
    assert (merged["spread_s"].to_numpy() == merged["spread_a"].to_numpy()).all()


def test_bank_spreads_have_the_term_structure_beta_and_persistence_asked():
    p = BankParams(sigma=0.0)
    wide = credit() + 0.01  # away from the floor
    s = bank_spreads(wide, p)
    one = s[s["tenor"] == 1.0].set_index("obs_date")["spread"]
    five = s[s["tenor"] == 5.0].set_index("obs_date")["spread"]
    assert np.allclose((five - one).to_numpy(), 0.004, atol=1e-12)
    assert np.allclose(one.to_numpy(), p.premium[1.0] + p.beta * (wide.to_numpy() - p.credit_reference), atol=1e-12)
    noisy = bank_spreads(credit(), BankParams(beta=0.0))
    x = noisy[noisy["tenor"] == 7.0]["spread"].to_numpy() - 0.0085
    assert np.corrcoef(x[:-1], x[1:])[0, 1] == pytest.approx(0.98, abs=0.02)


def test_derived_tables_load_with_their_origin_and_plug_into_the_wrapper(tmp_path):
    lake = Lake(tmp_path)
    days = pd.bdate_range("2020-01-01", "2020-03-31")
    baa = pd.DataFrame({"obs_date": days, "rate": 4.0, "curve": "USD.BAA", "tenor": 20.0})
    t10 = pd.DataFrame({"obs_date": days, "rate": 2.0, "curve": "USD.CMT10", "tenor": 10.0})
    lake.ingest("rate_points", pd.concat([baa, t10]), "fred", "observed")
    build_bank(lake, BankParams(start="2020-01-01", sigma=0.0))
    assert set(lake.read("credit_spreads")["origin"]) == {"synthetic"}
    expected = 0.0050 + 0.35 * (0.02 - 0.019)
    assert spread_at(lake, "2020-03-15", 3.0) == pytest.approx(expected, abs=1e-12)  # a Sunday: Friday's level
    assert spread_at(lake, "2020-03-15", 4.0) == pytest.approx(0.0060 + 0.35 * 0.001, abs=1e-12)
    wrapper = wrapper_spreads(lake, "2020-03-13", 3.0)
    assert wrapper["wrapper.issuer_spread"] == pytest.approx(expected, abs=1e-12)
    assert lake.read("credit_spreads")["entity"].iloc[0] == ENTITY
    with pytest.raises(KeyError):
        spread_at(lake, "2021-01-01", 1.0)


def test_build_curves_and_carry_on_the_lake(tmp_path):
    lake = Lake(tmp_path)
    days = pd.bdate_range("2019-01-01", "2020-12-31")
    points = [
        pd.DataFrame({"obs_date": days, "rate": r, "curve": "USD.TREASURY", "tenor": t})
        for t, r in ((0.25, 1.0), (1.0, 1.1), (5.0, 1.5))
    ]
    points.append(pd.DataFrame({"obs_date": days, "rate": 1.0, "curve": "USD.FF", "tenor": 1 / 365}))
    points.append(pd.DataFrame({"obs_date": days, "rate": 0.5, "curve": "EUR.AAA", "tenor": 1.0}))
    points.append(pd.DataFrame({"obs_date": days, "rate": 0.5, "curve": "EUR.ESTR", "tenor": 1 / 365}))
    lake.ingest("rate_points", pd.concat(points), "test", "observed")
    results = build_discount_curves(lake, "2019-01-01")
    assert all(r["n_new"] > 0 for r in results)
    assert sum(r["n_new"] for r in build_discount_curves(lake, "2019-01-01")) == 0
    assert set(lake.read("discount_curves")["origin"]) == {"derived"}
    t = (days - days[0]).days / 365.0
    prices = [
        pd.DataFrame({"instrument": n, "obs_date": days, "close": 100 * np.exp(g * t)})
        for n, g in (("SPX", 0.05), ("SPX.TR", 0.07), ("NDX", 0.05), ("AAPL", 0.0))
    ]
    lake.ingest("prices_eod", pd.concat(prices), "yahoo", "observed")
    lake.ingest(
        "corporate_actions",
        pd.DataFrame({"instrument": "AAPL", "kind": "dividend", "obs_date": ["2020-06-01"], "value": [1.0]}),
        "yahoo",
        "observed",
    )
    build_carry(lake, [instrument("SPX"), instrument("NDX"), instrument("AAPL")])
    assert dividend_yield_at(lake, "SPX", dt.date(2020, 6, 1))[0] == pytest.approx(0.02, abs=1e-3)
    assert dividend_yield_at(lake, "SPX", dt.date(2020, 6, 1))[1:] == ("derived", "total_return")
    assert dividend_yield_at(lake, "NDX", dt.date(2020, 6, 1))[1:] == ("synthetic", "assumed_yield")
    assert dividend_yield_at(lake, "AAPL", dt.date(2020, 7, 1)) == (
        pytest.approx(0.01),
        "derived",
        "trailing_dividends",
    )
    assert dividend_yield_at(lake, "AAPL", dt.date(2020, 5, 1))[0] == 0.0  # the June dividend is not known in May


def _lake_with_prices(tmp_path, name, days, levels):
    lake = Lake(tmp_path)
    lake.ingest(
        "prices_eod", pd.DataFrame({"instrument": name, "obs_date": days, "close": levels}), "yahoo", "observed"
    )
    return lake


def test_a_shares_stream_repeats_last_years_ex_dates_as_shares_of_todays_spot(tmp_path):
    days = pd.bdate_range("2019-01-01", "2021-06-30")
    lake = _lake_with_prices(tmp_path, "AAPL", days, 100.0)
    ex = pd.to_datetime(["2020-03-16", "2020-06-15", "2020-09-14", "2020-12-14", "2021-03-15", "2021-09-13"])
    div = pd.DataFrame({"instrument": "AAPL", "kind": "dividend", "obs_date": ex, "value": 0.5})
    lake.ingest("corporate_actions", div, "yahoo", "observed")
    day = dt.date(2021, 4, 1)
    stream = dividend_stream(lake, "AAPL", day, 50.0)
    assert stream.method == "dividend_dates" and stream.origin == "derived"
    assert stream.annual_yield == pytest.approx(
        4 * 0.01
    )  # 0.5 on a spot of 50, from the four dividends known at that date
    first = np.sort(stream.times)[0]
    assert first == pytest.approx(((pd.Timestamp("2020-06-15") - pd.Timestamp(day)).days + 365.25) / 365)
    assert stream.factor(first - 1e-6)[0] == 1.0 and stream.factor(first)[0] == pytest.approx(0.99)
    # the dividend of September 2021 is not known in April: nothing falls near 13 September
    assert not np.any(np.abs(stream.times - ((pd.Timestamp("2021-09-13") - pd.Timestamp(day)).days / 365)) < 1e-9)
    assert dividend_stream(lake, "AAPL", dt.date(2019, 6, 1), 50.0).annual_yield == 0.0


def test_a_fall_of_the_spot_raises_the_yield_at_once(tmp_path):
    days = pd.bdate_range("2018-06-01", "2020-03-23")
    level = pd.Series(100.0, index=days)
    tr = 100.0 * np.exp(0.02 * (days - days[0]).days / 365)
    lake = Lake(tmp_path)
    lake.ingest(
        "prices_eod",
        pd.concat(
            [
                pd.DataFrame({"instrument": "SPX", "obs_date": days, "close": level.to_numpy()}),
                pd.DataFrame({"instrument": "SPX.TR", "obs_date": days, "close": tr}),
            ]
        ),
        "yahoo",
        "observed",
    )
    day = days[-1].date()
    assert dividend_stream(lake, "SPX", day, 100.0).annual_yield == pytest.approx(0.02, rel=0.02)
    assert dividend_stream(lake, "SPX", day, 70.0).annual_yield == pytest.approx(0.02 / 0.7, rel=0.02)


def test_an_assumed_yield_keeps_its_total_and_takes_its_season(tmp_path):
    days = pd.bdate_range("2019-01-01", "2020-12-31")
    lake = _lake_with_prices(tmp_path, "SX5E", days, 100.0)
    day = dt.date(2020, 1, 15)
    stream = dividend_stream(lake, "SX5E", day, 100.0)
    assert stream.origin == "synthetic" and stream.method == "assumed_season"
    assert stream.shares[stream.times <= 1.001].sum() == pytest.approx(0.03, abs=2e-9)
    spring = stream.factor(0.5)[0] / stream.factor(0.2)[0]  # end of March to middle of July
    winter = stream.factor(1.0)[0] / stream.factor(0.75)[0]
    assert 1 - spring > 5 * (1 - winter)


def test_a_forward_has_its_steps_and_no_dividend_before_the_ex_date(tmp_path):
    from optlab.core.market import Market
    from optlab.marketdata.derive.carry import DividendStream
    from optlab.marketdata.pit import ONE_DAY, curve_pillars

    stream = DividendStream(np.array([0.4, 1.4]), np.array([0.02, 0.02]), "derived", "dividend_dates")
    pillars = curve_pillars(stream)
    assert {0.4 - ONE_DAY, 0.4, 1.4} <= set(pillars.tolist())
    m = Market(100.0, pillars, np.ones_like(pillars), 100.0 * stream.factor(pillars))
    assert m.F(0.4 - ONE_DAY) == pytest.approx(100.0)
    assert m.F(0.4) == pytest.approx(98.0)
    assert m.F(1.39) == pytest.approx(98.0, abs=1e-9)
    assert m.F(5.0) == pytest.approx(100.0 * 0.98**2)


def test_every_dividend_of_a_stream_is_a_step_on_its_own_date():
    from optlab.core.market import Market
    from optlab.marketdata.derive.carry import DividendStream
    from optlab.marketdata.pit import ONE_DAY, curve_pillars

    times = np.array([0.1075342465, 0.3595890410958904, 0.6143835616438356, 0.8554794520547945, 1.3603])
    stream = DividendStream(times, np.full(5, 0.01), "derived", "dividend_dates")
    pillars = curve_pillars(stream)
    m = Market(100.0, pillars, np.ones_like(pillars), 100.0 * stream.factor(pillars))
    for t in times:
        assert m.F(t) / m.F(t - ONE_DAY) == pytest.approx(0.99, abs=1e-9)
