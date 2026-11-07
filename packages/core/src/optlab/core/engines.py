"""Which engine simulates: NumPy, the library's reference, or a compiled engine that another distribution registers.

The core never imports the compiled engine (ADR-0004). `optlab-native` declares itself in the entry point group
`optlab.engines` and is loaded on first use, so `optlab.core` installs and runs alone. An engine is asked for by name:

- "numpy": the pricers of this package, whose numbers are the library's reference;
- "cpu" or "gpu": the compiled engine on every CPU thread (double) or on a CUDA device (float);
- "auto": the GPU when there is one, else the CPU, else NumPy with the reason.

The default is `set_default`'s, else $OPTLAB_ENGINE, else numpy, so the library's numbers stay those of NumPy unless
asked. A spawned process does not inherit `set_default`: a worker is told its engine explicitly (`risk.var`).
Which engine runs is only a name: the paths each one produces are unchanged by the registry.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from typing import Any, Protocol, cast

import numpy as np

ENGINES = ("numpy", "cpu", "gpu", "auto")
GROUP = "optlab.engines"
NOT_INSTALLED = "no compiled engine is installed (optlab-native)"


class EngineError(RuntimeError):
    """The compiled engine failed, or cannot run what was asked of it by name."""


class Engine(Protocol):
    """What the core and the products ask of a compiled engine; `optlab.native` implements it."""

    MAX_WINDOWS: int  # extreme-spot windows one run can follow

    def available(self) -> bool: ...  # the compiled library is there (its CPU backend then works)

    def gpu_available(self) -> bool: ...

    def cpu_threads(self) -> int: ...

    def unsupported(self, model) -> str | None: ...  # why this model cannot run on the engine, or None

    def plan(self, model, market, times, dt_max: float = ..., dt_min: float = ..., n_x: int = ...) -> Any: ...

    def run(
        self,
        p,
        n_pairs: int,
        seed: int,
        first_pair: int = 0,
        backend: str = "cpu",
        threads: int = 0,
        keep=None,
        windows=(),
    ) -> tuple: ...

    def simulate(
        self, model, market, times, n_paths: int, seed=None, dt_max=..., dt_min=..., backend: str = "auto"
    ) -> Any: ...  # spots at `times`, as models.simulate (a dict per name for a Basket)

    def calibrate_leverage(self, params, surface, market, T_max: float, *args, **kwargs) -> Any: ...  # an SLVModel


_DEFAULT: str | None = None
_compiled: list = []  # [engine or None] once looked up


def compiled() -> Engine | None:
    """The compiled engine registered under the name "native" in `optlab.engines`, or None when none is installed.
    A registered engine that fails to import raises: a broken install is not silently replaced by NumPy."""
    if not _compiled:
        found = next((ep for ep in entry_points(group=GROUP) if ep.name == "native"), None)
        _compiled.append(cast(Engine, found.load()) if found is not None else None)
    return _compiled[0]


def set_default(engine: str | None) -> None:
    """The engine of every simulation that does not name one (None: back to $OPTLAB_ENGINE or numpy)."""
    global _DEFAULT
    if engine is not None and engine.lower() not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}, not {engine!r}")
    _DEFAULT = engine.lower() if engine else None


def default_engine() -> str:
    """set_default's, else $OPTLAB_ENGINE, else numpy: the library's reference numbers stay those of NumPy unless
    asked."""
    if _DEFAULT:
        return _DEFAULT
    e = os.environ.get("OPTLAB_ENGINE", "numpy").lower()
    return e if e in ENGINES else "numpy"


def choose(engine: str | None, model) -> tuple[str, str | None]:
    """(backend, reason): "numpy", "cpu" or "gpu" for this request; the reason says why NumPy when "auto" falls back.
    A named engine that cannot run ("cpu" or "gpu") raises EngineError with the reason."""
    engine = (engine or default_engine()).lower()
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}, not {engine!r}")
    if engine == "numpy":
        return "numpy", None
    eng = compiled()
    if eng is None:
        why = NOT_INSTALLED
    else:
        why = eng.unsupported(model)
        if why is None and not eng.available():
            why = "the compiled engine is not built"
        if why is None and engine == "gpu" and not eng.gpu_available():
            why = "no CUDA device"
    if why is not None:
        if engine == "auto":
            return "numpy", why
        raise EngineError(why)
    assert eng is not None
    if engine == "auto":
        return ("gpu" if eng.gpu_available() else "cpu"), None
    return engine, None


def require() -> Engine:
    """The compiled engine, or EngineError when no distribution provides one."""
    eng = compiled()
    if eng is None:
        raise EngineError(NOT_INSTALLED)
    return eng


def seed64(seed) -> int:
    """A 64-bit key from an int, a SeedSequence or None (fresh entropy): the engine's Philox key."""
    if isinstance(seed, np.random.SeedSequence):
        return int(seed.generate_state(1, np.uint64)[0])
    if seed is None:
        return int(np.random.SeedSequence().generate_state(1, np.uint64)[0])
    return int(seed) & (2**64 - 1)
