"""Adapters of the free sources. Each module splits the download from a pure `parse` that the tests run offline.

Requests carry a generic User-Agent and no personal identifier. Yahoo's data are for personal use: they stay in the
lake (never versioned), and only calibrated parameters and charts may leave the machine.
"""

from __future__ import annotations

import time

import requests

USER_AGENT = "Mozilla/5.0 (compatible; optlab-data/0.1)"


def http_get(url: str, timeout: float = 90.0, retries: int = 3, headers: dict | None = None) -> bytes:
    """GET with a generic User-Agent; retries transient failures (some endpoints answer 5xx at times)."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, timeout=timeout)
            if r.status_code < 500:
                r.raise_for_status()
                return r.content
            last = requests.HTTPError(f"{r.status_code} for {url}")
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = exc
        time.sleep(2.0 * (attempt + 1))
    raise last
