"""The desk API and its HTTP server: term-sheet forms, quote and book jobs, trades, fixings, events, script lab,
reports, the static page and the token. Quotes run at the quick precision (20,000 paths) on the SPX calibration."""

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from optlab.app.api import ApiError, Desk
from optlab.app.fixings import parse_fixings
from optlab.app.netcheck import interpret
from optlab.app.report import book_report, quote_report
from optlab.app.server import serve, serve_in_thread
from optlab.app.tunnel import public_url

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    d = Desk(store=tmp_path_factory.mktemp("desk") / "desk.sqlite")
    yield d
    d.jobs.shutdown()


def wait(desk, job_id, timeout=300):
    t0 = time.perf_counter()
    while (j := desk.job(job_id))["state"] in ("queued", "running"):
        assert time.perf_counter() - t0 < timeout, "job did not finish"
        time.sleep(0.1)
    assert j["state"] == "done", j.get("error")
    return j["result"]


def sheet_of(desk, name):
    s = next(p["sheet"] for p in desk.presets()["presets"] if p["name"] == name)
    return {"type": s["type"], "values": {k: v for k, v in s.items() if k != "type"}}


def test_health_context_and_market(desk):
    h = desk.health()
    assert h["ok"] and h["valuation"] == "2026-10-27" and h["snapshot_id"].startswith("SPX-")
    c = desk.context()
    assert c["spot"] == pytest.approx(100.0) and 0.05 < c["atm_vols"]["1"] < 0.5 and c["maturities"]
    s = desk.smile(1.0)
    assert len(s["moneyness"]) == len(s["iv"]) == 61 and all(0 < v < 1.5 for v in s["iv"])
    t = desk.term_structure()
    assert len(t["T"]) == 40 and t["discount"][0] > t["discount"][-1]
    risk = desk.model_risk()
    assert "exotics" in risk and risk["errors"]


def test_presets_round_trip_through_the_form(desk):
    presets = desk.presets()
    assert {"autocall", "bullet"} <= set(presets["masks"])
    for p in presets["presets"]:  # a sheet read back from the API carries the computed gearing: still accepted
        f = desk.form(sheet_of(desk, p["name"]))
        assert f["error"] is None, (p["name"], f["error"])
        assert f["form"] and f["bounds"]


def test_form_reports_the_field_in_error(desk):
    body = sheet_of(desk, "phoenix")
    body["values"]["coupon"]["barrier"] = -0.2
    f = desk.form(body)
    assert f["sheet"] is None and f["error"]["field"] == "coupon.barrier"
    # the form keeps the conditional coupon of the sheet, so the field to correct stays on screen
    shown = [x["path"] for sec in f["form"]["sections"] for x in sec["fields"]]
    assert "coupon.barrier" in shown and "coupon.memory" in shown
    with pytest.raises(ApiError) as exc:
        desk.form({"type": "nope"})
    assert exc.value.status == 422


def test_quote_job_end_to_end(desk):
    job = desk.quote({"sheet": sheet_of(desk, "phoenix"), "options": {"precision": "quick", "greeks": False}})["job"]
    q = wait(desk, job)
    assert q["headline"]["kind"] == "coupon" and 0 < q["headline"]["value"] < 30 and q["headline"]["se"] > 0
    assert q["policy"]["class"] == "callable" and q["options"]["n_paths"] == 20_000
    assert q["schedule"] and abs(sum(r["p_end"] for r in q["schedule"]) - 1) < 1e-9
    saved = desk.saved_quote(q["quote_id"])
    assert saved["result"]["headline"] == q["headline"]
    assert desk.quotes()["quotes"][0]["quote_id"] == q["quote_id"]
    html = quote_report(saved)
    assert html.startswith("<!doctype html>") and "<svg" in html and q["snapshot_id"] in html


def test_quote_with_slv_vega(desk):
    """SLV has a vega only when the leverage is recalibrated on the bumped surface: the option reaches the library."""
    job = desk.quote({"sheet": sheet_of(desk, "phoenix"),
                      "options": {"precision": "quick", "greeks": True, "slv_vega": True}})["job"]
    g = wait(desk, job)["greeks"]
    assert g["model"] == "slv" and g["vega"] < 0 and 0 < g["vega_se"] < abs(g["vega"]) / 4
    assert 0 < g["delta"] and g["delta_se"] < g["delta"] / 4


