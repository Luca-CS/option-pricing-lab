"""Yen and sterling curves: the parsers on small fixtures in each publisher's format, the loads without network, the forwards of USDJPY
and GBPUSD against covered parity, and the quanto market paid in those currencies."""

import datetime as dt
import io
import time
import zipfile

import numpy as np
import pandas as pd
import pytest
from _lake_fixture import synthetic_lake
from optlab.marketdata.derive.carry import cip_forward
from optlab.marketdata.derive.curves import (
    GBP_CURVE,
    JPY_CURVE,
    bootstrap_par,
    build_discount_curves,
    par_curve_rows,
    zero_curve_rows,
)
from optlab.marketdata.derive.fx import build_fx_correl, build_fx_forwards, build_fx_vols, quanto_link_at, spot_series
from optlab.marketdata.ingest import load_boe, load_boj, load_mof
from optlab.marketdata.pit import basket_at, market_at
from optlab.marketdata.quality import RESOLVED, open_issues
from optlab.marketdata.quotes import discount_function
from optlab.marketdata.sources import rates
from optlab.marketdata.sources.rates import (
    glc_workbooks,
    parse_boe_series,
    parse_boj_call,
    parse_glc_workbook,
    parse_mof_jgb,
)
from optlab.structured import preset
from optlab.structured.context import PricingContext
from optlab.structured.schema import TermSheetError

DAY = dt.date(2020, 6, 15)


# --- the Ministry of Finance ---


def mof_csv(rows, title="Interest Rate"):
    """The publisher's layout: a title line, the header, YYYY/M/D dates."""
    head = f"{title},,,,(Unit : %)\nDate,1Y,2Y,10Y,40Y\n"
    return (head + "".join(f"{d.year}/{d.month}/{d.day},{a},{b},{c},{e}\n" for d, a, b, c, e in rows)).encode()


def test_jgb_yields_parse_to_long_format_and_a_missing_maturity_is_dropped():
    out = parse_mof_jgb(
        mof_csv([(dt.date(2015, 1, 5), 0.01, 0.02, 0.3, "-"), (dt.date(2020, 3, 9), -0.3, -0.25, -0.1, 0.4)])
    )
    assert sorted(out["tenor"].unique()) == [1.0, 2.0, 10.0, 40.0]
    first = out[out["obs_date"] == dt.date(2015, 1, 5)]
    assert sorted(first["tenor"]) == [1.0, 2.0, 10.0] and first.loc[first["tenor"] == 10.0, "rate"].iloc[0] == 0.3
    assert (
        out[(out["obs_date"] == dt.date(2020, 3, 9)) & (out["tenor"] == 1.0)]["rate"].iloc[0] == -0.3
    )  # negative yields are levels


def test_the_note_at_the_end_of_the_current_month_file_is_dropped():
    note = b',,,,\n"  \x81\xa6If you cannot download the latest csv data, please clear the browser\'s cache.",,,,\n'
    out = parse_mof_jgb(
        mof_csv([(dt.date(2026, 11, 2), 1.671, 1.88, 2.817, 3.96)], "Interest Rate (November 2026)") + note
    )
    assert len(out) == 4 and set(out["obs_date"]) == {dt.date(2026, 11, 2)}


def test_a_month_without_a_day_yet_is_empty_not_an_error():
    assert parse_mof_jgb(mof_csv([], "Interest Rate (January 2027)")).empty


def test_the_jgb_parser_refuses_a_page_a_file_without_header_and_absurd_levels():
    with pytest.raises(ValueError, match="HTML"):
        parse_mof_jgb(b"<html>maintenance</html>")
    with pytest.raises(ValueError, match="no 'Date,' header"):
        parse_mof_jgb(b"a,b\n1,2\n")
    with pytest.raises(ValueError, match="outside"):
        parse_mof_jgb(mof_csv([(dt.date(2020, 3, 9), 1.0, 1.0, 167.0, 1.0)]))


# --- the Bank of Japan ---


