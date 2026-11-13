"""The lake core, offline: parsers on small fixtures in the sources' real formats, idempotent and bitemporal loads."""

import datetime as dt
import time

import numpy as np
import pandas as pd
import pytest
from _paths import SPX_CALIBRATION

from optlab.marketdata.ingest import (
    load_cboe,
    load_ecb,
    load_fred,
    load_stoxx,
    load_treasury,
    load_universe,
    update_all,
)
from optlab.marketdata.lake import Lake
from optlab.marketdata.quality import check_prices, check_splits, check_vol_indices, run_report
from optlab.marketdata.sources import simple, yahoo


@pytest.fixture
def lake(tmp_path):
    return Lake(tmp_path / "lake")


def vol_rows(values=(20.0, 21.0)):
    return pd.DataFrame(
        {
            "index_name": "VIX",
            "obs_date": ["2020-03-02", "2020-03-03"][: len(values)],
            "open": np.nan,
            "high": np.nan,
            "low": np.nan,
            "close": values,
        }
    )


def test_the_lake_is_given_its_folder(tmp_path):
    """ADR-0012: the lake has no default folder (Settings.lake gives it, packages/server/tests/test_settings.py)."""
    import pytest

    with pytest.raises(TypeError):
        Lake()  # type: ignore[call-arg]
    assert Lake(tmp_path / "lake").root == tmp_path / "lake"


def test_loading_twice_adds_nothing(lake):
    first = lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    second = lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    assert (first["n_new"], first["n_changed"]) == (2, 0)
    assert (second["n_new"], second["n_changed"]) == (0, 0)
    assert len(lake.versions("vol_indices")) == 2
    assert len(lake.read("vol_indices")) == 2
    assert len(lake.read("ingestion_runs")) == 2  # both loads are recorded


def test_a_restated_value_keeps_the_old_version_readable(lake):
    lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    time.sleep(1.1)  # ingestion stamps have one second of resolution
    known_before = pd.Timestamp.now(tz="UTC").tz_localize(None)
    time.sleep(1.1)
    restated = lake.ingest("vol_indices", vol_rows((20.0, 25.0)), "cboe", "observed")
    assert (restated["n_new"], restated["n_changed"]) == (0, 1)
    now = lake.read("vol_indices", order="obs_date")
    assert now["close"].tolist() == [20.0, 25.0]
    past = lake.read("vol_indices", as_of=known_before, order="obs_date")
    assert past["close"].tolist() == [20.0, 21.0]
    assert len(lake.versions("vol_indices")) == 3


def test_a_row_absent_from_a_later_file_is_not_deleted(lake):
    lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    lake.ingest("vol_indices", vol_rows()[:1], "cboe", "observed")
    assert len(lake.read("vol_indices")) == 2


def test_sources_do_not_overwrite_each_other_and_origin_is_required(lake):
    lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    lake.ingest("vol_indices", vol_rows((30.0, 31.0)), "other", "derived")
    assert len(lake.read("vol_indices")) == 4
    assert set(lake.read("vol_indices")["origin"]) == {"observed", "derived"}
    with pytest.raises(ValueError, match="unknown origin"):
        lake.ingest("vol_indices", vol_rows(), "cboe", "guess")
    with pytest.raises(ValueError, match="rows without"):
        lake.ingest("vol_indices", vol_rows().drop(columns="index_name"), "cboe", "observed")


def test_curated_rows_carry_the_standard_columns_and_partition_by_year(lake):
    lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    row = lake.read("vol_indices").iloc[0]
    for col in ("obs_date", "ingested_at", "source", "run_id", "quality", "origin"):
        assert col in row.index and row[col] is not None
    assert list((lake.curated / "vol_indices").glob("year=2020/*.parquet"))