def test_trades_versions_delete_and_restore(desk):
    t = desk.create_trade({"sheet": sheet_of(desk, "phoenix"), "coupon_rate": 0.05, "quantity": 2.0})
    tid = t["trade_id"]
    assert t["version"] == 1 and t["summary"]["coupon_rate"] == 0.05
    assert t["product"]["term_sheet"]["wrapper"]["solve"] == "upfront"
    t2 = desk.update_trade(tid, {"quantity": 3.0, "counterparty": "Desk B"})
    assert t2["version"] == 2 and t2["quantity"] == 3.0 and t2["counterparty"] == "Desk B"
    desk.delete_trade(tid)
    assert tid not in [x["trade_id"] for x in desk.trades()["trades"]]
    desk.restore_trade(tid)
    assert tid in [x["trade_id"] for x in desk.trades()["trades"]]


def test_fixings_scale_and_future_dates(desk):
    # real index levels are divided by real spot / model spot (the market is rescaled to 100)
    r = desk.put_fixings({"underlying": "SX5E", "levels": {"2026-10-26": 5100.0}, "real_spot": 5000.0})
    assert r["stored"] == 1
    assert desk.fixings("SX5E")["fixings"]["SX5E"]["2026-10-26"] == pytest.approx(102.0)
    with pytest.raises(ApiError):
        desk.put_fixings({"underlying": "SX5E", "levels": {"2026-12-01": 100.0}})
    desk.delete_fixing("SX5E", "2026-10-26")
    assert "SX5E" not in desk.fixings()["fixings"]


def test_fixings_text_lines_columns_and_yahoo_csv(desk):
    yahoo = ("Date,Open,High,Low,Close,Adj Close,Volume\n2026-10-20,4890,4910,4880,4902.5,4902.5,100\n"
             "2026-10-21,4900,4930,4895,4925.0,4920.0,100\n2026-10-24,null,null,null,null,null,0\n")
    levels, skipped = parse_fixings(yahoo)  # the Close column, not the open nor the adjusted close
    assert levels == {"2026-10-20": 4902.5, "2026-10-21": 4925.0}
    assert skipped == ["2026-10-24,null,null,null,null,null,0"]
    levels, skipped = parse_fixings("27/07/2026; 104.2\n2026-10-27\t98.7\n\n2026-10-28 -3\nnot a fixing")
    assert levels == {"2026-07-27": 104.2, "2026-10-27": 98.7} and skipped == ["2026-10-28 -3", "not a fixing"]
    r = desk.put_fixings({"underlying": "sx5e", "text": yahoo, "real_spot": 4950.0})
    assert r["stored"] == 2 and len(r["skipped"]) == 1
    assert desk.fixings("SX5E")["fixings"]["SX5E"]["2026-10-20"] == pytest.approx(100 * 4902.5 / 4950)
    with pytest.raises(ApiError, match="no line"):
        desk.put_fixings({"underlying": "SX5E", "text": "nothing here"})
    for d in ("2026-10-20", "2026-10-21"):
        desk.delete_fixing("SX5E", d)


def test_lan_blockers_read_from_the_windows_firewall():
    python = r"C:\Program Files\Python314\python.exe"
    data = {"profiles": {"alias": "Wi-Fi", "category": "Public"},  # one object, as ConvertTo-Json writes it
            "rules": [{"program": r"C:\program files\python314\python.exe", "action": "Block", "profile": "Public",
                       "enabled": "True", "direction": "Inbound"},
                      {"program": r"C:\Other\python.exe", "action": "Block", "profile": "Public", "enabled": "True",
                       "direction": "Inbound"}]}
    reasons = interpret(data, python)
    assert len(reasons) == 2 and "blocks incoming connections" in reasons[0] and "Wi-Fi" in reasons[1]
    data = {"profiles": [{"alias": "Wi-Fi", "category": "Private"}],
            "rules": [{"program": python, "action": "Allow", "profile": "Private", "enabled": "True",
                       "direction": "Inbound"}]}
    assert interpret(data, python) == []


