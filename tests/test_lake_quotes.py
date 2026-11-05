"""Option quotes: every source's format lands in the same rows and goes through the same cleaning chain."""

import datetime as dt
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from optlab.blackscholes import black_price
from optlab.data import build_iv_table
from optlab.marketdata.derive.curves import EUR_CURVE, USD_CURVE
from optlab.marketdata.lake import Lake
from optlab.marketdata.quotes import as_clean_input, clean_day, discount_function, quote_days
from optlab.marketdata.sources import dolthub, yahoo
from optlab.marketdata.sources.archives import (import_historicaldata, parse_historicaldata, parse_optionsdx,
                                                snapshot_time)

DAY = dt.date(2022, 9, 15)
RATE, CARRY, SPOT = 0.03, 0.015, 4000.0


def smile(k, T):
    return 0.22 - 0.25 * k + 0.5 * k * k + 0.01 * T


def synthetic_rows(expiries=tuple(DAY + dt.timedelta(days=d) for d in (30, 60, 120, 240, 400)), half_spread=0.002):
    """Quotes from a known smile, on known curves: the cleaning should find them again. A relative half spread keeps
    the deep out-of-the-money quotes two-sided, and a price floor keeps a small tick."""
    rows = []
    T0 = snapshot_time(DAY)
    for expiry in expiries:
        close = dt.datetime.combine(expiry, dt.time(16, 0), tzinfo=T0.tzinfo)
        T = (close - T0).total_seconds() / (365 * 86400)
        F, D = SPOT * np.exp(CARRY * T), np.exp(-RATE * T)
        for K in np.arange(2800.0, 5201.0, 50.0):
            vol = smile(np.log(K / F), T)
            for cp in ("C", "P"):
                price = float(black_price(F, K, T, vol, D, "call" if cp == "C" else "put"))
                rows.append({"underlying": "SPX", "root": "SPXW", "expiry": expiry, "strike": K, "cp": cp, "obs_date": DAY,
                             "bid": price * (1 - half_spread), "ask": price * (1 + half_spread),
                             "underlying_close": SPOT, "settlement": "PM"})
    return pd.DataFrame(rows)


def curve_rows():
    return pd.DataFrame({"curve": USD_CURVE, "obs_date": DAY, "tenor": [1 / 365, 1.0, 5.0], "zero_rate": RATE,
                         "discount": np.nan})


@pytest.fixture
def lake(tmp_path):
    lake = Lake(tmp_path / "lake")
    lake.ingest("discount_curves", curve_rows(), "test", "derived")
    return lake


def test_cleaning_finds_the_smile_and_the_forward_back(lake):
    lake.ingest("option_quotes", synthetic_rows(), "archive", "observed")
    day = clean_day(lake, "SPX", DAY)
    assert len(day.slices) == 5 and day.source == "archive"
    for s in day.slices:
        assert s["F"] == pytest.approx(SPOT * np.exp(CARRY * s["T"]), rel=1e-6)
        assert s["D"] == pytest.approx(np.exp(-RATE * s["T"]), rel=1e-10)
        assert np.abs(s["iv"] - smile(s["k"], s["T"])).max() < 1e-4


def test_spxw_is_preferred_to_monthly_on_a_shared_expiry_and_am_monthlies_stay_elsewhere(lake):
    rows = synthetic_rows()
    monthly = rows.assign(root="SPX", settlement="AM", bid=rows["bid"] * 2, ask=rows["ask"] * 2)
    first = rows["expiry"].min()
    only_monthly = monthly[monthly["expiry"] != first]
    # On the first expiry both exist: the doubled monthly quotes must be ignored. Elsewhere only SPXW exists here.
    lake.ingest("option_quotes", pd.concat([rows, monthly[monthly["expiry"] == first]]), "archive", "observed")
    s = clean_day(lake, "SPX", DAY).slices[0]
    assert np.abs(s["iv"] - smile(s["k"], s["T"])).max() < 1e-4
    assert len(only_monthly) > 0