def test_partitioned_table_and_date_keys_stay_idempotent(lake):
    rows = pd.DataFrame(
        {
            "underlying": "SPX",
            "root": "SPXW",
            "expiry": ["2022-09-16", "2022-09-16"],
            "strike": [4000.0, 4100.0],
            "cp": "C",
            "obs_date": "2022-07-01",
            "bid": [1.0, 2.0],
            "ask": [1.5, 2.5],
            "settlement": "PM",
        }
    )
    assert lake.ingest("option_quotes", rows, "archive", "observed")["n_new"] == 2
    assert lake.ingest("option_quotes", rows, "archive", "observed")["n_new"] == 0
    got = lake.read("option_quotes", where="underlying = 'SPX'", order="strike")
    assert got["underlying"].tolist() == ["SPX", "SPX"] and got["bid"].tolist() == [1.0, 2.0]


def test_raw_files_are_stored_by_content_with_a_manifest(lake):
    a = lake.store_raw("cboe", "VIX.csv", b"abc", "http://x/VIX.csv")
    b = lake.store_raw("cboe", "VIX.csv", b"abc", "http://x/VIX.csv")
    c = lake.store_raw("cboe", "VIX.csv", b"abd", "http://x/VIX.csv")
    assert a.path == b.path != c.path
    assert a.path.read_bytes() == b"abc"
    assert [m["sha256"] for m in lake.manifest()] == [a.sha256, a.sha256, c.sha256]
    assert lake.manifest()[0]["url"] == "http://x/VIX.csv"


def test_duckdb_views_cover_the_tables(lake):
    lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    con = lake.connect()
    assert con.execute("SELECT count(*) FROM vol_indices").fetchone()[0] == 2
    cov = lake.coverage()
    assert {"vol_indices", "ingestion_runs"} <= set(cov["dataset"])


# --- parsers, on the real formats probed on 5 November 2026 ---
CBOE_OHLC = (
    "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17.240000,17.240000,17.240000,17.240000\n01/03/1990,18.19,18.19,18.19,18.19\n"
)
CBOE_ONE = "DATE,SKEW\n01/02/1990,126.090000\n01/03/1990,123.340000\n"
CBOE_VVIX = "DATE,VVIX\n03/15/2006,15.71\n06/01/2006,80.5\n"
STOXX = (
    "Date;Symbol;Indexvalue\n04.01.1999;V2TX;18.2033\n05.01.1999;V2TX;29.6912\n05.01.1999;OTHER;1\n06.01.1999;V2TX;\n"
)
FRED = "observation_date,DGS10\n2020-01-02,1.88\n2020-01-03,\n2020-01-06,1.81\n"
TREASURY = 'Date,"1 Mo","1.5 Month","2 Mo","1 Yr","30 Yr"\n11/04/2026,4.01,4.08,4.15,4.39,5.28\n11/03/2026,4.03,,4.15,4.40,5.32\n'
ECB = (
    "KEY,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "YC.B.U2,B,2026-10-28,2.9081346669,A\nYC.B.U2,B,2026-10-29,2.8810573843,A\n"
)


def test_cboe_parser_reads_both_layouts():
    ohlc = simple.parse_cboe(CBOE_OHLC, "VIX")
    assert ohlc["obs_date"].iloc[0] == dt.date(1990, 1, 2) and ohlc["close"].iloc[1] == 18.19
    one = simple.parse_cboe(CBOE_ONE, "SKEW")
    assert one["close"].tolist() == [126.09, 123.34] and one["open"].isna().all()


def test_cboe_parser_filters_the_suspect_first_vvix_rows():
    assert simple.parse_cboe(CBOE_VVIX, "VVIX")["close"].tolist() == [80.5]


def test_stoxx_parser_reads_day_first_dates_and_one_symbol():
    out = simple.parse_stoxx(STOXX)
    assert out["obs_date"].tolist() == [dt.date(1999, 1, 4), dt.date(1999, 1, 5)]
    assert out["close"].tolist() == [18.2033, 29.6912]


def test_fred_parser_drops_missing_values():
    out = simple.parse_fred(FRED, "DGS10")
    assert out["value"].tolist() == [1.88, 1.81]
    with pytest.raises(ValueError, match="columns"):
        simple.parse_fred(FRED, "DGS2")