def test_seasoned_trade_events_book_and_report(desk):
    t = desk.create_trade({"sheet": sheet_of(desk, "phoenix"), "coupon_rate": 0.06, "quantity": 1.0,
                           "strike_date": "2026-01-27", "trade_date": "2026-01-20"})
    tid = t["trade_id"]
    ev = desk.trade_events(tid)
    assert ev["missing"] and ev["past"] == [] and ev["strike_date"] == "2026-01-27"
    assert all(u["observation"] > "2026-10-27" for u in ev["upcoming"])
    desk.put_fixings({"underlying": "SPX", "levels": {"2026-01-27": 100.0, "2026-04-27": 95.0, "2026-07-27": 90.0,
                                                      "2026-10-27": 92.0}})
    ev = desk.trade_events(tid)
    assert ev["missing"] is None and "coupon_paid" in [e["kind"] for e in ev["past"]]
    up = desk.upcoming(120)["events"]
    assert tid in [e["trade_id"] for e in up] and up == sorted(up, key=lambda e: e["observation"])

    book = wait(desk, desk.price_book({"options": {"precision": "quick", "greeks": True}})["job"])
    names = [p["name"] for p in book["positions"]]
    assert tid in names and book["stderr"] > 0 and book["greeks"]["by_name"]["SPX"]["delta"] > 0
    assert book["independent_stderr"] > 0
    assert desk.last_book is not None
    html = book_report(desk)
    assert tid in html and "<table" in html


def worst_of(desk, names=("SPX", "SX5E")):
    body = sheet_of(desk, "phoenix")
    body["values"]["general"].update(basket="worst_of", underlyings=list(names))
    return body


def test_basket_settings_are_validated(desk):
    with pytest.raises(ApiError, match="volatility"):
        desk.put_basket({"names": {"SX5E": {"vol": 5.0}}})
    with pytest.raises(ApiError, match="valid matrix"):  # SPX close to both, the two others opposite: not a matrix
        desk.put_basket({"names": {"AAA": {}, "BBB": {}},
                         "corr": {"SPX|AAA": 0.9, "SPX|BBB": 0.9, "AAA|BBB": -0.9}})
    s = desk.put_basket({"names": {"sx5e": {"vol": 0.18, "q": 0.03}}, "corr": {"SPX|SX5E": 0.75}})
    assert s["names"]["SX5E"]["vol"] == 0.18 and s["corr"] == {"SPX|SX5E": 0.75}


def test_worst_of_quote_book_and_trade_page(desk):
    desk.put_basket({"names": {"SX5E": {"vol": 0.18, "q": 0.03}}, "corr": {"SPX|SX5E": 0.75}})
    q = wait(desk, desk.quote({"sheet": worst_of(desk), "options": {"precision": "quick", "greeks": True}})["job"])
    single = wait(desk, desk.quote({"sheet": sheet_of(desk, "phoenix"), "options": {"precision": "quick"}})["job"])
    assert q["basket"]["names"]["SX5E"]["vol"] == 0.18 and q["basket"]["corr"] == {"SPX|SX5E": 0.75}
    assert q["policy"]["model"] == "lv" and set(q["greeks"]["per_name"]) == {"SPX", "SX5E"}
    assert q["headline"]["value"] > single["headline"]["value"] + 4 * q["headline"]["se"]  # worst-of pays more
    t = desk.create_trade({"sheet": worst_of(desk), "coupon_rate": q["coupon_rate"], "quantity": 2.0,
                           "tags": [f"quote:{q['quote_id']}"]})
    detail = desk.trade(t["trade_id"])
    assert detail["quote_id"] == q["quote_id"] and detail["versions"][0]["quantity"] == 2.0
    assert detail["form"]["sections"] and detail["trade"]["summary"]["underlyings"] == ["SPX", "SX5E"]
    v = wait(desk, desk.value_trade(t["trade_id"], {"options": {"precision": "quick"}})["job"])
    # the trade's coupon was solved on the same paths for a note price of 98: the value says so
    assert v["unit_price"] == pytest.approx(98.0, abs=1e-6) and v["value"] == pytest.approx(2.0 * 100 * 0.98)
    book = wait(desk, desk.price_book({"options": {"precision": "quick", "greeks": True}})["job"])
    groups = {g["label"]: g for g in book["groups"]}
    assert "worst-of" in groups and groups["worst-of"]["model"] == "lv"
    assert book["value"] == pytest.approx(sum(g["value"] for g in book["groups"]))
    assert book["stderr"] == pytest.approx(sum(g["stderr"] ** 2 for g in book["groups"]) ** 0.5)
    assert {"SPX", "SX5E"} <= set(book["greeks"]["by_name"])
    assert len(book["greeks"]["positions"]) == len(book["positions"])
    assert "worst-of" in book_report(desk)
    assert desk.trade_events(t["trade_id"])["underlyings"] == ["SPX", "SX5E"]


