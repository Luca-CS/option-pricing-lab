"""Start the desk: python -m optlab.app [--port 8765] [--host 127.0.0.1] [--token TOKEN] [--workers N] [--open]
[--tunnel]"""

from __future__ import annotations

import argparse
import secrets
import threading
import webbrowser

from ..log import configure
from .api import Desk
from .server import serve
from .tunnel import open_tunnel


def main():
    ap = argparse.ArgumentParser(prog="python -m optlab.app", description="optlab desk: pricing, book and lifecycle")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 (this machine only) or 0.0.0.0 (the LAN)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", help="access token; generated when listening beyond this machine")
    ap.add_argument("--workers", type=int, default=None, help="process pool for Monte Carlo batches (e.g. 6)")
    ap.add_argument("--store", help="SQLite file of trades, fixings and quotes (default data/store/desk.sqlite)")
    ap.add_argument("--calibration", help="results/*.json calibration (default: the SPX one)")
    ap.add_argument("--tunnel", action="store_true",
                    help="public https address through an SSH tunnel to localhost.run (third-party relay, token on)")
    ap.add_argument("--open", action="store_true", help="open the page in the default browser")
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args()
    configure(args.log)
    token = args.token
    if (args.tunnel or args.host not in ("127.0.0.1", "localhost", "::1")) and not token:
        token = secrets.token_urlsafe(16)
    desk = Desk(args.store, args.calibration, args.workers)
    httpd = serve(desk, args.host, args.port, token)
    port = httpd.server_address[1]
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    url = f"http://{shown}:{port}/" + (f"?token={token}" if token else "")
    print(f"optlab desk on {url}")
    print(f"market {desk.ctx.snapshot_id}, valuation {desk.ctx.valuation}, store {desk.repo.path}")
    if args.host == "0.0.0.0":
        print(f"listening on the LAN: share http://<this-pc-address>:{port}/?token={token}")
    tunnel = None
    if args.tunnel:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            tunnel, public = open_tunnel(port)
            desk.public_url = public
            print(f"public address (through localhost.run): {public}/?token={token}")
        except RuntimeError as exc:
            print(f"no tunnel: {exc}")
    if args.open:
        webbrowser.open(url)
    try:
        if args.tunnel:
            threading.Event().wait()
        else:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if tunnel is not None:
            tunnel.terminate()
        desk.jobs.shutdown()


if __name__ == "__main__":
    main()
