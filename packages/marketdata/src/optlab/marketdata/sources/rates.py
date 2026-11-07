"""Yen and sterling rates from public sources, all pure parsers.

Yen: the Ministry of Finance's JGB par yields, 1 to 40 years, semi-annual, in percent, and the Bank of Japan's uncollateralised
overnight call rate as the short end. Sterling: the Bank of England's fitted gilt curve (GLC) and SONIA. GLC spot rates are continuously
compounded zero rates in percent: integrating the workbook's own forward sheet gives its spot sheet to 0.0001 bp, against 8 bp if they
were semi-annual. Both publishers split their data in the same way: a history that stops at the end of the last month, and a small file
for the current month. Both are needed to reach the last days.

The GLC comes as xlsx, and only its numeric cells are needed: they are read from the sheet's xml, without a dependency for one source.
Column A holds Excel date serials; a day without a fit, such as a holiday, has a date and no rate.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd
from optlab.marketdata.sources.simple import _text

# --- Ministry of Finance: two title and header lines, YYYY/M/D, percent, "-" when a maturity did not exist ---
MOF_HISTORY_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
MOF_CURRENT_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
JGB_CURVE = "JPY.JGB"
PLAUSIBLE = (-3.0, 25.0)  # percent, any JGB, gilt or overnight level since 1974


def _in_range(rows: pd.DataFrame, what: str, column: str = "rate") -> pd.DataFrame:
    """A rate far outside any level ever seen means a misread file, not a market move: stop the load."""
    bad = rows[(rows[column] <= PLAUSIBLE[0]) | (rows[column] >= PLAUSIBLE[1])]
    if len(bad):
        raise ValueError(
            f"{what}: {len(bad)} rates outside {PLAUSIBLE}, first {bad[column].iloc[0]:g} on {bad['obs_date'].iloc[0]}"
        )
    return rows


def _refuse_html(text: str, who: str) -> None:
    if text.lstrip().startswith("<"):
        raise ValueError(f"{who} answered with an HTML page, not a CSV")


def parse_mof_jgb(content) -> pd.DataFrame:
    """Long format: obs_date, tenor (years), rate (percent). The current month's file ends with a note, which is dropped."""
    text = _text(content)
    _refuse_html(text, "the Ministry of Finance")
    lines = text.splitlines()
    head = next((i for i, line in enumerate(lines) if line.startswith("Date,")), None)
    if head is None:
        raise ValueError("no 'Date,' header in the JGB file")
    df = pd.read_csv(
        io.StringIO("\n".join(lines[head:])), na_values=["-"], usecols=lambda c: c == "Date" or re.fullmatch(r"\d+Y", c)
    )
    dates = pd.to_datetime(df["Date"], format="%Y/%m/%d", errors="coerce")
    parts = [
        pd.DataFrame(
            {"obs_date": dates, "tenor": float(col[:-1]), "rate": pd.to_numeric(df[col], errors="coerce")}
        ).dropna()
        for col in df.columns
        if col != "Date"
    ]
    if not parts:
        raise ValueError(f"no maturity column in the JGB file: {list(df.columns)}")
    out = pd.concat(parts, ignore_index=True)
    out["obs_date"] = out["obs_date"].dt.date
    return _in_range(out, "JGB")


# --- Bank of Japan: API with a metadata block, then one row per calendar day, "null" on the days without a rate ---
CALL_SERIES = "STRDCLUCON"
CALL_CURVE = "JPY.CALL"


def boj_call_url(start: str = "201501") -> str:
    return f"https://www.stat-search.boj.or.jp/api/v1/getDataCode?db=FM01&code={CALL_SERIES}&startDate={start}&lang=en&format=csv"


def parse_boj_call(content) -> pd.DataFrame:
    """obs_date, value (percent). Refuses an error answer, another series, or a block that says more follows."""
    text = _text(content)
    _refuse_html(text, "the Bank of Japan")
    lines = text.splitlines()
    meta = {
        line.split(",")[0]: line.split(",", 1)[1].strip()
        for line in lines
        if "," in line and not line.startswith("SERIES_CODE")
    }
    head = next((i for i, line in enumerate(lines) if line.startswith("SERIES_CODE,")), None)
    if head is None or meta.get("STATUS") != "200":
        raise ValueError(f"the Bank of Japan answered {meta.get('STATUS')}: {meta.get('MESSAGE')}")
    if meta.get("NEXTPOSITION"):
        raise ValueError("the Bank of Japan's answer is cut, it continues at position " + meta["NEXTPOSITION"])
    df = pd.read_csv(io.StringIO("\n".join(lines[head:])), dtype={"SURVEY_DATES": str})
    if set(df["SERIES_CODE"]) - {CALL_SERIES}:
        raise ValueError(f"expected {CALL_SERIES}, the file holds {sorted(set(df['SERIES_CODE']))}")
    out = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(df["SURVEY_DATES"], format="%Y%m%d", errors="coerce"),
            "value": pd.to_numeric(df["VALUES"], errors="coerce"),
        }
    ).dropna()
    out["obs_date"] = out["obs_date"].dt.date
    return _in_range(out.reset_index(drop=True), "call rate", "value")


