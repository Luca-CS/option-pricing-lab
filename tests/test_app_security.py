"""The desk refuses cross-site requests: a page open in the same browser must not drive or read it (A0 finding 1,
docs/architecture/security.md). No market data needed: the routes used here read or write the store only."""

import http.client
import json

import pytest

from optlab.app.api import Desk
from optlab.app.server import cross_site_refusal, serve_in_thread


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    desk = Desk(store=tmp_path_factory.mktemp("desk-sec") / "desk.sqlite")
    httpd, base = serve_in_thread(desk)
    yield desk, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()
    desk.jobs.shutdown()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


FIXING = json.dumps({"underlying": "SPX", "text": "2025-01-06 100"})


def test_a_cross_site_text_plain_post_writes_nothing(server):
    desk, port = server
    before = desk.fixings("SPX")
    status, _ = call(port, "POST", "/api/fixings", FIXING, {"Content-Type": "text/plain"})
    assert status == 415
    status, _ = call(
        port, "POST", "/api/fixings", FIXING, {"Content-Type": "application/json", "Origin": "https://evil.example"}
    )
    assert status == 403
    status, _ = call(port, "POST", "/api/fixings", FIXING, {"Content-Type": "application/json", "Origin": "null"})
    assert status == 403
    assert desk.fixings("SPX") == before


def test_the_desk_s_own_page_and_scripts_still_write(server):
    desk, port = server
    status, body = call(
        port, "POST", "/api/fixings", FIXING, {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"}
    )
    assert status == 200, body
    status, body = call(port, "POST", "/api/fixings", FIXING, {"Content-Type": "application/json; charset=utf-8"})
    assert status == 200, body  # no Origin: a script, not a browser


def test_a_rebound_host_cannot_read(server):
    _, port = server
    status, body = call(port, "GET", "/api/trades", headers={"Host": f"evil.example:{port}"})
    assert status == 403 and b"127.0.0.1" in body
    status, _ = call(port, "GET", "/", headers={"Host": f"attacker.localhost.evil:{port}"})
    assert status == 403
    for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
        assert call(port, "GET", "/api/trades", headers={"Host": host})[0] == 200


def test_the_rule_beyond_this_machine():
    # with a token the Host may be a LAN address or a tunnel's name; the Origin and content type rules stay
    h = {"Host": "192.168.1.20:8765"}
    assert cross_site_refusal("GET", h, None)[0] == 403
    assert cross_site_refusal("GET", h, "t") is None
    assert (
        cross_site_refusal(
            "POST",
            h | {"Origin": "http://192.168.1.20:8765", "Content-Type": "application/json", "Content-Length": "2"},
            "t",
        )
        is None
    )
    assert (
        cross_site_refusal("POST", h | {"Origin": "http://other:8765", "Content-Type": "application/json"}, "t")[0]
        == 403
    )
    assert cross_site_refusal("DELETE", {"Host": "127.0.0.1:8765", "Origin": "https://evil.example"}, None)[0] == 403
    assert cross_site_refusal("POST", {"Host": "127.0.0.1:1", "Content-Length": "x"}, None)[0] == 400


def test_no_url_reads_a_file_outside_the_static_folder(server):
    _, port = server
    status, page = call(port, "GET", "/")
    assert status == 200 and b"<html" in page.lower()
    status, body = call(port, "GET", "/js/main.js")
    assert status == 200 and b"import { api }" in body
    for path in (
        "/../server.py",
        "/js/../../server.py",
        "/%2e%2e/server.py",
        "/..%2fserver.py",
        "/C:/Windows/win.ini",
        "//etc/passwd",
        "/static/../../api.py",
    ):
        status, body = call(port, "GET", path)
        assert status == 200 and body == page, path  # an unknown path is the page, never another file