def test_snapshots_switch_the_market(desk):
    snaps = desk.snapshots()["snapshots"]
    active = next(s for s in snaps if s["active"])
    other = next(s for s in snaps if s["source"] == "simulated")
    assert active["file"] == "spx_calibration.json"
    try:
        ctx = desk.use_snapshot({"snapshot_id": other["snapshot_id"]})
        assert ctx["snapshot_id"] == other["snapshot_id"] and desk.health()["snapshot_id"] == other["snapshot_id"]
        assert desk.model_risk()["exotics"]  # the simulated calibration carries its own table
        assert desk.last_book is None
    finally:
        desk.use_snapshot({"snapshot_id": active["snapshot_id"]})
    with pytest.raises(ApiError) as exc:
        desk.use_snapshot({"snapshot_id": "nope"})
    assert exc.value.status == 404


def test_tunnel_address_is_read_from_the_relay_output():
    line = "abc123.lhr.life tunneled with tls termination, https://abc123.lhr.life"
    assert public_url(line) == "https://abc123.lhr.life"
    assert public_url("** your connection id is 1f2e, please mention it if you send me a message **") is None


def test_script_lab(desk):
    src = (ROOT / "optlab/app/static/js/views/script.js").read_text(encoding="utf-8")
    example = re.search(r"const EXAMPLE = `(.*?)`;", src, re.S)[1]  # the page's example must stay valid
    ok = desk.script_check({"script": example})
    assert ok["ok"] and list(ok["underlyings"]) == ["SPX"]
    bad = desk.script_check({"script": "underlying SPX\nevent 2027-10-27\n  pay (SPX as redemption\nend\n"})
    assert not bad["ok"] and bad["line"] == 3
    with pytest.raises(ApiError):
        desk.script_price({"script": "underlying SPX\nevent"})
    r = wait(desk, desk.script_price({"script": example, "model": "lv", "n_paths": 20_000})["job"])
    assert 50 < r["price"] < 130 and 0 < r["stderr"] < 1


def test_jobs_report_errors_and_cancel(desk):
    def boom():
        raise RuntimeError("no luck")

    j = desk.jobs.submit("test", boom)
    while desk.job(j)["state"] in ("queued", "running"):
        time.sleep(0.02)
    err = desk.job(j)
    assert err["state"] == "error" and err["error"] == {"type": "RuntimeError", "message": "no luck"}
    with pytest.raises(ApiError) as exc:
        desk.job("missing")
    assert exc.value.status == 404


def _get(url, headers=None, data=None, method=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_server_token_static_and_api(desk):
    httpd, base = serve_in_thread(desk, token="s3cret")
    try:
        assert _get(base + "/api/health")[0] == 401
        status, headers, body = _get(base + "/?token=s3cret")
        assert status == 200 and b"<!doctype html>" in body.lower() and "optlab_token=s3cret" in headers["Set-Cookie"]
        auth = {"X-Optlab-Token": "s3cret"}
        status, _, body = _get(base + "/api/health", auth)
        assert status == 200 and json.loads(body)["ok"]
        assert _get(base + "/api/health", {"Cookie": "optlab_token=s3cret"})[0] == 200
        assert _get(base + "/api/health", {"X-Optlab-Token": "wrong"})[0] == 401
        status, headers, _ = _get(base + "/js/main.js", auth)
        assert status == 200 and headers["Content-Type"].startswith("text/javascript")
        status, _, body = _get(base + "/../../api.py", auth)  # outside the static folder: the page, not the file
        assert status == 200 and b"class Desk" not in body
        status, _, body = _get(base + "/api/termsheet/form", auth, {"type": "nope"}, "POST")
        assert status == 422 and "unknown product type" in json.loads(body)["error"]
        status, _, body = _get(base + "/api/trades/NOPE-1/events", auth)
        assert status == 404
        status, _, body = _get(base + "/api/nothing", auth)
        assert status == 404
        status, _, body = _get(base + "/api/fixings", auth, method="POST", data=None)
        assert status == 422 and "no line" in json.loads(body)["error"]  # an empty body stores nothing
        status, headers, body = _get(base + "/report/book", auth)
        assert status == 200 and headers["Content-Type"].startswith("text/html")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_serve_refuses_the_network_without_a_token(desk):
    with pytest.raises(ValueError, match="token"):
        serve(desk, host="0.0.0.0", port=0)
