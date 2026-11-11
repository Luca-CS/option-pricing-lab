"""Accounts from the command line (ADR-0020); `optlab users` in A7.

    python -m optlab.server.users create <name> --roles admin[,trader,...]   # asks the password twice, never echoes it
    python -m optlab.server.users list
    python -m optlab.server.users roles <name> <role,role>
    python -m optlab.server.users password <name>
    python -m optlab.server.users disable <name> | enable <name>

The database is the one of the settings (`OPTLAB_DATA`, `--database`). Every command is in the audit log, as the
system. A password is read with `getpass` (or from standard input with `--stdin`, for scripts and tests); it is never
an argument, so it never lands in a shell history or a process list.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from optlab.server.audit import Audit
from optlab.server.persistence import Database
from optlab.server.security import ROLES, Accounts
from optlab.server.settings import Settings


def _password(stdin: bool) -> str:
    if stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("password (12 characters at least): ")
    if getpass.getpass("again: ") != first:
        raise SystemExit("the two passwords differ")
    return first


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("create", "list", "roles", "password", "disable", "enable"))
    ap.add_argument("name", nargs="?")
    ap.add_argument("value", nargs="?", help="the roles, for `roles`")
    ap.add_argument("--roles", help=f"for `create`: comma-separated, among {', '.join(ROLES)}")
    ap.add_argument("--database", help="the database file (default: the settings')")
    ap.add_argument("--stdin", action="store_true", help="read the password from standard input")
    args = ap.parse_args(argv)
    settings = Settings.load()
    db = Database(args.database or settings.database)
    accounts, audit = Accounts(db, settings), Audit(db)
    try:
        if args.command == "list":
            for u in accounts.all():
                state = "disabled" if u["disabled"] else ("locked" if u["locked_until"] else "active")
                print(f"{u['name']:20s} {','.join(u['roles']):40s} {state}")
            return 0
        if not args.name:
            raise SystemExit(f"{args.command} needs a user name")
        if args.command == "create":
            roles = [r.strip() for r in (args.roles or "").split(",") if r.strip()]
            user = accounts.create(args.name, _password(args.stdin), roles)
            audit.record(None, "cli_create_user", args.name, detail={"roles": sorted(user.roles)})
            print(f"created {args.name} ({', '.join(sorted(user.roles))})")
        elif args.command == "roles":
            roles = [r.strip() for r in (args.value or "").split(",") if r.strip()]
            accounts.set_roles(args.name, roles)
            audit.record(None, "cli_set_roles", args.name, detail={"roles": roles})
            print(f"{args.name}: {', '.join(sorted(roles))}; its sessions are closed")
        elif args.command == "password":
            accounts.set_password(args.name, _password(args.stdin))
            audit.record(None, "cli_set_password", args.name)
            print(f"{args.name}: new password; its sessions are closed")
        else:
            accounts.set_disabled(args.name, args.command == "disable")
            audit.record(None, f"cli_{args.command}_user", args.name)
            print(f"{args.name}: {args.command}d")
    except (ValueError, KeyError) as exc:
        print(f"error: {str(exc).strip(chr(39))}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
