"""What stops other machines from reaching the desk on this PC's network, on Windows: a connection in the Public
profile, or inbound firewall rules that block this Python. Read-only: changing them is the owner's decision, so the
desk only says what it found and where to change it. A tunnel (outbound) is not affected."""

from __future__ import annotations

import json
import os
import subprocess
import sys

SCRIPT = r"""
$p = @(Get-NetConnectionProfile | ForEach-Object { [pscustomobject]@{ alias = $_.InterfaceAlias;
       category = "$($_.NetworkCategory)" } })
$r = @(Get-NetFirewallApplicationFilter | Where-Object { $_.Program -like '*python*' } | ForEach-Object {
       $x = $_ | Get-NetFirewallRule
       [pscustomobject]@{ program = $_.Program; action = "$($x.Action)"; profile = "$($x.Profile)";
                          enabled = "$($x.Enabled)"; direction = "$($x.Direction)" } })
ConvertTo-Json -Compress -Depth 4 @{ profiles = $p; rules = $r }
"""


def _list(x) -> list:
    return [] if x is None else x if isinstance(x, list) else [x]


def interpret(data: dict, python: str) -> list[str]:
    """Plain-language reasons why another machine cannot connect, from the profiles and rules read on Windows."""
    out = []
    me = os.path.normcase(os.path.abspath(python))
    blocks = [
        r
        for r in _list(data.get("rules"))
        if r.get("direction") == "Inbound"
        and r.get("action") == "Block"
        and r.get("enabled") == "True"
        and os.path.normcase(os.path.abspath(os.path.expandvars(r.get("program", "")))) == me
    ]
    if blocks:
        where = ", ".join(sorted({r.get("profile", "?") for r in blocks}))
        out.append(
            f"Windows Firewall blocks incoming connections to {python} ({where} networks): allow it in "
            "Windows Security > Firewall > Allow an app, or remove the block rule"
        )
    public = [p.get("alias") for p in _list(data.get("profiles")) if p.get("category") == "Public"]
    if public:
        out.append(
            f"{', '.join(public)} is in the Public profile, where Windows refuses incoming connections by "
            "default: on a home network set it to Private (Settings > Network > the connection > Private)"
        )
    return out


def lan_blockers(timeout: float = 30.0) -> list[str]:
    """Reasons this PC refuses other machines (Windows only; empty elsewhere or when the check cannot run)."""
    if sys.platform != "win32":
        return []
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        data = json.loads(res.stdout or "{}")
    except OSError, subprocess.TimeoutExpired, json.JSONDecodeError:
        return []
    return interpret(data, getattr(sys, "_base_executable", sys.executable))
