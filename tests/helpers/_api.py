"""API v1 on a fresh database in a test folder, and clients signed in by role (generated passwords, never written).

with api(tmp_path) as a:
    trader = a.client("trader")         # a user holding only that role, its CSRF token sent on every call
    anonymous = a.client(None)
"""

from __future__ import annotations

import secrets
import time
from contextlib import contextmanager
from pathlib import Path

# the script lab's example of the v0 page (legacy/desk-static/js/views/script.js): it must stay valid
SCRIPT_EXAMPLE = """underlying SPX
param N = 100
param K = 1.0
param B = 0.7

event 2026-10-27
  S0 = SPX
end

event 2027-10-27 pay 2027-11-03
  perf = SPX / S0
  if lowest(SPX, 2026-10-27, 2027-10-27) < B * S0 then
    pay N - N * max(K - perf, 0) as redemption
  else
    pay N + N * 0.5 * max(perf - 1, 0) as redemption
  end
end
"""

ROLES = ("admin", "trader", "structurer", "risk", "quant", "viewer")


class Api:
    def __init__(self, app):
        from fastapi.testclient import TestClient

        self.app = app
        self.state = app.state.optlab
        self._client = TestClient
        self._open: list = []

    def client(self, *roles: str):
        """A client signed in as a new user with these roles (none: not signed in)."""
        c = self._client(self.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
        self._open.append(c)
        if roles:
            name = f"u{secrets.token_hex(4)}"
            password = secrets.token_urlsafe(18)
            self.state.accounts.create(name, password, list(roles))
            r = c.post("/api/v1/auth/login", json={"name": name, "password": password})
            assert r.status_code == 200, r.text
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            c.user_name = name  # type: ignore[attr-defined]
        return c

    @staticmethod
    def wait(client, job_id: str, timeout: float = 300.0) -> dict:
        """The job's result once it is done (the job polled as the page would)."""
        t0 = time.perf_counter()
        while True:
            r = client.get(f"/api/v1/jobs/{job_id}")
            assert r.status_code == 200, r.text
            j = r.json()
            if j["state"] not in ("queued", "running"):
                break
            assert time.perf_counter() - t0 < timeout, f"job {job_id} did not finish"
            time.sleep(0.1)
        assert j["state"] == "done", j.get("error")
        return j["result"]

    def close(self):
        for c in self._open:
            c.close()


@contextmanager
def api(folder, **settings):
    """The app on `folder` (a store, an empty lake); `settings` override Settings fields. The engine is NumPy unless
    asked (the Desk sets the process-wide default, which the bit-for-bit tests of the other packages read): the
    default in force before is restored on exit."""
    from optlab.core import engines
    from optlab.server.app import create_app
    from optlab.server.settings import Settings

    folder = Path(folder)
    before = engines._DEFAULT
    s = Settings(data=folder, lake=folder / "lake", **{"engine": "numpy", **settings})
    app = create_app(s)
    a = Api(app)
    try:
        yield a
    finally:
        a.close()
        app.state.optlab.desk.jobs.shutdown()
        app.state.optlab.desk.db.close()
        engines.set_default(before)


def preset_sheet(client, name: str) -> dict:
    """A preset's sheet as the API takes it back: {type, values}."""
    presets = client.get("/api/v1/structuring/presets").json()["presets"]
    s = next(p["sheet"] for p in presets if p["name"] == name)
    return {"type": s["type"], "values": {k: v for k, v in s.items() if k != "type"}}