def boj_csv(rows, status="200", next_position="", code="STRDCLUCON"):
    head = (
        f"STATUS,{status}\nMESSAGEID,M181000I\nMESSAGE,Successfully completed\nDATE,2026-11-06T01:41:10.787+09:00\nPARAMETER,FORMAT,CSV\n"
        f"PARAMETER,DB,FM01\nNEXTPOSITION,{next_position}\nSERIES_CODE,NAME_OF_TIME_SERIES,UNIT,FREQUENCY,CATEGORY,LAST_UPDATE,SURVEY_DATES,VALUES\n"
    )
    line = (
        f'{code},"Call Rate, Uncollateralized Overnight, Average (Daily)",percent per annum,DAILY,Call Rate,20261105,'
    )
    return (head + "".join(f"{line}{d},{v}\n" for d, v in rows)).encode()


def test_the_call_rate_parses_and_a_day_without_a_rate_is_dropped():
    out = parse_boj_call(boj_csv([("20261030", 1.227), ("20261031", "null"), ("20161104", -0.042)]))
    assert out["obs_date"].tolist() == [dt.date(2026, 10, 30), dt.date(2016, 11, 4)] and out["value"].tolist() == [
        1.227,
        -0.042,
    ]


def test_the_call_rate_refuses_an_error_a_cut_answer_and_another_series():
    with pytest.raises(ValueError, match="answered 400"):
        parse_boj_call(boj_csv([], status="400"))
    with pytest.raises(ValueError, match="cut"):
        parse_boj_call(boj_csv([("20261030", 1.0)], next_position="251"))
    with pytest.raises(ValueError, match="expected STRDCLUCON"):
        parse_boj_call(boj_csv([("20261030", 1.0)], code="STRDCLUAVE"))


# --- the Bank of England ---


def test_sonia_parses_and_refuses_a_page_and_another_series():
    out = parse_boe_series(b"DATE,IUDSOIA\n02 Jan 2015,0.4316\n03 Nov 2026,3.7342\n")
    assert out["obs_date"].tolist() == [dt.date(2015, 1, 2), dt.date(2026, 11, 3)] and out["value"].iloc[1] == 3.7342
    with pytest.raises(ValueError, match="HTML"):
        parse_boe_series(b"<html></html>")
    with pytest.raises(ValueError, match="IUDSOIA"):
        parse_boe_series(b"DATE,OTHER\n02 Jan 2015,1.0\n")


def serial(day: dt.date) -> int:
    return (day - dt.date(1899, 12, 30)).days


def _cell(reference, value):
    return (
        f'<c r="{reference}" t="s"><v>{value[0]}</v></c>'
        if isinstance(value, tuple)
        else f'<c r="{reference}"><v>{value}</v></c>'
    )


def _sheet_xml(rows) -> str:
    body = ""
    for i, row in enumerate(rows, start=1):
        body += f'<row r="{i}">' + "".join(_cell(f"{c}{i}", v) for c, v in row.items()) + "</row>"
    return f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>'


LABEL, REFRESH = (0,), (1,)  # indexes in the shared strings, the "years:" and "Refresh" cells of the real files


def glc_xlsx(days, base=4.0, date1904=False, sheets=None) -> bytes:
    """A workbook as the Bank's: the sheets' files in another order than their tabs, a title row, a label row, a "Refresh" row, and a
    holiday with a date and no rate. Rates grow with the maturity and the day."""
    months = {"B": 1.0, "C": 2.0, "D": 3.0, "E": 6.0, "F": 12.0}
    years = {"B": 0.5, "C": 1.0, "D": 1.5, "E": 2.0, "F": 5.0, "G": 40.0}

    def sheet(heads, in_years, skip=()):
        rows = [
            {"B": LABEL},
            {"A": LABEL, **heads},
            {c: REFRESH for c in ("A", *heads)},
            {"A": serial(dt.date(2015, 1, 1))},
        ]
        for i, d in enumerate(days):
            t = {c: (m if in_years else m / 12) for c, m in heads.items() if c not in skip}
            rows.append({"A": serial(d), **{c: base + i * 0.01 + 0.1 * x for c, x in t.items()}})
        return _sheet_xml(rows)

    tabs = sheets or {
        "3. spot, short end": ("sheet5.xml", sheet(months, False, skip=("B",))),
        "4. spot curve": ("sheet2.xml", sheet(years, True)),
        "info": ("sheet1.xml", _sheet_xml([{"A": LABEL}])),
    }
    entries = "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(tabs, start=1))
    relations = "".join(
        f'<Relationship Id="rId{i}" Target="worksheets/{tabs[name][0]}"/>' for i, name in enumerate(tabs, start=1)
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.'
            f'openxmlformats.org/officeDocument/2006/relationships"><workbookPr{" date1904=" + chr(34) + "1" + chr(34) if date1904 else ""}/>'
            f"<sheets>{entries}</sheets></workbook>",
        )
        book.writestr("xl/_rels/workbook.xml.rels", f"<Relationships>{relations}</Relationships>")
        for path, xml in tabs.values():
            book.writestr(f"xl/worksheets/{path}", xml)
    return buffer.getvalue()


