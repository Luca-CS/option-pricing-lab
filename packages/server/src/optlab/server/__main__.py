"""`python -m optlab.server [options]` is `optlab serve [options]` (ADR-0010): the API on 127.0.0.1, with its worker.

Another host needs the LAN profile (`OPTLAB_LAN=true`, with Luca's agreement, ADR-0002). Accounts and sessions replace
v0's shared token and `--tunnel` link (`optlab users create <name> --roles admin`).
"""

from __future__ import annotations

import sys

from optlab.server.cli import run

if __name__ == "__main__":
    sys.exit(run(["serve", *sys.argv[1:]]))
