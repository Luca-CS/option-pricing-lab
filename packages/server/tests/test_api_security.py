"""The security of API v1 (ADR-0020, docs/architecture/security.md): the guard (cross-site requests, rebound hosts,
content types, body size), sessions (cookie flags, CSRF, expiry, revocation, lockout and throttling), headers, and
errors that never show a stack trace. No market data needed: the routes used here read or write the store only."""

import datetime as dt
import secrets
from html.parser import HTMLParser
from typing import Any

import pytest
from _api import api

from optlab.server import security

FIXING: dict[str, Any] = {"underlying": "SPX", "text": "2025-01-06 100"}
PASSWORD = secrets.token_urlsafe(18)  # generated for the run, never written anywhere


@pytest.fixture(scope="module")
def a(tmp_path_factory):
    with api(tmp_path_factory.mktemp("api-sec"), login_max_failures=3, login_per_address=8) as a:
        yield a


@pytest.fixture(scope="module")
def trader(a):
    return a.client("trader")


def fixings(a):
    return a.state.desk.fixings("SPX")


# --- the guard ------------------------------------------------------------------------------------------------------
def test_a_cross_site_post_writes_nothing(a, trader):
    before = fixings(a)
    r = trader.post("/api/v1/trading/fixings", content=b'{"underlying": "SPX"}', headers={"content-type": "text/plain"})
    assert r.status_code == 415 and r.headers["content-type"] == "application/problem+json"
    for origin in ("https://evil.example", "null", "http://127.0.0.1.evil.example"):
        r = trader.post("/api/v1/trading/fixings", json=FIXING, headers={"origin": origin})
        assert r.status_code == 403, origin
    r = trader.delete("/api/v1/trading/fixings/SPX/2025-01-06", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert fixings(a) == before


def test_the_same_origin_and_scripts_still_write(a, trader):
    r = trader.post("/api/v1/trading/fixings", json=FIXING, headers={"origin": "http://127.0.0.1"})
    assert r.status_code == 200, r.text
    r = trader.post("/api/v1/trading/fixings", json=FIXING)  # no Origin: a script, not a browser
    assert r.status_code == 200, r.text
    r = trader.post(
        "/api/v1/trading/fixings",
        content=b'{"underlying": "SPX", "text": "2025-01-07 101"}',
        headers={"content-type": "application/json; charset=utf-8"},
    )
    assert r.status_code == 200, r.text


def test_a_rebound_host_cannot_read(a, trader):
    for host in ("evil.example", "attacker.localhost.evil", "127.0.0.1.evil.example", "192.168.1.20:8765"):
        r = trader.get("/api/v1/trading/trades", headers={"host": host})
        assert r.status_code == 403 and "127.0.0.1" in r.json()["detail"], host
        assert trader.get("/api/v1/health", headers={"host": host}).status_code == 403
    for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "127.0.0.1"):
        assert trader.get("/api/v1/trading/trades", headers={"host": host}).status_code == 200, host


def test_the_lan_profile_answers_on_its_names_only_and_keeps_the_origin_rule(tmp_path):
    names = "optlab.home.arpa,192.168.1.20"
    with api(tmp_path, lan=True, host="0.0.0.0", lan_names=names) as lan:
        c = lan.client("trader")
        h = {"host": "192.168.1.20:8765"}
        assert c.get("/api/v1/trading/trades", headers=h).status_code == 200
        assert c.get("/api/v1/trading/trades", headers={"host": "OPTLAB.home.arpa"}).status_code == 200
        for host in ("evil.example", "optlab.home.arpa.evil.example", "192.168.1.21:8765"):  # finding 17
            r = c.get("/api/v1/trading/trades", headers={"host": host})
            assert r.status_code == 403 and "LAN names" in r.json()["detail"], host
        assert c.get("/api/v1/trading/trades", headers={"host": "127.0.0.1:8765"}).status_code == 200
        r = c.post("/api/v1/trading/fixings", json=FIXING, headers=h | {"origin": "http://192.168.1.20:8765"})
        assert r.status_code == 200, r.text
        r = c.post("/api/v1/trading/fixings", json=FIXING, headers=h | {"origin": "http://other:8765"})
        assert r.status_code == 403


