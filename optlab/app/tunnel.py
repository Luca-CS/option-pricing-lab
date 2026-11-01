"""A public https address for the desk through an SSH reverse tunnel to localhost.run: nothing to install (ssh ships
with Windows 10 and later, macOS and Linux) and the engine stays on this PC. The relay is a third party that sees
the traffic go by, so the desk always requires its token when a tunnel is open; the address changes at each start.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading

URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.(lhr\.life|localhost\.run)\b", re.I)


def public_url(line: str) -> str | None:
    """The https address in a line of the relay's output, if any."""
    m = URL.search(line)
    return m.group(0) if m else None


def open_tunnel(port: int, relay: str = "localhost.run", timeout: float = 30.0):
    """Start `ssh -R 80:127.0.0.1:port nokey@relay` and wait for the public address. Returns (process, url); the
    caller terminates the process to close the tunnel."""
    ssh = shutil.which("ssh")
    if ssh is None:
        raise RuntimeError("ssh is not on PATH (Windows: Settings > System > Optional features > OpenSSH Client)")
    cmd = [ssh, "-T", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
           "-o", "ExitOnForwardFailure=yes", "-R", f"80:127.0.0.1:{port}", f"nokey@{relay}"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    found: dict = {}
    seen: list = []
    ready = threading.Event()

    def pump():
        for line in proc.stdout:
            seen.append(line.rstrip())
            url = public_url(line)
            if url and "url" not in found:
                found["url"] = url
                ready.set()
        ready.set()  # ssh ended

    threading.Thread(target=pump, daemon=True).start()
    if not ready.wait(timeout) or "url" not in found:
        proc.terminate()
        tail = " | ".join(seen[-3:]) or "no output"
        raise RuntimeError(f"the tunnel gave no public address within {timeout:.0f} s ({tail})")
    return proc, found["url"]
