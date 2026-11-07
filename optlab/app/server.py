"""HTTP server of the desk: the page (static files) and the JSON API on one origin, standard library only.

By default it listens on 127.0.0.1. To share it (LAN, or a tunnel such as Tailscale or Cloudflare Tunnel pointed at
the port), start it with a token: every request then needs it, as `?token=` once (it is kept in a cookie) or in the
`X-Optlab-Token` header.

Cross-site requests are refused, so that a web page open in the same browser cannot use the desk: without a token the
`Host` must be a loopback name (a page that rebinds its DNS name to 127.0.0.1 is turned away), a request that writes
must come from the desk's own origin when it says where it comes from (`Origin`), and a body must be declared as
`application/json` (a cross-site form or `text/plain` post is not read).
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

from ..log import get_logger
from .api import ApiError, Desk
from .report import book_report, desk_snapshot, quote_report, risk_report
from .risk_api import risk_routes

STATIC = Path(__file__).resolve().parent / "static"
LOG = get_logger(__name__)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _hostname(netloc: str) -> str | None:
    try:
        return urlsplit(f"//{netloc}").hostname
    except ValueError:
        return None


def cross_site_refusal(method: str, headers, token: str | None) -> tuple[int, str] | None:
    """(status, reason) when a request must be refused as cross-site, else None. Clients without a browser (scripts,
    tests) send no `Origin` and are not affected."""
    host = headers.get("Host") or ""
    if not token and _hostname(host) not in LOOPBACK:
        return 403, "this desk answers on 127.0.0.1 or localhost only"
    if method not in ("GET", "HEAD"):
        origin = headers.get("Origin")
        if origin is not None and (origin == "null" or urlsplit(origin).netloc.lower() != host.lower()):
            return 403, "cross-site request refused"
        try:
            length = int(headers.get("Content-Length") or 0)
        except ValueError:
            return 400, "bad Content-Length"
        ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if method in ("POST", "PUT") and (length or ctype) and ctype != "application/json":
            return 415, "the body must be application/json"
    return None


def routes(desk: Desk):
    """(method, regex, handler(match, query, body)) in order."""
    q1 = lambda q, k, d=None: q.get(k, [d])[0]  # noqa: E731
    return [
        ("GET", r"/api/health", lambda m, q, b: desk.health()),
        ("GET", r"/api/context", lambda m, q, b: desk.context()),
        ("GET", r"/api/presets", lambda m, q, b: desk.presets()),
        ("GET", r"/api/schema", lambda m, q, b: desk.schema()),
        ("POST", r"/api/termsheet/form", lambda m, q, b: desk.form(b)),
        ("POST", r"/api/quote", lambda m, q, b: desk.quote(b)),
        ("GET", r"/api/quotes", lambda m, q, b: desk.quotes()),
        ("GET", r"/api/quotes/(\w+)", lambda m, q, b: desk.saved_quote(m[1])),
        ("GET", r"/api/jobs/(\w+)", lambda m, q, b: desk.job(m[1])),
        ("DELETE", r"/api/jobs/(\w+)", lambda m, q, b: desk.cancel(m[1])),
        ("GET", r"/api/trades", lambda m, q, b: desk.trades()),
        ("GET", r"/api/trades/([\w-]+)", lambda m, q, b: desk.trade(m[1])),
        ("POST", r"/api/trades/([\w-]+)/value", lambda m, q, b: desk.value_trade(m[1], b)),
        ("POST", r"/api/trades", lambda m, q, b: desk.create_trade(b)),
        ("PUT", r"/api/trades/([\w-]+)", lambda m, q, b: desk.update_trade(m[1], b)),
        ("DELETE", r"/api/trades/([\w-]+)", lambda m, q, b: desk.delete_trade(m[1])),
        ("POST", r"/api/trades/([\w-]+)/restore", lambda m, q, b: desk.restore_trade(m[1])),
        ("GET", r"/api/trades/([\w-]+)/events", lambda m, q, b: desk.trade_events(m[1])),
        ("POST", r"/api/book/price", lambda m, q, b: desk.price_book(b)),
        ("GET", r"/api/upcoming", lambda m, q, b: desk.upcoming(int(q1(q, "days", 45)))),
        ("GET", r"/api/fixings", lambda m, q, b: desk.fixings(q1(q, "underlying"))),
        ("POST", r"/api/fixings", lambda m, q, b: desk.put_fixings(b)),
        ("DELETE", r"/api/fixings/(\w+)/([\d-]+)", lambda m, q, b: desk.delete_fixing(m[1], m[2])),
        ("GET", r"/api/market/smile", lambda m, q, b: desk.smile(float(q1(q, "T", 1.0)))),
        ("GET", r"/api/market/term", lambda m, q, b: desk.term_structure()),
        ("GET", r"/api/market/risk", lambda m, q, b: desk.model_risk()),
        ("GET", r"/api/market/snapshots", lambda m, q, b: desk.snapshots()),
        ("POST", r"/api/market/use", lambda m, q, b: desk.use_snapshot(b)),
        ("GET", r"/api/data/overview", lambda m, q, b: desk.data_overview()),
        ("GET", r"/api/data/vol", lambda m, q, b: desk.data_vol_index(q1(q, "name", "VIX"), q1(q, "start"))),
        ("GET", r"/api/data/baskets", lambda m, q, b: desk.data_baskets()),
        ("POST", r"/api/data/market", lambda m, q, b: desk.market_from_lake(b)),
        ("POST", r"/api/data/fixings", lambda m, q, b: desk.fixings_from_lake(b)),
        ("GET", r"/api/vollab/days", lambda m, q, b: desk.lab_days()),
        ("GET", r"/api/vollab/quotes", lambda m, q, b: desk.lab_quotes(q1(q, "underlying", "SPX"), q1(q, "day", ""), q1(q, "source"))),
        ("POST", r"/api/vollab/fit", lambda m, q, b: desk.lab_fit(b)),
        ("GET", r"/api/vollab/runs", lambda m, q, b: desk.lab_runs(q1(q, "underlying"))),
        ("POST", r"/api/vollab/runs/([\w-]+)/reproduce", lambda m, q, b: desk.lab_reproduce(m[1])),
        ("GET", r"/api/replay/runs", lambda m, q, b: desk.replay_runs()),
        ("POST", r"/api/replay/run", lambda m, q, b: desk.replay_run(b)),
        ("GET", r"/api/replay/runs/([\w./-]+)", lambda m, q, b: desk.replay_get(m[1])),
        ("GET", r"/api/costs/defaults", lambda m, q, b: desk.cost_defaults()),
        ("PUT", r"/api/costs/defaults", lambda m, q, b: desk.put_cost_defaults(b)),
        ("GET", r"/api/basket", lambda m, q, b: desk.basket_settings()),
        ("PUT", r"/api/basket", lambda m, q, b: desk.put_basket(b)),
        ("POST", r"/api/script/check", lambda m, q, b: desk.script_check(b)),
        ("POST", r"/api/script/price", lambda m, q, b: desk.script_price(b)),
        *risk_routes(desk),
    ]


def make_handler(desk: Desk, token: str | None = None):
    table = [(meth, re.compile(f"^{pat}$"), fn) for meth, pat, fn in routes(desk)]
    risk = desk.risk

    class Handler(BaseHTTPRequestHandler):
        server_version = "optlab-desk"
        # One buffered write per response and no Nagle: headers and body sent apart wait ~250 ms for a delayed ACK
        # in browsers on Windows. Keep-alive serves the page's modules on one connection; idle ones close after 30 s.
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True
        wbufsize = -1
        timeout = 30

        def log_message(self, fmt, *args):  # through logging, not stderr
            LOG.debug("%s %s", self.address_string(), fmt % args)

        def _authorised(self, query) -> bool:
            if not token:
                return True
            given = self.headers.get("X-Optlab-Token") or query.get("token", [None])[0]
            if not given:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                given = cookie["optlab_token"].value if "optlab_token" in cookie else None
            return bool(given) and hmac.compare_digest(given, token)

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload):
            self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

        def _handle(self, method: str):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if refusal := cross_site_refusal(method, self.headers, token):
                LOG.warning("refused %s %s from %s: %s", method, url.path, self.address_string(), refusal[1])
                return self._json(refusal[0], {"error": refusal[1]})
            if not self._authorised(query):
                return self._json(401, {"error": "a token is required: open the address with ?token=..."})
            extra = {}
            if token and "token" in query:
                extra["Set-Cookie"] = f"optlab_token={token}; HttpOnly; SameSite=Strict; Path=/"
            if url.path.startswith("/api/"):
                return self._api(method, url.path, query)
            if url.path.startswith("/report/"):
                return self._report(url.path, extra)
            if method != "GET":
                return self._json(405, {"error": "method not allowed"})
            return self._static(url.path, extra)

        def _api(self, method, path, query):
            body = {}
            if method in ("POST", "PUT"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "the request body is not JSON"})
            for meth, rx, fn in table:
                m = rx.match(path)
                if m and meth == method:
                    try:
                        return self._json(200, fn(m, query, body))
                    except ApiError as exc:
                        return self._json(exc.status, {"error": str(exc), "field": exc.field})
                    except KeyError as exc:
                        return self._json(404, {"error": str(exc).strip("'\"")})
                    except (ValueError, TypeError) as exc:
                        return self._json(422, {"error": str(exc)})
                    except Exception as exc:  # the page shows it; the log keeps the trace
                        LOG.exception("API %s %s failed", method, path)
                        return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return self._json(404, {"error": f"no route {method} {path}"})

        def _report(self, path, extra):
            try:
                if path == "/report/book":
                    html = book_report(desk)
                elif path == "/report/desk":
                    html = desk_snapshot(desk)
                elif path == "/report/risk":
                    html = risk_report(risk)
                elif m := re.match(r"^/report/quote/(\w+)$", path):
                    html = quote_report(desk.saved_quote(m[1]))
                else:
                    return self._json(404, {"error": "no such report"})
            except (ApiError, KeyError) as exc:
                return self._json(404, {"error": str(exc)})
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                       extra | {"Content-Disposition": "inline"})

        def _static(self, path, extra):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / rel).resolve()
            if not target.is_relative_to(STATIC) or not target.is_file():
                target = STATIC / "index.html"  # client-side routes fall back to the page
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".js":
                ctype = "text/javascript"
            self._send(200, target.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""),
                       extra)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


def serve(desk: Desk, host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        raise ValueError("listening beyond this machine needs a token (--token): the desk has no other protection")
    httpd = ThreadingHTTPServer((host, port), make_handler(desk, token))
    httpd.daemon_threads = True
    return httpd


def serve_in_thread(desk: Desk, host="127.0.0.1", port=0, token=None):
    """For tests: a server on a free port, running in a thread. Returns (server, base url)."""
    httpd = serve(desk, host, port, token)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://{host}:{httpd.server_address[1]}"