def test_a_glc_workbook_gives_its_months_and_years_as_exact_tenors():
    day = dt.date(2020, 1, 2)
    out = parse_glc_workbook(glc_xlsx([day]))
    assert set(out["obs_date"]) == {day}  # the holiday, a date without a rate, is gone
    assert sorted(out["tenor"]) == sorted(
        [2 / 12, 3 / 12, 6 / 12, 1.0, 1.5, 2.0, 5.0, 40.0]
    )  # 12 months and 0.5 years are not read twice
    by_tenor = out.set_index("tenor")["rate"]
    assert by_tenor[3 / 12] == pytest.approx(4.0 + 0.1 * 0.25) and by_tenor[40.0] == pytest.approx(4.0 + 0.1 * 40)


def test_a_glc_workbook_with_another_layout_is_refused():
    with pytest.raises(ValueError, match="no sheet '3. spot, short end'"):
        parse_glc_workbook(glc_xlsx([dt.date(2020, 1, 2)], sheets={"info": ("sheet1.xml", _sheet_xml([{"A": LABEL}]))}))
    with pytest.raises(ValueError, match="1904"):
        parse_glc_workbook(glc_xlsx([dt.date(2020, 1, 2)], date1904=True))
    with pytest.raises(ValueError, match="outside"):
        parse_glc_workbook(glc_xlsx([dt.date(2020, 1, 2)], base=400.0))


