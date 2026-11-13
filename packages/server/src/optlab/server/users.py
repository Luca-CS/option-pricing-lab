"""Accounts from the command line (ADR-0020): `optlab users ...`, or `python -m optlab.server.users ...`.

    optlab users create <name> --roles admin[,trader,...]   # asks the password twice, never echoes it
    optlab users list
    optlab users roles <name> <role,role>
    optlab users password <name>
    optlab users disable <name> | enable <name>

The database is the one of the settings (`OPTLAB_DATA`, `--database`). Every command is in the audit log, as the
system. A password is read with `getpass` (or from standard input with `--stdin`, for scripts and tests); it is never
an argument, so it never lands in a shell history or a process list. The commands themselves are in
`optlab.server.cli`.
"""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    from optlab.server.cli import run

    return run(["users", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
