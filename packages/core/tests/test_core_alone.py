"""optlab.core stands alone (ADR-0004): importing every module of it loads no other optlab package, none of the data or
web libraries, and starts no thread, so that the Pyodide trial of the demo (session 2) can load it. The compiled engine
is reached by name through `optlab.core.engines`, and its absence falls back to NumPy with the reason."""

import json
import subprocess
import sys

import numpy as np
import pytest

from optlab.core import engines
from optlab.core.models import BlackScholesModel

PROBE = """
import json, pkgutil, sys, threading
before = threading.active_count()
import optlab.core
for m in pkgutil.walk_packages(optlab.core.__path__, "optlab.core."):
    __import__(m.name)
heavy = ("pandas", "duckdb", "pyarrow", "requests", "yfinance", "matplotlib", "fastapi", "starlette", "sqlalchemy")
print(json.dumps({
    "optlab": sorted(m for m in sys.modules if m.split(".")[0] == "optlab"),
    "heavy": [m for m in heavy if m in sys.modules],
    "threads": threading.active_count() - before,
}))
"""


def test_the_core_imports_alone():
    out = subprocess.run([sys.executable, "-I", "-c", PROBE], capture_output=True, text=True, check=True, timeout=120)
    got = json.loads(out.stdout)
    assert "optlab.core.engines" in got["optlab"] and "optlab.core.slv" in got["optlab"]
    assert all(m in ("optlab", "optlab.core") or m.startswith("optlab.core.") for m in got["optlab"]), got["optlab"]
    assert got["heavy"] == [] and got["threads"] == 0


@pytest.fixture
def no_compiled_engine(monkeypatch):
    monkeypatch.setattr(engines, "_compiled", [None])
    monkeypatch.setattr(engines, "_DEFAULT", None)
    monkeypatch.delenv("OPTLAB_ENGINE", raising=False)


def test_without_a_compiled_engine_auto_is_numpy_with_the_reason(no_compiled_engine):
    model = BlackScholesModel(0.2)
    assert engines.choose(None, model) == ("numpy", None)  # the default stays NumPy
    assert engines.choose("auto", model) == ("numpy", engines.NOT_INSTALLED)
    for named in ("cpu", "gpu"):
        with pytest.raises(engines.EngineError, match="no compiled engine"):
            engines.choose(named, model)
    with pytest.raises(engines.EngineError):
        engines.require()
    with pytest.raises(ValueError, match="engine must be one of"):
        engines.choose("cuda", model)


def test_the_default_engine_is_named_by_set_default_then_the_environment(no_compiled_engine, monkeypatch):
    assert engines.default_engine() == "numpy"
    monkeypatch.setenv("OPTLAB_ENGINE", "AUTO")
    assert engines.default_engine() == "auto"
    monkeypatch.setenv("OPTLAB_ENGINE", "nonsense")
    assert engines.default_engine() == "numpy"
    engines.set_default("cpu")
    assert engines.default_engine() == "cpu"
    engines.set_default(None)
    with pytest.raises(ValueError):
        engines.set_default("cuda")


def test_seed64_is_the_engine_key():
    assert engines.seed64(7) == 7 and engines.seed64(-1) == 2**64 - 1
    ss = np.random.SeedSequence(2026)
    assert engines.seed64(ss) == int(np.random.SeedSequence(2026).generate_state(1, np.uint64)[0])
    assert 0 <= engines.seed64(None) < 2**64