def test_the_body_size_is_bounded(a, trader):
    limit = a.state.settings.max_body_bytes
    big = b'{"underlying": "SPX", "text": "' + b"x" * limit + b'"}'
    r = trader.post("/api/v1/trading/fixings", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413

    def chunks():  # no Content-Length: the guard counts what arrives
        yield b'{"underlying": "SPX", "text": "'
        for _ in range(limit // 65536 + 2):
            yield b"x" * 65536
        yield b'"}'

    r = trader.post("/api/v1/trading/fixings", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    r = trader.post("/api/v1/trading/fixings", content=b"{}", headers={"content-length": "x"})
    assert r.status_code == 400


def test_unknown_fields_and_bounds_are_refused(trader):
    r = trader.post("/api/v1/trading/fixings", json={**FIXING, "admin": True})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "admin"
    r = trader.post("/api/v1/structuring/script/check", json={"script": "x" * 20_001})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "script"
    r = trader.get("/api/v1/trading/fixings", params={"underlying": "../../etc"})
    assert r.status_code == 422
    r = trader.get("/api/v1/trading/trades", params={"cursor": "not base64!"})
    assert r.status_code == 400


# --- headers and errors ---------------------------------------------------------------------------------------------
def test_security_headers(trader):
    r = trader.get("/api/v1/trading/trades")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
    assert h["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert h["cache-control"] == "no-store" and h["cross-origin-opener-policy"] == "same-origin"
    assert "server" not in h or "uvicorn" not in h["server"].lower()
    rep = trader.get("/api/v1/trading/book/report")
    assert rep.status_code == 200 and rep.headers["content-type"].startswith("text/html")
    assert "script-src" not in rep.headers["content-security-policy"] and "<script" not in rep.text.lower()
    refused = trader.get("/api/v1/health", headers={"host": "evil.example"})
    assert refused.headers["x-content-type-options"] == "nosniff"  # on the guard's own answers too


def test_a_crash_is_a_problem_without_a_trace(a, trader, monkeypatch):
    def boom(*args, **kw):
        raise RuntimeError(r"C:\Users\secret\path.py line 12")

    monkeypatch.setattr(a.state.desk, "trades", boom)
    r = trader.get("/api/v1/trading/trades")
    assert r.status_code == 500 and r.headers["content-type"] == "application/problem+json"
    assert r.json() == {
        "type": "about:blank",
        "title": "Internal error",
        "status": 500,
        "detail": "the server failed on this request; the log has the details",
    }
    assert "secret" not in r.text and "Traceback" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff" and "content-security-policy" in r.headers


def test_unknown_routes_and_methods_are_problems(trader):
    r = trader.get("/api/v1/nothing")
    assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json"
    assert trader.delete("/api/v1/health").status_code == 405
    assert trader.get("/api/v1/docs").status_code == 404  # Swagger UI loads a CDN script: off by default


# --- sessions -------------------------------------------------------------------------------------------------------
def _login(c, name, password=PASSWORD):
    return c.post("/api/v1/auth/login", json={"name": name, "password": password})


def test_login_cookie_csrf_and_logout(a):
    a.state.accounts.create("ana", PASSWORD, ["trader"])
    c = a.client()
    r = _login(c, "ana")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "optlab_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie  # plain HTTP on 127.0.0.1; Secure under TLS (the LAN profile)
    me = r.json()
    assert me["name"] == "ana" and me["roles"] == ["trader"] and "create_trade" in me["permissions"]
    assert "users" not in " ".join(me["permissions"]) and "var" not in me["permissions"]
    assert c.post("/api/v1/trading/fixings", json=FIXING).status_code == 403  # no CSRF header
    bad = c.post("/api/v1/trading/fixings", json=FIXING, headers={"X-CSRF-Token": "guess"})
    assert bad.status_code == 403
    ok = c.post("/api/v1/trading/fixings", json=FIXING, headers={"X-CSRF-Token": me["csrf_token"]})
    assert ok.status_code == 200
    assert c.get("/api/v1/auth/me").json()["csrf_token"] == me["csrf_token"]
    stolen = c.cookies.get("optlab_session")
    assert c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": me["csrf_token"]}).status_code == 200
    c.cookies.set("optlab_session", stolen)  # the old cookie, replayed after the logout
    assert c.get("/api/v1/auth/me").status_code == 401


def test_a_new_login_never_reuses_a_session(a):
    a.state.accounts.create("bea", PASSWORD, ["viewer"])
    c = a.client()
    _login(c, "bea")
    first = c.cookies.get("optlab_session")
    _login(c, "bea")
    assert c.cookies.get("optlab_session") != first
    assert a.state.accounts.session(first) is None


def test_wrong_passwords_lock_the_account_and_say_nothing_about_names(a):
    a.state.accounts.create("cyd", PASSWORD, ["viewer"])
    c = a.client()
    unknown = _login(c, "nobody-here", "whatever-it-is")
    wrong = _login(c, "cyd", "not-the-password")
    assert unknown.status_code == wrong.status_code == 401 and unknown.json()["detail"] == wrong.json()["detail"]
    _login(c, "cyd", "not-the-password-2")
    _login(c, "cyd", "not-the-password-3")  # the third failure locks it (login_max_failures=3 here)
    assert _login(c, "cyd").status_code == 423  # even the right password, for login_lockout_minutes
    row = a.state.accounts.get("cyd")
    assert row["locked_until"]
    entries = a.state.audit.entries(50)
    assert any(e["action"] == "login_failed" and e["target"] == "cyd" for e in entries)
    assert all(PASSWORD not in str(e) for e in entries)


def test_one_address_is_throttled(tmp_path):
    with api(tmp_path, login_per_address=5) as t:
        c = t.client()
        codes = [_login(c, f"x{i}", "whatever-it-is").status_code for i in range(6)]
        assert codes[:5] == [401] * 5 and codes[5] == 429


def test_a_burst_of_guesses_gets_no_more_than_the_same_guesses_in_a_row(tmp_path):
    """Found by the probe in A9: the count of failures was read, then written after the hash, so guesses sent together
    were all evaluated and counted as one; the account was not even locked afterwards."""
    import threading

    with api(tmp_path, login_max_failures=3, login_per_address=50) as t:
        t.state.accounts.create("eve", PASSWORD, ["viewer"])
        clients = [t.client() for _ in range(12)]
        codes: list[int] = []

        def guess(c, i):
            codes.append(_login(c, "eve", f"not-the-password-{i}").status_code)

        threads = [threading.Thread(target=guess, args=(c, i)) for i, c in enumerate(clients)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert codes.count(401) == 3 and codes.count(423) == 9, codes  # three evaluated, the others never hashed
        assert _login(clients[0], "eve").status_code == 423  # locked, even for the right password
        assert t.state.accounts.get("eve")["locked_until"]


def test_a_right_password_does_not_lift_a_lock_set_by_others_meanwhile(a, monkeypatch):
    """Found by the review of A9: a correct login cleared the lock that guesses sent during its hash had set."""
    accounts = a.state.accounts
    accounts.create("gil", PASSWORD, ["viewer"])
    real = accounts.hasher

    class Slow:  # while the right password is checked, guesses lock the account
        def verify(self, hash_, password):
            if password == PASSWORD:
                monkeypatch.setattr(accounts, "hasher", real)
                for i in range(a.state.settings.login_max_failures):
                    with pytest.raises(security.LoginError):
                        accounts._authenticate("gil", f"guess-{i}")
            return real.verify(hash_, password)

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(accounts, "hasher", Slow())
    assert accounts._authenticate("gil", PASSWORD).name == "gil"  # the right password signs in
    assert accounts.get("gil")["locked_until"]  # and the lock of the guesses stays
    with pytest.raises(security.LoginError) as err:
        accounts._authenticate("gil", PASSWORD)
    assert err.value.status == 423


def test_the_throttle_counts_every_failed_attempt_and_bounds_the_audit_log(tmp_path):
    with api(tmp_path, login_max_failures=3, login_per_address=6) as t:
        t.state.accounts.create("fay", PASSWORD, ["viewer"])
        c = t.client()
        assert _login(c, "fay").status_code == 200  # a success gives its attempt back
        codes = [_login(c, "fay", f"wrong-password-{i}").status_code for i in range(10)]
        assert codes == [401] * 3 + [423] * 3 + [429] * 4  # attempts on a locked account count too
        failed = [e for e in t.state.audit.entries(200) if e["action"] == "login_failed"]
        assert len(failed) == 6  # the throttled ones write nothing: an address cannot fill the log
        assert _login(c, "fay").status_code == 429


def test_no_nan_nor_infinity_reaches_the_desk(a, trader):
    """Found in A9: Python's JSON reads NaN and Infinity, a NaN passes every bound, and fixings took any level."""
    before = fixings(a)
    json_ = {"content-type": "application/json"}
    for raw in (
        b'{"underlying": "SPX", "levels": {"2025-01-06": NaN}}',
        b'{"underlying": "SPX", "levels": {"2025-01-06": Infinity}}',
        b'{"underlying": "SPX", "text": "2025-01-06 1", "real_spot": NaN}',
    ):
        assert trader.post("/api/v1/trading/fixings", content=raw, headers=json_).status_code == 422, raw
    for levels in ({"2025-01-06": -5.0}, {"2025-01-06": 0.0}):
        r = trader.post("/api/v1/trading/fixings", json={"underlying": "SPX", "levels": levels})
        assert r.status_code == 422 and "positive" in r.json()["detail"], levels
    r = trader.post(
        "/api/v1/trading/fixings", json={"underlying": "SPX", "text": "2025-01-06 inf" + chr(10) + "2025-01-07 nan"}
    )
    assert r.status_code == 422  # both lines skipped: nothing left to store
    s = a.client("structurer")
    r = s.post("/api/v1/structuring/script/price", content=b'{"script": "x", "seed": NaN}', headers=json_)
    assert r.status_code == 422
    assert fixings(a) == before


def test_the_guard_reads_a_request_one_way(a, trader):
    """Found by the probe in A9: a chunked body without a type passed the content-type rule, a request could carry
    both a length and a chunked encoding, and a Host with user info was read as its host part."""
    before = fixings(a)
    r = trader.post("/api/v1/trading/fixings", content=iter([b'{"underlying": "SPX", "text": "2025-01-08 1"}']))
    assert r.status_code == 415, r.text
    both = {"content-type": "application/json", "transfer-encoding": "chunked", "content-length": "2"}
    r = trader.post("/api/v1/trading/fixings", content=b"{}", headers=both)
    assert r.status_code == 400 and "not both" in r.json()["detail"]
    assert trader.get("/api/v1/health", headers={**both, "content-type": ""}).status_code == 400
    for host in ("evil@127.0.0.1", "127.0.0.1:8765@evil.example", "127.0.0.1 evil.example", "127.0.0.1/evil", ""):
        r = trader.get("/api/v1/trading/trades", headers={"host": host})
        assert r.status_code == 403, host
        assert r.headers["cache-control"] == "no-store" and r.headers["content-type"] == "application/problem+json"
    assert fixings(a) == before


def test_sessions_expire_when_idle_or_old(a, monkeypatch):
    a.state.accounts.create("dan", PASSWORD, ["viewer"])
    c = a.client()
    _login(c, "dan")
    assert c.get("/api/v1/auth/me").status_code == 200
    now = dt.datetime.now(dt.UTC)
    idle = a.state.settings.session_idle_minutes
    monkeypatch.setattr(security, "_now", lambda: now + dt.timedelta(minutes=idle + 1))
    assert c.get("/api/v1/auth/me").status_code == 401
    monkeypatch.setattr(security, "_now", lambda: now + dt.timedelta(hours=a.state.settings.session_absolute_hours))
    assert c.get("/api/v1/auth/me").status_code == 401


def test_changing_an_account_ends_its_sessions(a):
    a.state.accounts.create("eve", PASSWORD, ["trader"])
    c = a.client()
    _login(c, "eve")
    a.state.accounts.set_roles("eve", ["viewer"])
    assert c.get("/api/v1/auth/me").status_code == 401
    _login(c, "eve")
    assert c.get("/api/v1/auth/me").json()["roles"] == ["viewer"]
    a.state.accounts.set_disabled("eve", True)
    assert c.get("/api/v1/auth/me").status_code == 401
    assert _login(c, "eve").status_code == 401


def test_passwords_are_argon2id_and_sessions_stored_as_hashes(a):
    a.state.accounts.create("fay", PASSWORD, ["viewer"])
    from sqlalchemy import select

    from optlab.server.persistence.schema import sessions, users

    c = a.client()
    _login(c, "fay")
    token = c.cookies.get("optlab_session")
    with a.state.desk.db.read() as conn:
        h = conn.execute(select(users.c.password_hash).where(users.c.name == "fay")).scalar_one()
        stored = [r.session_hash for r in conn.execute(select(sessions.c.session_hash))]
    assert h.startswith("$argon2id$") and PASSWORD not in h
    assert token not in stored and security._digest(token) in stored
    with pytest.raises(ValueError):
        a.state.accounts.create("gus", "short", ["viewer"])


def test_someone_else_s_job_does_not_exist(a):
    alice, bob, admin = a.client("structurer"), a.client("structurer"), a.client("admin")
    from _tasks import submit_as

    job = submit_as(a.state.desk, a.user_id(alice), "test_answer", {"answer": "ok"})
    assert alice.get(f"/api/v1/jobs/{job}").status_code == 200
    assert bob.get(f"/api/v1/jobs/{job}").status_code == 404
    assert bob.post(f"/api/v1/jobs/{job}/cancel").status_code == 404
    assert admin.get(f"/api/v1/jobs/{job}").status_code == 200


def test_a_frontend_folder_serves_its_files_only(tmp_path):
    front = tmp_path / "front"
    front.mkdir()
    (front / "index.html").write_text("<!doctype html><html><body>front</body></html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for you", encoding="utf-8")
    with api(tmp_path / "data", frontend=front) as f:
        c = f.client()
        r = c.get("/")
        assert r.status_code == 200 and "front" in r.text
        assert r.headers["content-security-policy"].startswith("default-src 'self'")
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "//secret.txt"):
            r = c.get(path)
            assert "not for you" not in r.text, path


class _Scripts(HTMLParser):
    """The text of every inline script of a page (an HTML parser, not a pattern: tags in any case or spacing)."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.inline: list[str] = []
        self._open = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and not dict(attrs).get("src"):
            self._open = True
            self.inline.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._open = False

    def handle_data(self, data):
        if self._open:
            self.inline[-1] += data


def test_the_docs_page_is_off_unless_asked_and_pinned_when_on(tmp_path):
    import base64
    import hashlib

    with api(tmp_path, docs=True) as d:
        r = d.client().get("/api/v1/docs")
        assert r.status_code == 200
        csp = r.headers["content-security-policy"]
        scripts = _Scripts()
        scripts.feed(r.text)
        assert len(scripts.inline) == 1
        inline = scripts.inline[0]
        digest = base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode()
        assert (
            f"'sha256-{digest}'" in csp and "unsafe-eval" not in csp and "script-src https://cdn.jsdelivr.net " in csp
        )
        assert r.text.count('integrity="sha384-') == 2 and "swagger-ui-dist@5." in r.text and "@5/" not in r.text
