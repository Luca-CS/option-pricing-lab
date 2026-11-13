"""The bodies of v0's scripts (`scripts/*.py`, inventory §3), each a function the `optlab` command line calls with its
options (ADR-0010): reports, benchmarks, the showcase and the lake's maintenance. Paths come from `Settings`; nothing
here runs at import, so `optlab --help` needs neither matplotlib nor the lake."""


def say(*parts) -> None:
    """Print a line now: long commands are often read through a pipe or a log file."""
    print(*parts, flush=True)