def test_treasury_parser_goes_long_with_tenors_in_years():
    out = simple.parse_treasury(TREASURY)
    assert set(out["tenor"]) == {1 / 12, 1.5 / 12, 2 / 12, 1.0, 30.0}
    assert len(out) == 9  # one empty cell dropped
    row = out[(out["obs_date"] == dt.date(2026, 11, 4)) & (out["tenor"] == 30.0)]
    assert row["rate"].iloc[0] == 5.28


def test_ecb_parser_refuses_an_html_answer():
    assert simple.parse_ecb(ECB)["value"].iloc[1] == 2.8810573843
    with pytest.raises(ValueError, match="HTML"):
        simple.parse_ecb("<html lang='en'>")


def yahoo_frame():
    idx = pd.DatetimeIndex(["2026-11-03", "2026-11-04", "2026-11-05"], tz="Europe/Paris")
    return pd.DataFrame(
        {
            "Open": [10.0, 11.0, 12.0],
            "High": [11.0, 12.0, 13.0],
            "Low": [9.0, 10.0, 11.0],
            "Close": [10.5, 11.5, np.nan],
            "Adj Close": [10.4, 11.5, np.nan],
            "Volume": [1, 2, 3],
            "Dividends": [0.0, 0.5, 0.0],
            "Stock Splits": [0.0, 0.0, 20.0],
        },
        index=idx,
    )


def test_yahoo_parser_drops_the_nan_of_the_day_and_keeps_local_dates():
    prices, actions = yahoo.parse_history(yahoo_frame(), "SX5E")
    assert prices["obs_date"].tolist() == [dt.date(2026, 11, 3), dt.date(2026, 11, 4)]
    assert prices["adj_close"].tolist() == [10.4, 11.5]
    assert actions.set_index("kind")["value"].to_dict() == {"dividend": 0.5, "split": 20.0}


def test_yahoo_chain_parser():
    raw = pd.DataFrame(
        {
            "contractSymbol": ["SPXW261120C05000000", "SPX261218P05000000"],
            "strike": [5000.0, 5000.0],
            "bid": [1.0, 2.0],
            "ask": [1.5, 2.5],
            "volume": [1, 2],
            "openInterest": [3, 4],
            "impliedVolatility": [0.2, 0.3],
            "type": ["call", "put"],
            "expiry": ["2026-11-20", "2026-12-18"],
        }
    )
    out = yahoo.parse_chain(raw, "SPX", dt.date(2026, 11, 4), 5100.0)
    assert out["root"].tolist() == ["SPXW", "SPX"] and out["cp"].tolist() == ["C", "P"]
    assert out["settlement"].tolist() == ["PM", "AM"]


# --- loads, with a fake network ---
def fake_fetch(url, headers=None):
    if "cboe" in url:
        return (CBOE_ONE if "SKEW" in url else CBOE_OHLC).encode()
    if "stoxx" in url:
        return STOXX.encode()
    if "fred" in url:
        series = url.split("id=")[1].split("&")[0]
        return FRED.replace("DGS10", series).encode()
    if "treasury" in url:
        return TREASURY.encode()
    return ECB.encode()


@pytest.mark.slow
def test_loads_are_idempotent_end_to_end(lake):
    def run():
        out = load_cboe(lake, fake_fetch) + load_stoxx(lake, fake_fetch) + load_fred(lake, fake_fetch, lambda: None)
        out += load_treasury(lake, fake_fetch, 2026, 2026) + load_ecb(lake, fake_fetch) + [load_universe(lake)]
        return out

    assert sum(r["n_new"] for r in run()) > 0
    assert sum(r["n_new"] + r["n_changed"] for r in run()) == 0
    assert set(lake.read("rate_points")["curve"]) >= {"USD.TREASURY", "EUR.AAA", "USD.SOFR"}
    assert lake.read("correl_implied", where="index_name = 'COR3M'")["correlation"].max() == pytest.approx(0.1819)
    assert "SPX" in set(lake.read("instruments")["instrument"])