def test_one_sided_quotes_and_wide_spreads_are_removed(lake):
    rows = synthetic_rows()
    far = rows["strike"] == 3000.0
    rows.loc[far, "bid"] = 0.0
    wide = rows["strike"] == 3500.0
    rows.loc[wide, "ask"] = rows.loc[wide, "bid"] * 3
    lake.ingest("option_quotes", rows, "archive", "observed")
    table = clean_day(lake, "SPX", DAY).table
    assert not set(table["strike"]) & {3000.0, 3500.0}
    assert (table["bid"] > 0).all() and ((table["ask"] - table["bid"]) / table["mid"] < 0.5).all()


def test_a_day_without_quotes_is_an_error(lake):
    with pytest.raises(KeyError, match="no quotes of SPX"):
        clean_day(lake, "SPX", dt.date(2022, 1, 3))


def test_discount_function_takes_the_last_curve_within_ten_days(lake):
    disc = discount_function(lake, "USD", DAY + dt.timedelta(days=3))
    assert float(disc(2.0)) == pytest.approx(np.exp(-RATE * 2.0))
    with pytest.raises(KeyError):
        discount_function(lake, "USD", DAY + dt.timedelta(days=30))
    with pytest.raises(KeyError):
        discount_function(lake, "EUR", DAY)
    assert EUR_CURVE == "EUR.DISC"


# --- each source's format, on rows from the same smile ---
def historicaldata_csv(rows):
    out = rows.assign(contract="X", underlying=rows["root"], expiration=rows["expiry"], type=np.where(rows["cp"] == "C", "call", "put"),
                      quote_date=rows["obs_date"], volume=1, open_interest=2, settlement_time="PM", iv=0.2, delta=0.5,
                      gamma=0.0, theta=0.0, vega=0.0, style="E")
    return out[["contract", "underlying", "expiration", "type", "strike", "style", "quote_date", "bid", "ask", "volume",
                "open_interest", "underlying_close", "settlement_time", "iv", "delta", "gamma", "theta", "vega"]].to_csv(index=False)


def yahoo_frame(rows):
    symbols = "SPXW" + pd.to_datetime(rows["expiry"]).dt.strftime("%y%m%d") + rows["cp"] + "0"
    return pd.DataFrame({"contractSymbol": symbols.to_numpy(), "strike": rows["strike"].to_numpy(), "bid": rows["bid"].to_numpy(),
                         "ask": rows["ask"].to_numpy(), "volume": 1, "openInterest": 2, "impliedVolatility": 0.2,
                         "type": np.where(rows["cp"] == "C", "call", "put"),
                         "expiry": pd.to_datetime(rows["expiry"]).dt.strftime("%Y-%m-%d").to_numpy()})


def test_archive_and_yahoo_days_go_through_the_same_chain_to_the_same_slices(lake):
    rows = synthetic_rows()
    lake.ingest("option_quotes", parse_historicaldata(historicaldata_csv(rows)), "historicaldata", "observed")
    lake.ingest("option_quotes", yahoo.parse_chain(yahoo_frame(rows), "SPX", DAY, SPOT), "yahoo", "observed")
    archive, live = clean_day(lake, "SPX", DAY, "historicaldata"), clean_day(lake, "SPX", DAY, "yahoo")
    assert len(archive.slices) == len(live.slices) == 5
    for a, b in zip(archive.slices, live.slices):
        assert a["T"] == b["T"] and a["F"] == b["F"]
        assert (a["iv"] == b["iv"]).all()
    assert quote_days(lake, "SPX", "yahoo") == [DAY]