def zip_of(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_the_zips_give_their_nominal_workbooks_from_the_first_year_oldest_first():
    names = [
        "GLC Nominal daily data_2025 to present.xlsx",
        "GLC Nominal daily data_2000 to 2004.xlsx",
        "GLC Nominal daily data_2005 to 2015.xlsx",
        "GLC Real daily data current month.xlsx",
        "OIS daily data current month.xlsx",
        "GLC Nominal daily data_2016 to 2024.xlsx",
    ]
    assert list(glc_workbooks(zip_of(dict.fromkeys(names, b"x")), 2015)) == [names[2], names[5], names[0]]
    assert list(glc_workbooks(zip_of({"GLC Nominal daily data current month.xlsx": b"x", names[3]: b"y"}))) == [
        "GLC Nominal daily data current month.xlsx"
    ]
    with pytest.raises(ValueError, match="no GLC nominal workbook"):
        glc_workbooks(zip_of({names[3]: b"y"}))


# --- the curves ---


def test_jgb_par_yields_bootstrap_to_a_curve_that_reprices_par_bonds_even_when_negative():
    tenors = np.array([1.0, 2.0, 3.0, 5.0, 10.0, 40.0])
    for par in (
        np.array([-0.003, -0.0025, -0.002, -0.001, 0.001, 0.008]),
        np.array([0.0167, 0.019, 0.02, 0.0227, 0.0285, 0.0399]),
    ):
        t, _ = bootstrap_par(tenors, par)
        assert t.tolist() == tenors.tolist()
        for T, c in zip(tenors, par):
            grid = np.arange(0.5, T + 1e-9, 0.5)
            zero = bootstrap_par(np.append(tenors, grid), np.append(par, np.interp(grid, tenors, par)))
            price = c / 2 * np.exp(-np.interp(grid, *zero) * grid).sum() + np.exp(-np.interp(T, *zero) * T)
            assert price == pytest.approx(1.0, abs=1e-10)


def test_the_yen_curve_starts_with_the_call_rate_and_runs_straight_to_one_year():
    par = pd.DataFrame({"obs_date": [DAY] * 3, "tenor": [1.0, 2.0, 5.0], "rate": [1.0, 1.0, 1.0]})
    rows = par_curve_rows(par, pd.DataFrame({"obs_date": [DAY], "rate": [0.2]}), JPY_CURVE)
    assert rows["curve"].iloc[0] == JPY_CURVE and rows["tenor"].iloc[0] == pytest.approx(1 / 365)
    assert rows["zero_rate"].iloc[0] == pytest.approx(0.002, abs=1e-6) and rows["zero_rate"].iloc[1] == pytest.approx(
        2 * np.log(1.005), abs=1e-12
    )
    assert rows["discount"].to_numpy() == pytest.approx(np.exp(-rows["zero_rate"] * rows["tenor"]).to_numpy())


def test_gilt_rates_are_kept_as_continuous_zero_rates():
    zeros = pd.DataFrame({"obs_date": [DAY] * 2, "tenor": [1.0, 5.0], "rate": [4.0, 4.5]})
    rows = zero_curve_rows(zeros, pd.DataFrame({"obs_date": [DAY], "rate": [3.7]}), GBP_CURVE)
    assert rows["curve"].iloc[0] == GBP_CURVE and rows["zero_rate"].tolist()[1:] == [0.04, 0.045]
    assert rows["discount"].iloc[2] == pytest.approx(np.exp(-0.045 * 5.0), abs=1e-14)


# --- the loads and the forwards ---


def month(days, value=lambda i: 1.0 + 0.001 * i):
    return [(d.date(), value(i)) for i, d in enumerate(days)]


def fetchers(days):
    """The four publishers, on `days` of a synthetic world: history up to the last day but one, the last day in the current-month files."""
    history, current = days[:-1], days[-1:]

    def jgb(selection):
        return mof_csv([(d, a, a + 0.1, a + 0.5, a + 1.0) for d, a in month(selection)])

    def glc(selection, name):
        return zip_of({name: glc_xlsx([d.date() for d in selection], base=3.0)})

    urls = {
        rates.MOF_HISTORY_URL: jgb(history),
        rates.MOF_CURRENT_URL: jgb(current),
        rates.boj_call_url("201501"): boj_csv([(d.strftime("%Y%m%d"), 0.1) for d in days]),
        rates.GLC_HISTORY_URL: glc(history, "GLC Nominal daily data_2016 to 2024.xlsx"),
        rates.GLC_CURRENT_URL: glc(current, "GLC Nominal daily data current month.xlsx"),
        rates.boe_series_url(): ("DATE,IUDSOIA\n" + "".join(f"{d.strftime('%d %b %Y')},2.5\n" for d in days)).encode(),
    }
    return lambda url, **kwargs: urls[url]


def spots_for(lake, days):
    rng = np.random.default_rng(5)
    rows = [
        pd.DataFrame(
            {"pair": p, "obs_date": days, "rate": level * np.exp(np.cumsum(0.004 * rng.standard_normal(len(days))))}
        )
        for p, level in (("USDJPY", 108.0), ("GBPUSD", 1.25))
    ]
    lake.ingest("fx_spots", pd.concat(rows), "ecb", "derived")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """First a lake with spots and dollar rates only, with issues; then the loads of the four sources."""
    lake, days, _ = synthetic_lake(tmp_path_factory.mktemp("rates") / "lake", "2019-01-01", "2020-12-31")
    spots_for(lake, days)
    build_fx_forwards(lake)
    before = lake.read("quality_issues", where="check_name = 'no_curve'")
    time.sleep(1.1)  # ingestion stamps have one second of resolution
    stamp = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    time.sleep(1.1)
    fetch = fetchers(days)
    loads = load_mof(lake, fetch=fetch) + load_boj(lake, fetch=fetch) + load_boe(lake, fetch=fetch)
    build_discount_curves(lake, "2019-01-01")
    build_fx_forwards(lake)
    build_fx_vols(lake)
    build_fx_correl(lake, ("SPX", "NDX"))
    return lake, days, before, stamp, loads, fetch


@pytest.mark.slow
def test_before_the_loads_a_pair_says_which_curve_it_lacks(world):
    _, _, before, _, _, _ = world
    detail = dict(zip(before["item"], before["detail"]))
    assert set(detail) == {"USDJPY", "GBPUSD"} and "JPY" in detail["USDJPY"] and "GBP" in detail["GBPUSD"]
    assert set(before["severity"]) == {"warning"}


def test_the_loads_are_observed_reach_the_current_month_and_are_idempotent(world):
    lake, days, _, _, loads, fetch = world
    assert sum(r["n_new"] for r in loads) > 0
    points = lake.read(
        "rate_points",
        where="curve IN ('JPY.JGB', 'JPY.CALL', 'GBP.GLC', 'GBP.SONIA')",
        columns="curve, obs_date, origin",
    )
    assert set(points["origin"]) == {"observed"} and set(points["curve"]) == {
        "JPY.JGB",
        "JPY.CALL",
        "GBP.GLC",
        "GBP.SONIA",
    }
    last = days[-1].date()
    assert all(pd.to_datetime(g["obs_date"]).max().date() == last for _, g in points.groupby("curve"))
    again = load_mof(lake, fetch=fetch) + load_boj(lake, fetch=fetch) + load_boe(lake, fetch=fetch)
    assert sum(r["n_new"] + r["n_changed"] for r in again) == 0
    names = {m["name"] for m in lake.manifest()}
    assert {
        "jgbcme_all.csv",
        "jgbcme.csv",
        "call_rate.csv",
        "GLC Nominal daily data_2016 to 2024.xlsx",
        "IUDSOIA.csv",
    } <= names


def test_a_yen_and_a_sterling_curve_are_derived_and_start_overnight(world):
    lake, _, _, _, _, _ = world
    rows = lake.read(
        "discount_curves",
        where=f"curve IN ('{JPY_CURVE}', '{GBP_CURVE}') AND obs_date = DATE '{DAY}'",
        order="curve, tenor",
    )
    assert set(rows["origin"]) == {"derived"} and set(rows["source"]) == {"jgb+call", "boe_glc+sonia"}
    for _, g in rows.groupby("curve"):
        assert g["tenor"].iloc[0] == pytest.approx(1 / 365)
    assert rows[rows["curve"] == GBP_CURVE]["zero_rate"].iloc[0] == pytest.approx(0.025, abs=1e-5)


@pytest.mark.parametrize("pair, base, quote", [("USDJPY", "USD", "JPY"), ("GBPUSD", "GBP", "USD")])
def test_the_forwards_of_both_pairs_match_covered_parity_on_the_lakes_curves(world, pair, base, quote):
    lake, _, _, _, _, _ = world
    rows = lake.read("fx_forwards", where=f"pair = '{pair}' AND obs_date = DATE '{DAY}'", order="tenor")
    assert set(rows["origin"]) == {"derived"} and len(rows) == 7
    S = float(spot_series(lake, pair, DAY).iloc[-1])
    T = rows["tenor"].to_numpy()
    np.testing.assert_allclose(
        rows["forward"],
        cip_forward(S, discount_function(lake, quote, DAY)(T), discount_function(lake, base, DAY)(T)),
        rtol=1e-12,
    )
    np.testing.assert_allclose(rows["forward"] - rows["points"], S, rtol=1e-12)


@pytest.mark.slow
def test_the_forward_goes_the_way_of_the_rate_differential(world):
    lake, _, _, _, _, _ = world
    jpy = lake.read("fx_forwards", where=f"pair = 'USDJPY' AND obs_date = DATE '{DAY}'", order="tenor")
    assert (jpy["points"] < 0).all()  # yen rates are below dollar rates in this lake: fewer yen per dollar forward
    gbp = lake.read("fx_forwards", where=f"pair = 'GBPUSD' AND obs_date = DATE '{DAY}' AND tenor >= 1", order="tenor")
    assert (gbp["points"] < 0).all()  # sterling rates are above dollar rates: fewer dollars per pound forward


def test_the_issues_close_and_their_history_stays(world):
    lake, _, _, stamp, _, _ = world
    now = lake.read("quality_issues", where="check_name = 'no_curve'")
    assert set(now["severity"]) == {RESOLVED}
    assert open_issues(lake, "check_name = 'no_curve'").empty
    then = lake.read("quality_issues", where="check_name = 'no_curve'", as_of=stamp)
    assert set(then["severity"]) == {"warning"} and set(then["item"]) == {"USDJPY", "GBPUSD"}
    assert len(lake.versions("quality_issues", "check_name = 'no_curve'")) == 4
    build_fx_forwards(lake)  # nothing left to close
    assert len(lake.versions("quality_issues", "check_name = 'no_curve'")) == 4


def test_a_pair_keeps_its_issue_while_one_curve_is_missing(tmp_path):
    lake, days, _ = synthetic_lake(tmp_path / "lake", "2020-01-01", "2020-03-31")
    spots_for(lake, days)
    load_mof(lake, fetch=fetchers(days))
    build_discount_curves(lake, "2020-01-01")
    build_fx_forwards(lake)
    assert set(lake.read("fx_forwards", columns="pair")["pair"]) == {"USDJPY"}
    issues = lake.read("quality_issues", where="check_name = 'no_curve'")
    assert issues["item"].tolist() == ["GBPUSD"] and "GBP" in issues["detail"].iloc[0]


# --- paying in yen and in sterling ---


def test_a_sheet_can_pay_in_yen_and_sterling_and_nothing_else_was_opened():
    assert preset("phoenix", **{"general.payment_currency": "JPY"}).payment_currency == "JPY"
    assert preset("phoenix", **{"general.payment_currency": "GBP"}).payment_currency == "GBP"
    with pytest.raises(TermSheetError, match="payment_currency"):
        preset("phoenix", **{"general.payment_currency": "CHF"})


@pytest.mark.parametrize("payment, pair, inverted", [("JPY", "USDJPY", False), ("GBP", "GBPUSD", True)])
def test_a_dollar_name_paid_in_yen_or_sterling_has_that_curve_and_the_adjusted_forward(world, payment, pair, inverted):
    lake, _, _, _, _, _ = world
    native, quanto = market_at(lake, "SPX", DAY), market_at(lake, "SPX", DAY, payment=payment)
    meta = quanto.provenance["quanto"]
    assert quanto.provenance["payment"] == payment and quanto.provenance["discount"]["curve"] == payment
    assert (
        meta["pair"] == pair
        and meta["inverted"] is inverted
        and meta["foreign"] == "USD"
        and meta["domestic"] == payment
    )
    ctx, own = PricingContext.from_snapshot(quanto), PricingContext.from_snapshot(native)
    assert ctx.payment_currency == payment
    disc = discount_function(lake, payment, DAY)
    for T in (0.5, 1.0, 2.0):
        assert float(ctx.market.D(T)) == pytest.approx(float(disc(T)), rel=1e-6)
        ratio = np.log(float(ctx.market.F(T)) / float(own.market.F(T)))
        assert ratio == pytest.approx(-meta["rho"] * meta["vol_fx"] * float(own.surface.iv(0.0, T)) * T, rel=0.25)


def test_a_basket_of_dollar_names_pays_in_yen(world):
    lake, _, _, _, _, _ = world
    ctx, _, meta = basket_at(lake, ["SPX", "NDX"], DAY, payment="JPY")
    assert ctx.payment_currency == "JPY" and set(meta["quanto"]) == {"SPX", "NDX"}
    assert float(ctx.markets["SPX"].D(1.0)) == pytest.approx(float(discount_function(lake, "JPY", DAY)(1.0)), rel=1e-6)


def test_a_euro_name_in_yen_is_refused_as_no_pair_links_them(world):
    lake, _, _, _, _, _ = world
    with pytest.raises(KeyError, match="no enabled pair between EUR and JPY"):
        quanto_link_at(lake, "SX5E", "JPY", DAY)
