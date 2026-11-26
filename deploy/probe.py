"""Probe a running desk from the outside, the way a hostile neighbour on the network would (security.md).

    OPTLAB_PROBE_PASSWORD=... python deploy/probe.py --url http://127.0.0.1:8765 --admin <name> [--only host,csrf]

Standard library only, raw HTTP where it matters (a missing Host, a chunked body without a type, both a length and a
chunked encoding, bursts of concurrent logins). It needs an admin account of the instance under test (a test account
with a generated password, read from the environment and never printed); it creates its own users for the roles and
disables them at the end. It only ever writes fixings of a made-up name and its own accounts, but it does fail logins
on purpose: run it against a test instance, not the real one. One line per check; exit code 1 when one fails.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import socket
import statistics
import sys
import threading
import time
from urllib.parse import urlsplit

COOKIE = "optlab_session"
RESULTS: list[tuple[str, str, bool, str]] = []
PROBE_NAME = "ZZPROBE"  # the underlying whose fixings the probe writes


class Target:
    def __init__(self, url: str):
        u = urlsplit(url)
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 80
        self.netloc = f"{self.host}:{self.port}"

    def request(self, method, path, body=None, headers=None, cookie=None, csrf=None, host=None, timeout=30.0):
        """(status, headers as a lower-case dict, body bytes, set-cookie list) of one request on its own connection;
        status 0 when the server closed the connection without an answer the client could read."""
        h = {"Host": host if host is not None else self.netloc, "Connection": "close", **(headers or {})}
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        if cookie:
            h["Cookie"] = f"{COOKIE}={cookie}"
        if csrf:
            h["X-CSRF-Token"] = csrf
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if body is not None and "Content-Length" not in h and "Transfer-Encoding" not in h:
                h["Content-Length"] = str(len(body))
            for k, v in h.items():
                conn.putheader(k, v)
            try:
                conn.endheaders(body)
                r = conn.getresponse()
                data = r.read()
            except (
                ConnectionError,
                TimeoutError,
            ):  # refused while the body was still being sent (a relay may hold the answer)
                return 0, {}, b"", []
            return r.status, {k.lower(): v for k, v in r.getheaders()}, data, r.msg.get_all("set-cookie") or []
        finally:
            conn.close()

    def raw(self, payload: bytes, timeout=10.0) -> bytes:
        """Bytes sent as they are on a socket; what comes back until the server closes or goes quiet."""
        s = socket.create_connection((self.host, self.port), timeout=timeout)
        out = b""
        try:
            s.sendall(payload)
            while chunk := s.recv(65536):
                out += chunk
        except TimeoutError, ConnectionError:  # the server may answer and close before the payload is all sent
            pass
        finally:
            s.close()
        return out


class Session:
    def __init__(self, t: Target, name: str, password: str):
        self.t, self.name = t, name
        status, _, body, cookies = t.request("POST", "/api/v1/auth/login", {"name": name, "password": password})
        if status != 200:
            raise SystemExit(f"cannot sign in as {name}: {status} {body[:200]!r}")
        self.set_cookie = next(c for c in cookies if c.startswith(COOKIE + "="))
        self.cookie = self.set_cookie.split(";")[0].split("=", 1)[1]
        self.csrf = json.loads(body)["csrf_token"]

    def call(self, method, path, body=None, headers=None, csrf=True, **kw):
        return self.t.request(method, path, body, headers, cookie=self.cookie, csrf=self.csrf if csrf else None, **kw)


def check(group: str, what: str, ok: bool, got: object = "") -> bool:
    RESULTS.append((group, what, bool(ok), str(got)[:140]))
    print(f"{'PASS' if ok else 'FAIL'}  [{group}] {what}" + (f"  -> {str(got)[:140]}" if got != "" else ""))
    return bool(ok)


def status_line(raw: bytes) -> str:
    return raw.split(b"\r\n", 1)[0].decode("latin-1") if raw else "(connection closed, nothing sent back)"


def fill(path: str) -> str:
    """A path of the OpenAPI document with made-up ids in its parameters."""
    out = []
    for part in path.split("/"):
        if part.startswith("{"):
            name = part.strip("{}").split(":")[0]
            part = "2025-01-06" if name == "date" else PROBE_NAME if name in ("underlying", "name") else "probe-x"
        out.append(part)
    return "/".join(out)


# --- the checks ---------------------------------------------------------------------------------------------------------
def host_checks(t: Target, admin: Session) -> None:
    g = "host"
    for bad in (
        "evil.example",
        f"{t.host}.evil.example",
        "localhost.",
        "192.168.1.20:8765",
        "0.0.0.0",  # noqa: S104 (a Host header the probe sends, not an address it binds)
        f"evil@{t.host}",
    ):
        s, _, _, _ = t.request("GET", "/api/v1/health", host=bad)
        check(g, f"Host: {bad} is refused", s in (400, 403), s)
    s, _, _, _ = admin.call("GET", "/api/v1/trading/trades", host="evil.example")
    check(g, "a signed-in read under a rebound Host is refused", s == 403, s)
    raw = t.raw(b"GET /api/v1/health HTTP/1.0\r\n\r\n")
    check(g, "a request without a Host header is refused", b" 200 " not in raw.split(b"\r\n", 1)[0], status_line(raw))
    raw = t.raw(
        f"GET http://evil.example/api/v1/health HTTP/1.1\r\nHost: {t.netloc}\r\nConnection: close\r\n\r\n".encode()
    )
    check(g, "an absolute request target does not change the checked Host", True, status_line(raw))
    raw = t.raw(
        f"GET /api/v1/health HTTP/1.1\r\nHost: {t.netloc}\r\nHost: evil.example\r\nConnection: close\r\n\r\n".encode()
    )
    check(g, "two Host headers are refused", b" 200 " not in raw.split(b"\r\n", 1)[0], status_line(raw))


def anonymous_walk(t: Target, doc: dict) -> None:
    g = "anonymous"
    leaks = []
    n = 0
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete") or "public" in (op.get("x-roles") or []):
                continue
            n += 1
            body = {} if method != "get" else None
            s, _, _, _ = t.request(method.upper(), fill(path), body)
            if s != 401:
                leaks.append(f"{method.upper()} {path} {s}")
    check(g, f"every one of the {n} non-public operations answers 401 without a session", not leaks, leaks[:4] or "")
    s, _, body, _ = t.request("GET", "/api/v1/docs")
    check(g, "the Swagger page is off", s == 404, s)
    for cookie in ("x", "A" * 5000, "../../etc/passwd", "%00", secrets.token_urlsafe(32)):
        s, _, _, _ = t.request("GET", "/api/v1/auth/me", cookie=cookie)
        if s != 401:
            leaks.append(f"cookie {cookie[:12]!r} {s}")
    check(g, "garbage, oversized and guessed cookies are 401, never a 500", not leaks, leaks[:4] or "")


def csrf_checks(t: Target, doc: dict, trader: Session, other: Session) -> None:
    g = "csrf"
    fixing = {"underlying": PROBE_NAME, "text": "2019-01-07 100"}  # before any desk's valuation date, a demo's included
    path = "/api/v1/trading/fixings"
    s, _, _, _ = trader.call("POST", path, fixing)
    check(g, "the legitimate write passes (control)", s == 200, s)
    for origin in ("https://evil.example", "null", f"http://{t.host}.evil.example", f"http://evil.example@{t.netloc}"):
        s, _, _, _ = trader.call("POST", path, fixing, headers={"Origin": origin})
        check(g, f"Origin: {origin} is refused with a valid session and token", s == 403, s)
    s, _, _, _ = trader.call("POST", path, fixing, csrf=False)
    check(g, "a write without the CSRF header is refused", s == 403, s)
    s, _, _, _ = t.request("POST", path, fixing, cookie=trader.cookie, csrf=other.csrf)
    check(g, "another session's CSRF token is refused", s == 403, s)
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", ""):
        headers = {"Content-Type": ctype} if ctype else {"Content-Type": ""}
        s, _, _, _ = trader.call("POST", path, json.dumps(fixing).encode(), headers=headers)
        check(g, f"a body typed {ctype or '(empty)'!r} is refused", s == 415, s)
    raw = json.dumps(fixing).encode()
    chunked = f"{len(raw):x}\r\n".encode() + raw + b"\r\n0\r\n\r\n"
    request = (
        f"POST {path} HTTP/1.1\r\nHost: {t.netloc}\r\nCookie: {COOKIE}={trader.cookie}\r\n"
        f"X-CSRF-Token: {trader.csrf}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    ).encode() + chunked
    got = status_line(t.raw(request))
    check(g, "a chunked body without any Content-Type is refused (415)", " 415 " in got, got)
    missing = []
    for p, item in doc["paths"].items():
        for method, op in item.items():
            if method in ("post", "put", "patch", "delete") and "public" not in (op.get("x-roles") or []):
                s, _, _, _ = t.request(method.upper(), fill(p), {}, cookie=trader.cookie)
                if s != 403:
                    missing.append(f"{method.upper()} {p} {s}")
    check(g, "every state-changing operation refuses a session without its token", not missing, missing[:4] or "")
    s, _, _, _ = trader.call("GET", f"{path}?underlying={PROBE_NAME}", headers={"X-HTTP-Method-Override": "DELETE"})
    check(g, "a method-override header on a GET changes nothing", s == 200, s)


def session_checks(t: Target, admin: Session, users: dict) -> None:
    g = "session"
    name, password = users["viewer"]
    s = Session(t, name, password)
    flags = s.set_cookie.lower()
    check(
        g,
        "the cookie is HttpOnly, SameSite=Strict, Path=/",
        all(f in flags for f in ("httponly", "samesite=strict", "path=/")),
        s.set_cookie.split(";", 1)[1],
    )
    planted = secrets.token_urlsafe(32)
    st, _, body, cookies = t.request("POST", "/api/v1/auth/login", {"name": name, "password": password}, cookie=planted)
    fresh = next(c for c in cookies if c.startswith(COOKIE + "=")).split(";")[0].split("=", 1)[1]
    check(g, "a cookie planted before the login is not adopted (fixation)", st == 200 and fresh != planted, st)
    st, _, _, _ = t.request("GET", "/api/v1/auth/me", cookie=planted)
    check(g, "the planted value opens nothing", st == 401, st)
    s.call("POST", "/api/v1/auth/logout", {})
    st, _, _, _ = s.call("GET", "/api/v1/auth/me")
    check(g, "a cookie replayed after its logout is refused", st == 401, st)
    again = Session(t, name, password)
    admin.call("PATCH", f"/api/v1/admin/users/{name}", {"roles": ["viewer", "quant"]})
    st, _, _, _ = again.call("GET", "/api/v1/auth/me")
    check(g, "a change of roles ends the user's sessions", st == 401, st)
    st, _, _, _ = t.request("GET", "/api/v1/auth/me", cookie=again.cookie.upper())
    check(g, "session ids are case-sensitive secrets", st == 401, st)


def roles_checks(t: Target, doc: dict, sessions: dict) -> None:
    g = "roles"
    for role, s in sessions.items():
        wrong = []
        n = 0
        for path, item in doc["paths"].items():
            for method, op in item.items():
                allowed = op.get("x-roles") if isinstance(op, dict) else None
                if method not in ("get", "post", "put", "patch", "delete") or not allowed or "public" in allowed:
                    continue
                if role in allowed or "viewer" in allowed:
                    continue
                n += 1
                st, _, _, _ = s.call(method.upper(), fill(path), {} if method != "get" else None)
                if st != 403:
                    wrong.append(f"{method.upper()} {path} {st}")
        check(
            g,
            f"a {role} is refused (403) on each of the {n} operations its role does not name",
            not wrong,
            wrong[:4] or "",
        )
    viewer, structurer = sessions["viewer"], sessions["structurer"]
    script = "underlying SPX\nevent 2030-01-07 pay 2030-01-14\n  pay 100 as redemption\nend\n"
    st, _, body, _ = structurer.call("POST", "/api/v1/structuring/script/price", {"script": script, "n_paths": 1000})
    if check(g, "a structurer may submit a job (control)", st == 202, st):
        job = json.loads(body)["job_id"]
        seen = [viewer.call("GET", f"/api/v1/jobs/{job}")[0], viewer.call("POST", f"/api/v1/jobs/{job}/cancel", {})[0]]
        seen.append(viewer.call("GET", f"/api/v1/jobs/{job}/events")[0])
        check(
            g,
            "someone else's job does not exist for another user (read, cancel, events)",
            seen == [404, 404, 404],
            seen,
        )
        structurer.call("POST", f"/api/v1/jobs/{job}/cancel", {})


def login_checks(t: Target, users: dict, max_failures: int) -> None:
    g = "login"
    name, _ = users["quant"]

    def attempt(n, p):
        t0 = time.perf_counter()
        s, _, body, _ = t.request("POST", "/api/v1/auth/login", {"name": n, "password": p})
        return s, body, time.perf_counter() - t0

    known = [attempt(name, secrets.token_urlsafe(16)) for _ in range(2)]
    unknown = [attempt("nobody" + secrets.token_hex(3), secrets.token_urlsafe(16)) for _ in range(2)]
    same = {(s, json.loads(b)["detail"]) for s, b, _ in known + unknown}
    check(g, "a wrong password and an unknown name get the same answer", len(same) == 1, same)
    a, b = statistics.median(x[2] for x in known), statistics.median(x[2] for x in unknown)
    check(
        g,
        "and take about the same time (an unknown name costs a hash too)",
        0.5 < a / b < 2.0,
        f"{1e3 * a:.0f} ms vs {1e3 * b:.0f} ms",
    )
    s, _, _ = attempt(name, "x" * 2000)
    check(g, "a password of 2,000 characters is refused before any hash", s == 422, s)
    # the lockout under a burst: `max_failures` wrong passwords lock the account, however they arrive
    victim, good = users["risk"]
    out: list[int] = []
    lock = threading.Lock()

    def guess():
        s, _, _ = attempt(victim, secrets.token_urlsafe(16))
        with lock:
            out.append(s)

    burst = [threading.Thread(target=guess) for _ in range(max_failures * 3)]
    for th in burst:
        th.start()
    for th in burst:
        th.join()
    evaluated = out.count(401)
    check(
        g,
        f"a burst of {len(burst)} concurrent guesses gets at most {max_failures} evaluated",
        evaluated <= max_failures,
        f"{evaluated} answered 401, {out.count(423)} locked, {out.count(429)} throttled",
    )
    s, _, _ = attempt(victim, good)
    check(g, "the account is locked after the burst, even for its real password", s in (423, 429), s)


def input_checks(t: Target, trader: Session, structurer: Session, limit: int) -> None:
    g = "input"
    path = "/api/v1/trading/fixings"
    big = json.dumps({"underlying": PROBE_NAME, "text": "2025-01-06 100\n" * (limit // 10)}).encode()
    s, _, _, _ = trader.call("POST", path, big)
    check(g, f"a body of {len(big):,} bytes is refused (413, or the connection closed)", s in (413, 0), s)
    head = (
        f"POST {path} HTTP/1.1\r\nHost: {t.netloc}\r\nCookie: {COOKIE}={trader.cookie}\r\nX-CSRF-Token: {trader.csrf}\r\n"
        "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    ).encode()
    got = status_line(t.raw(head + f"{len(big):x}\r\n".encode() + big + b"\r\n0\r\n\r\n"))
    check(g, "the same body sent chunked, without a length, is refused too", " 413 " in got or got.startswith("("), got)
    smuggle = head.replace(b"Transfer-Encoding: chunked\r\n", b"Transfer-Encoding: chunked\r\nContent-Length: 4\r\n")
    got = status_line(t.raw(smuggle + b"0\r\n\r\nGET /api/v1/health HTTP/1.1\r\nHost: evil.example\r\n\r\n"))
    check(g, "a request with both a length and a chunked encoding is refused", " 400 " in got, got)
    deep = ("[" * 200_000 + "]" * 200_000).encode()
    s, _, body, _ = trader.call("POST", path, deep, headers={"Content-Type": "application/json"})
    check(g, "JSON nested 200,000 deep is a client error, not a crash", s in (400, 422), s)
    nested = (
        "underlying SPX\nevent 2030-01-07 pay 2030-01-14\n  pay "
        + "(" * 9000
        + "1"
        + ")" * 9000
        + " as redemption\nend\n"
    )
    s, _, body, _ = structurer.call("POST", "/api/v1/structuring/script/check", {"script": nested})
    check(g, "a script nested 9,000 deep is refused cleanly, not a 500", s in (200, 422), f"{s} {body[:80]!r}")
    wide = "underlying SPX\nevent t=1 pay t=1\n  pay variance(SPX, t=0, t=1, n=10000000) as redemption\nend\n"
    s, _, body, _ = structurer.call("POST", "/api/v1/structuring/script/check", {"script": wide})
    check(g, "a script observing ten million dates is refused at the check", s == 200 and not json.loads(body)["ok"], s)
    s, _, _, _ = structurer.call("POST", "/api/v1/structuring/script/price", {"script": wide, "n_paths": 1_000})
    check(g, "and its price is refused before a job is queued", s == 422, s)
    for bad in ("..%2f..%2fetc%2fpasswd", "%2e%2e%2f%2e%2e", "..\\..\\x", "a/../../b", "%00", "x" * 300):
        s, _, _, _ = trader.call("GET", f"/api/v1/quant/replay/runs/{bad}")
        ok = s in (400, 404, 422)
        s2, _, _, _ = trader.call("GET", f"/api/v1/market/snapshots/{bad}/smile")
        check(g, f"the id {bad[:24]!r} reaches no file (replay run, snapshot)", ok and s2 in (400, 404, 422), (s, s2))
    s, _, body, _ = trader.call(
        "GET", "/api/v1/trading/trades?book=%27%20OR%201%3D1--&status=live%27%3B%20DROP%20TABLE%20trades%3B--"
    )
    check(g, "SQL in a filter is a filter value", s == 200 and json.loads(body)["items"] == [], s)
    s, _, _, _ = trader.call("POST", path, {"underlying": PROBE_NAME, "text": "x", "admin": True})
    check(g, "an unknown field is refused", s == 422, s)
    s, _, _, _ = structurer.call("POST", "/api/v1/structuring/script/price", {"script": "x", "n_paths": 10**12})
    check(g, "a costly parameter beyond its bound is refused", s == 422, s)
    got = status_line(
        t.raw(f"GET /api/v1/health HTTP/1.1\r\nHost: {t.netloc}\r\nX-Big: {'a' * 200_000}\r\n\r\n".encode())
    )
    check(g, "a header of 200,000 bytes is refused", " 200 " not in got, got)


def header_checks(t: Target, trader: Session) -> None:
    g = "headers"
    wanted = {
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "cache-control": "no-store",
        "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    }
    cases = {
        "200": trader.call("GET", "/api/v1/trading/trades"),
        "401": t.request("GET", "/api/v1/trading/trades"),
        "403": t.request("GET", "/api/v1/health", host="evil.example"),
        "404": trader.call("GET", "/api/v1/nothing-here"),
        "405": trader.call("PUT", "/api/v1/health", {}),
        "415": trader.call("POST", "/api/v1/trading/fixings", b"x", headers={"Content-Type": "text/plain"}),
        "422": trader.call("POST", "/api/v1/trading/fixings", {"nope": 1}),
    }
    for label, (s, h, body, _) in cases.items():
        missing = [k for k, v in wanted.items() if h.get(k) != v]
        check(g, f"a {label} carries the security headers", str(s) == label and not missing, missing or s)
        if label != "200":
            text = body.decode("utf-8", "replace")
            trace = any(w in text for w in ("Traceback", 'File "', "site-packages", ".py"))
            check(
                g,
                f"a {label} is problem+json without a trace or a path",
                h.get("content-type") == "application/problem+json" and not trace,
                h.get("content-type"),
            )
    s, h, _, _ = t.request("GET", "/api/v1/health")
    check(g, "no Server header names the software", "server" not in h, h.get("server", ""))
    s, h, body, _ = t.request(
        "OPTIONS",
        "/api/v1/trading/trades",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    check(g, "no CORS: a preflight from another origin gets no allow header", "access-control-allow-origin" not in h, s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--admin", required=True, help="an admin account of the instance (password: OPTLAB_PROBE_PASSWORD)")
    ap.add_argument(
        "--only", default="", help="groups, comma-separated: host,anonymous,csrf,session,roles,login,input,headers"
    )
    ap.add_argument("--max-failures", type=int, default=5, help="the instance's OPTLAB_LOGIN_MAX_FAILURES")
    ap.add_argument("--max-body", type=int, default=2_000_000, help="the instance's OPTLAB_MAX_BODY_BYTES")
    args = ap.parse_args()
    password = os.environ.get("OPTLAB_PROBE_PASSWORD") or sys.stdin.readline().rstrip("\n")
    only = {g for g in args.only.split(",") if g}
    t = Target(args.url)
    admin = Session(t, args.admin, password)
    doc = json.loads(t.request("GET", "/api/v1/openapi.json")[2])
    users: dict[str, tuple[str, str]] = {}
    for role in ("viewer", "trader", "structurer", "risk", "quant"):
        name, pw = f"probe-{role}-{secrets.token_hex(3)}", secrets.token_urlsafe(18)
        s, _, body, _ = admin.call("POST", "/api/v1/admin/users", {"name": name, "password": pw, "roles": [role]})
        if s != 201:
            raise SystemExit(f"cannot create {name}: {s} {body[:200]!r}")
        users[role] = (name, pw)
    sessions = {role: Session(t, *users[role]) for role in ("viewer", "trader", "structurer")}
    groups = {
        "host": lambda: host_checks(t, admin),
        "anonymous": lambda: anonymous_walk(t, doc),
        "csrf": lambda: csrf_checks(t, doc, sessions["trader"], sessions["structurer"]),
        "roles": lambda: roles_checks(t, doc, sessions),
        "input": lambda: input_checks(t, sessions["trader"], sessions["structurer"], args.max_body),
        "headers": lambda: header_checks(t, sessions["trader"]),
        "session": lambda: session_checks(t, admin, users),
        "login": lambda: login_checks(t, users, args.max_failures),  # last: it throttles this address
    }
    try:
        for name, run in groups.items():
            if not only or name in only:
                run()
    finally:
        for name, _ in users.values():
            admin.call("PATCH", f"/api/v1/admin/users/{name}", {"disabled": True})
        admin.call("DELETE", f"/api/v1/trading/fixings/{PROBE_NAME}/2025-01-06")
    failed = [r for r in RESULTS if not r[2]]
    print(
        f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed"
        + (f"; {len(failed)} FAILED" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
