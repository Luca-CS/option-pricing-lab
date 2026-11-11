"""Where the tests find the repository's files and the machine's data. The library itself has no default path any
more (ADR-0012): the versioned calibrations of `results/` (until ADR-0016 archives them) and, for the tests marked
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