def test_the_file_path_of_build_iv_table_gives_the_same_vols_with_parity_rates(tmp_path):
    rows = synthetic_rows()
    frame = yahoo_frame(rows).assign(spot=SPOT, snapshot=snapshot_time(DAY).isoformat(), lastPrice=0.0, lastTradeDate="")
    path = tmp_path / "spx.csv"
    frame.to_csv(path, index=False)
    market, slices, table, meta = build_iv_table(path)
    assert len(slices) == 5 and meta["zero_rate_curve"]["n_maturities"] >= 3
    for s in slices:
        assert s["D"] == pytest.approx(np.exp(-RATE * s["T"]), abs=2e-3)
        assert np.abs(s["iv"] - smile(s["k"], s["T"])).max() < 5e-3


def test_sample_zip_import_is_idempotent(tmp_path):
    lake = Lake(tmp_path / "lake")
    rows = synthetic_rows()[:40]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("day_by_date/2022-09-15_options.csv", historicaldata_csv(rows))
        z.writestr("README.md", "ignored")
    path = tmp_path / "s.zip"
    path.write_bytes(buf.getvalue())
    assert import_historicaldata(lake, path)[0]["n_new"] == 40
    assert import_historicaldata(lake, path)[0]["n_new"] == 0
    assert set(lake.read("option_quotes")["underlying"]) == {"SPX"}


def test_historicaldata_parser_keeps_a_missing_side_and_maps_spxw():
    rows = synthetic_rows()[:2]
    rows.loc[rows.index[0], "bid"] = np.nan
    out = parse_historicaldata(historicaldata_csv(rows))
    assert np.isnan(out["bid"].iloc[0]) and out["underlying"].tolist() == ["SPX", "SPX"] and out["root"].iloc[0] == "SPXW"


DOLT_CHAIN = ("date,act_symbol,expiration,strike,call_put,bid,ask,vol,delta,gamma,theta,vega,rho\n"
              "2024-06-03,SPY,2024-06-17,470.00,Call,58.87,59.16,0.2984,0.9797,0.0015,-0.1214,0.0504,0.1757\n"
              "2024-06-03,SPY,2024-06-17,470.00,Put,0.08,0.09,0.2589,-0.0092,0.0009,-0.0231,0.0258,-0.0019\n"
              "2024-06-03,ZZZ,2024-06-17,470.00,Put,0.08,0.09,0.2589,-0.0092,0.0009,-0.0231,0.0258,-0.0019\n")
DOLT_VOL = "date,act_symbol,iv_current\n2024-06-03,AAPL,0.2127\n2024-06-04,AAPL,\n"
OPTIONSDX = (" [QUOTE_DATE], [EXPIRE_DATE], [UNDERLYING_LAST], [STRIKE], [C_BID], [C_ASK], [C_IV], [C_DELTA], [C_VOLUME], "
             "[P_BID], [P_ASK], [P_IV], [P_DELTA], [P_VOLUME]\n"
             " 2020-03-16, 2020-04-17, 2386.13, 2400.0, 120.0, 125.0, 0.8, 0.5, 10, 130.0, 135.0, 0.82, -0.5, \n")


def test_dolthub_parsers():
    chain = dolthub.parse_chain(DOLT_CHAIN, ("SPY",))
    assert chain["cp"].tolist() == ["C", "P"] and chain["iv"].iloc[0] == 0.2984
    vols = dolthub.parse_vol_history(DOLT_VOL)
    assert vols["index_name"].tolist() == ["DOLT.AAPL"] and vols["close"].iloc[0] == pytest.approx(21.27)


def test_optionsdx_parser_splits_the_two_sides():
    out = parse_optionsdx(OPTIONSDX, "SPX")
    assert out["cp"].tolist() == ["C", "P"] and out["bid"].tolist() == [120.0, 130.0]
    assert out["obs_date"].iloc[0] == dt.date(2020, 3, 16) and out["underlying_close"].iloc[0] == 2386.13


def test_clean_input_columns():
    out = as_clean_input(synthetic_rows()[:2])
    assert set(out.columns) == {"root", "strike", "bid", "ask", "type", "expiry"} and out["type"].tolist() == ["call", "put"]