# --- Bank of England: the statistical database for SONIA, and the yield-curve zips of xlsx ---
SONIA_SERIES = "IUDSOIA"
SONIA_CURVE = "GBP.SONIA"
GLC_CURVE = "GBP.GLC"
GLC_HISTORY_URL = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/glcnominalddata.zip"
GLC_CURRENT_URL = (
    "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip"
)
GLC_SHORT_MONTHS = range(
    1, 12
)  # where the short end of a day starts varies, and the six-month point is missing on a third of the days
GLC_YEARS = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0)
_GLC_FILE = re.compile(r"GLC Nominal daily data(?:_(\d{4}) to (\d{4}|present)| current month)\.xlsx")
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RELATION = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_EXCEL_EPOCH = dt.date(1899, 12, 30)


def boe_series_url(series: str = SONIA_SERIES, start: str = "01/Jan/2015") -> str:
    return (
        "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes"
        f"&Datefrom={start}&Dateto=now&SeriesCodes={series}&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )


def parse_boe_series(content, series: str = SONIA_SERIES) -> pd.DataFrame:
    """obs_date, value (percent) from the statistical database: `DATE,CODE`, dates as 02 Jan 2015."""
    text = _text(content)
    _refuse_html(text, "the Bank of England")
    df = pd.read_csv(io.StringIO(text))
    if series not in df.columns:
        raise ValueError(f"{series}: columns {list(df.columns)}")
    out = pd.DataFrame(
        {
            "obs_date": pd.to_datetime(df["DATE"], format="%d %b %Y", errors="coerce"),
            "value": pd.to_numeric(df[series], errors="coerce"),
        }
    ).dropna()
    out["obs_date"] = out["obs_date"].dt.date
    return _in_range(out.reset_index(drop=True), series, "value")


def glc_workbooks(content: bytes, first_year: int = 2015) -> dict[str, bytes]:
    """{name: xlsx} of the nominal workbooks of a zip that reach `first_year`, oldest first. The real, inflation and OIS ones are left out."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        kept = []
        for name in archive.namelist():
            m = _GLC_FILE.fullmatch(name)
            if m and (m[2] in (None, "present") or int(m[2]) >= first_year):
                kept.append((int(m[1]) if m[1] else 9999, name))
        if not kept:
            raise ValueError(f"no GLC nominal workbook in the zip: {archive.namelist()}")
        return {name: archive.read(name) for _, name in sorted(kept)}


def _column(reference: str) -> str:
    return re.match(r"[A-Z]+", reference)[0]


def _sheet_numbers(book: zipfile.ZipFile, title: str):
    """Yields {column: number} for each row of a sheet. Text cells (labels, the "Refresh" row) and empty ones are left out."""
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    properties = workbook.find(f"{_MAIN}workbookPr")
    if properties is not None and properties.get("date1904") in ("1", "true"):
        raise ValueError("this workbook counts its dates from 1904, the parser assumes 1900")
    ids = {s.get("name"): s.get(f"{_RELATION}id") for s in workbook.find(f"{_MAIN}sheets")}
    if title not in ids:
        raise ValueError(f"no sheet {title!r} in the workbook: {sorted(ids)}")
    targets = {r.get("Id"): r.get("Target") for r in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
    with book.open("xl/" + targets[ids[title]].removeprefix("/xl/")) as sheet:
        for _, element in ET.iterparse(sheet):
            if element.tag == f"{_MAIN}row":
                row = {}
                for cell in element.iter(f"{_MAIN}c"):
                    value = cell.find(f"{_MAIN}v")
                    if value is not None and cell.get("t", "n") == "n":
                        row[_column(cell.get("r"))] = float(value.text)
                yield row
                element.clear()


def _glc_sheet(book: zipfile.ZipFile, title: str, wanted: dict) -> list[tuple]:
    """(obs_date, tenor, rate) of a sheet: its first row of numbers without a date in column A holds the maturities, the keys of `wanted`."""
    columns, rows = None, []
    for row in _sheet_numbers(book, title):
        if "A" in row:
            day = _EXCEL_EPOCH + dt.timedelta(days=int(row["A"]))
            if columns is None:
                raise ValueError(f"{title}: a date before the row of maturities")
            rows += [(day, columns[c], v) for c, v in row.items() if c in columns]
        elif columns is None and len(row) > 2:
            columns = {c: wanted[m] for c, m in row.items() if m in wanted}
    if columns is None:
        raise ValueError(f"{title}: no row of maturities")
    return rows


def parse_glc_workbook(content: bytes) -> pd.DataFrame:
    """Long format: obs_date, tenor (years), rate (percent, continuous zero rate). Below a year, from the short-end sheet, in months: it
    only holds the maturities the gilts of that day reach."""
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        rows = _glc_sheet(book, "3. spot, short end", {float(m): m / 12 for m in GLC_SHORT_MONTHS}) + _glc_sheet(
            book, "4. spot curve", {y: y for y in GLC_YEARS}
        )
    return _in_range(pd.DataFrame(rows, columns=["obs_date", "tenor", "rate"]), "GLC")