def test_a_failing_source_is_reported_and_does_not_stop_the_load(lake, monkeypatch):
    from optlab.marketdata import ingest

    def down(_lake):
        raise ConnectionError("no route")

    monkeypatch.setitem(ingest.STEPS, "stoxx", down)
    monkeypatch.setitem(ingest.STEPS, "universe", load_universe)
    report = update_all(lake, only=("stoxx", "universe"))
    assert report["stoxx"]["ok"] is False and "no route" in report["stoxx"]["error"]
    assert report["universe"]["ok"] is True
    assert lake.read("quality_issues")["check_name"].tolist() == ["load_failed"]


# --- quality ---
def test_quality_flags_jumps_gaps_and_stale_series(lake):
    prices = pd.DataFrame(
        {
            "instrument": "X",
            "obs_date": ["2020-01-02", "2020-01-03", "2020-02-03"],
            "high": [100.0, 150.0, 151.0],
            "low": [99.0, 100.0, 149.0],
            "close": [100.0, 150.0, 150.0],
        }
    )
    checks = {i["check_name"] for i in check_prices(prices, dt.date(2026, 1, 1))}
    assert checks == {"large_move", "gap", "stale"}
    vols = pd.DataFrame({"index_name": "VXAPL", "obs_date": ["2020-01-02"], "close": [500.0]})
    assert {i["check_name"] for i in check_vol_indices(vols, dt.date(2026, 1, 1))} == {"out_of_range", "stale"}


def test_the_quality_report_is_stored(lake):
    lake.ingest("vol_indices", vol_rows(), "cboe", "observed")
    issues = run_report(lake, dt.date(2026, 1, 1))
    assert issues["check_name"].tolist() == ["stale"]
    assert lake.read("quality_issues")["item"].tolist() == ["VIX"]


def test_split_check_passes_restated_closes_and_flags_raw_ones():
    actions = pd.DataFrame({"instrument": ["X"], "kind": ["split"], "value": [20.0], "obs_date": ["2022-07-18"]})
    days = ["2022-07-15", "2022-07-18", "2022-07-19"]
    restated = pd.DataFrame({"instrument": "X", "obs_date": days, "close": [111.0, 113.0, 115.0]})
    raw = pd.DataFrame({"instrument": "X", "obs_date": days, "close": [2220.0, 113.0, 115.0]})
    assert check_splits(restated, actions) == []
    assert [i["check_name"] for i in check_splits(raw, actions)] == ["split_not_adjusted"]


def test_term_sheet_underlyings_come_from_the_registry_and_need_their_own_market():
    from optlab.core.instruments import enabled_names, instrument
    from optlab.structured import PricingContext, TermSheet, quote
    from optlab.structured.schema import SECTIONS

    field = next(f for f in SECTIONS["general"].fields if f.name == "underlying")
    assert field.choices == enabled_names() and "NDX" in field.choices and "NVDA" not in field.choices
    assert instrument("NVDA").enabled is False
    sheet = TermSheet.from_dict({"type": "autocall", "general": {"underlying": "NDX"}})
    with pytest.raises(ValueError, match="the market in use is SPX"):
        quote(sheet, PricingContext.from_calibration(SPX_CALIBRATION), n_paths=2000)


def test_rule_calendars_know_the_special_closures_and_eurex():
    from optlab.structured.calendars import CALENDARS

    assert not CALENDARS["nyse"].is_business_day(dt.date(2025, 1, 9))
    assert not CALENDARS["nyse"].is_business_day(dt.date(2018, 12, 5))
    eurex = CALENDARS["eurex"]
    assert not eurex.is_business_day(dt.date(2020, 4, 13))  # Easter Monday
    assert not eurex.is_business_day(dt.date(2020, 5, 1))
    assert eurex.is_business_day(dt.date(2020, 7, 3))  # a US holiday
