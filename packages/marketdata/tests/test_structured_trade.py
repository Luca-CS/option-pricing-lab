"""Trades and market snapshots: serialisation, immutability and the scale of real fixings."""

import datetime as dt
import json

import pytest
from _structured_refs import flat_context

from optlab.core.snapshot import MarketSnapshot, to_model_scale
from optlab.marketdata import MarketStore
from optlab.structured import PRESETS, PricingContext, TermSheet, TermSheetError, compile_sheet, preset, quote
from optlab.structured.context import DEFAULT_CALIBRATION
from optlab.structured.portfolio import Book
from optlab.structured.schema import json_schema
from optlab.structured.trade import Trade

CTX = flat_context()
VAL = dt.date(2026, 10, 27)
STRIKE = dt.date(2026, 4, 27)


# --- term sheets and trades ---


@pytest.mark.parametrize("name", list(PRESETS))
def test_term_sheets_round_trip_through_json(name):
    sheet = preset(name, **{"general.strike_date": VAL})
    back = TermSheet.from_dict(json.loads(json.dumps(sheet.to_dict(json_safe=True))))
    assert back.to_dict() == sheet.to_dict()
    assert compile_sheet(back, VAL).script == compile_sheet(sheet, VAL).script


def test_worst_of_names_round_trip():
    sheet = preset("phoenix", **{"general.basket": "worst_of", "general.underlyings": ("SPX", "SX5E", "NKY")})
    assert TermSheet.from_dict(sheet.to_dict(json_safe=True)).underlyings == ("SPX", "SX5E", "NKY")


def test_json_schema_has_enums_bounds_and_visibility():
    schema = json_schema()
    autocall = next(b for b in schema["oneOf"] if b["properties"]["type"]["const"] == "autocall")
    coupon = autocall["properties"]["coupon"]["properties"]
    assert coupon["type"]["enum"] == ["fixed", "conditional", "snowball"]
    assert coupon["rate"]["maximum"] == 1.0 and coupon["rate"]["minimum"] == 0.0
    assert coupon["barrier"]["x-visible-if"] == {"coupon.type": ["conditional"]}
    assert "gearing" not in autocall["properties"]["downside"]["properties"]
    json.dumps(schema)


def test_a_trade_round_trips_and_never_restrikes():
    trade = Trade.from_term_sheet("PHX-1", preset("phoenix"), -10, VAL)
    assert trade.strike_date == VAL and trade.term_sheet().get("general.strike_date") == VAL
    back = Trade.from_json(trade.to_json())
    assert back == trade
    # priced a week later, the stored trade keeps its strike: it needs the fixing of its strike date
    later = compile_sheet(back.term_sheet(), dt.date(2026, 11, 3))
    assert later.strike_date == VAL


def test_a_trade_rejects_a_sheet_that_contradicts_its_strike_date():
    sheet = preset("phoenix", **{"general.strike_date": VAL}).to_dict(json_safe=True)
    with pytest.raises(TermSheetError, match="build it with Trade.from_term_sheet"):
        Trade("X", {"kind": "term_sheet", "term_sheet": sheet}, 1.0, VAL, STRIKE)
    with pytest.raises(ValueError, match="unknown status"):
        Trade.from_term_sheet("X", preset("phoenix"), 1.0, VAL, status="open")


def test_a_trade_prices_like_its_term_sheet_bit_for_bit():
    sheet = preset("reverse_convertible", **{"wrapper.solve": "upfront", "general.strike_date": VAL})
    trade = Trade.from_term_sheet("RC-1", sheet, 3.0, VAL)
    a = Book([trade.to_position()]).price(CTX, model="bs", n_paths=20_000, seed=5, batch=10_000)
    from optlab.structured.portfolio import Position

    b = Book([Position("RC-1", sheet, 3.0)]).price(CTX, model="bs", n_paths=20_000, seed=5, batch=10_000)
    assert a.value == b.value and a.stderr == b.stderr


def test_a_script_trade():
    script = compile_sheet(preset("reverse_convertible", **{"general.strike_date": VAL}), VAL).script
    trade = Trade("S-1", {"kind": "script", "script": script, "params": {"cpn": 0.01}, "format": "note"}, 1.0, VAL, VAL)
    pos = Trade.from_json(trade.to_json()).to_position()
    assert pos.params == {"cpn": 0.01} and pos.fmt == "note"


# --- market snapshots ---


def test_snapshot_ids_are_stable_and_the_context_is_unchanged():
    a = MarketSnapshot.from_calibration_file(DEFAULT_CALIBRATION)
    b = MarketSnapshot.from_calibration_file(DEFAULT_CALIBRATION, provenance=False)
    assert a.snapshot_id == b.snapshot_id and a.snapshot_id.startswith("SPX-20261027T")
    assert a.provenance["file_sha256"] and "spot_scale" not in a.to_json()
    ctx = PricingContext.from_calibration()
    assert ctx.snapshot_id == a.snapshot_id
    q1 = quote(preset("phoenix", **{"general.maturity_months": 12}), ctx, control=None, n_paths=20_000, batch=10_000)
    q2 = quote(
        preset("phoenix", **{"general.maturity_months": 12}),
        PricingContext.from_snapshot(a),
        control=None,
        n_paths=20_000,
        batch=10_000,
    )
    assert q1.coupon_rate == q2.coupon_rate and q1.snapshot_id == a.snapshot_id


def test_the_simulated_calibration_loads_with_a_valuation_date():
    path = DEFAULT_CALIBRATION.parent / "simulated_calibration.json"
    with pytest.raises(ValueError, match="give the valuation date"):
        PricingContext.from_calibration(path)
    ctx = PricingContext.from_calibration(path, valuation="2026-10-27")
    assert ctx.valuation == VAL and ctx.snapshot_id


def test_the_store_is_immutable_and_finds_the_latest(tmp_path):
    store = MarketStore(tmp_path)
    snap = MarketSnapshot.from_calibration_file(DEFAULT_CALIBRATION)
    sid = store.put(snap)
    assert store.put(snap) == sid  # idempotent
    assert store.get(sid) == snap
    assert store.latest("SPX", dt.date(2026, 10, 28)).snapshot_id == sid
    with pytest.raises(KeyError):
        store.latest("SPX", dt.date(2026, 10, 26))
    # the local spot scale is kept in the index, not in the snapshot file, and does not change the id
    assert store.put(snap.with_spot_scale(5800.0)) == sid
    assert store.get(sid).spot_scale == pytest.approx(58.0)
    assert "spot_scale" not in next(tmp_path.rglob(f"{sid}.json")).read_text()
    path = next(tmp_path.rglob(f"{sid}.json"))
    path.write_text(path.read_text().replace('"source": "spx"', '"source": "edited"'))
    with pytest.raises(ValueError, match="different content"):
        store.put(snap)


def test_real_fixings_against_a_rescaled_market_are_refused_until_converted():
    sheet = preset("reverse_convertible", **{"general.strike_date": STRIKE, "wrapper.solve": "upfront"})
    spx = PricingContext.from_calibration()
    with pytest.raises(ValueError, match="convert them with optlab.core.snapshot.to_model_scale"):
        quote(sheet, spx, control=None, fixings={"SPX": {STRIKE: 5800.0}})
    converted = to_model_scale({"SPX": {STRIKE: 5800.0}}, 58.0)
    assert converted["SPX"][STRIKE] == pytest.approx(100.0)
    q = quote(sheet, spx, control=None, fixings=converted)
    ref = quote(sheet, spx, control=None, fixings={"SPX": {STRIKE: 100.0}})
    assert q.upfront == pytest.approx(ref.upfront, rel=1e-14)
