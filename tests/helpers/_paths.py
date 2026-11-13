"""Where the tests find the repository's files and the machine's data. The library itself has no default path any
more (ADR-0012): the versioned calibrations of `results/` (compact JSON, ADR-0023) and, for the tests marked
`lake`, the real lake that Settings name."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "results"
SPX_CALIBRATION = RESULTS / "spx_calibration.json"
SIMULATED_CALIBRATION = RESULTS / "simulated_calibration.json"


def real_lake() -> Path:
    """The lake of this machine's settings (`OPTLAB_DATA`, or the development default)."""
    from optlab.server.settings import Settings

    return Settings.load().lake


def spx_lake(min_surfaces: int = 600):
    """The real lake when it holds at least `min_surfaces` reconstructed SPX surfaces; the test is skipped otherwise."""
    import pytest

    from optlab.marketdata.lake import Lake

    lk = Lake(real_lake())
    try:
        n = lk.read("vol_surfaces", where="underlying = 'SPX' AND layer = 'reconstructed'", columns="count(*) AS n")[
            "n"
        ].iloc[0]
    except Exception:  # noqa: BLE001 - no lake on this machine
        pytest.skip("no lake")
    if n < min_surfaces:
        pytest.skip("no reconstructed SPX surfaces in the lake")
    return lk
