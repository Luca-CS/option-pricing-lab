"""Market snapshots: an identified, immutable calibrated market that every price can point back to.

The id hashes the market content (curves, surface, Heston and SLV parameters, underlying, dates, source), so the
same calibration always gets the same id and any change gets a new one. `spot_scale` (real spot / model spot) is a
local fact: published calibrations are rescaled to a spot of 100 and the real level comes from Yahoo, so it is
never part of the hash nor of a versioned file.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from ..config import REPO_ROOT


def _code_version() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass(frozen=True)
class MarketSnapshot:
    underlying: str
    asof: str  # ISO timestamp of the observation
    valuation: dt.date
    source: str  # yahoo, simulated or manual
    market: dict  # Market.to_dict()
    surface: dict  # SVISurface.to_dict()
    heston: dict
    slv: dict  # {"mix", "leverage"}
    provenance: dict = field(default_factory=dict, compare=False)  # file hash, code version, creation time
    spot_scale: float | None = field(default=None, compare=False)  # real spot / model spot, local only

    def __post_init__(self):
        if isinstance(self.valuation, str):
            object.__setattr__(self, "valuation", dt.date.fromisoformat(self.valuation))

    def content(self) -> dict:
        d = asdict(self)
        d.pop("provenance"), d.pop("spot_scale")
        d["valuation"] = self.valuation.isoformat()
        return d

    @property
    def snapshot_id(self) -> str:
        digest = hashlib.sha256(json.dumps(self.content(), sort_keys=True).encode()).hexdigest()[:10]
        stamp = self.asof.replace("-", "").replace(":", "")[:13]
        return f"{self.underlying}-{stamp}-{digest}"

    def to_json(self, include_local: bool = False) -> str:
        d = {**self.content(), "provenance": self.provenance}
        if include_local and self.spot_scale is not None:
            d["spot_scale"] = self.spot_scale
        return json.dumps(d, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "MarketSnapshot":
        return cls(**json.loads(s))

    def with_spot_scale(self, real_spot: float) -> "MarketSnapshot":
        """The same snapshot with its local scale: real spot level / model spot."""
        return replace(self, spot_scale=float(real_spot) / float(self.market["spot"]))

    @classmethod
    def from_calibration_file(cls, path, underlying: str = "SPX", valuation: dt.date | str | None = None,
                              source: str | None = None, provenance: bool = True) -> "MarketSnapshot":
        """A snapshot from a calibration JSON (results/*.json). Files without a snapshot time (the simulated
        market) need `valuation`."""
        path = Path(path)
        raw = path.read_bytes()
        cal = json.loads(raw)
        meta = cal.get("meta", {})
        stamp = meta.get("snapshot")
        if valuation is None and stamp is None:
            raise ValueError(f"{path.name} has no snapshot time: give the valuation date")
        valuation = dt.date.fromisoformat(str(valuation)) if valuation is not None else \
            dt.date.fromisoformat(stamp[:10])
        slv = {"mix": cal["slv"]["mix"], "leverage": cal["slv"]["leverage"]}
        prov = {"file": path.name, "file_sha256": hashlib.sha256(raw).hexdigest(), "note": meta.get("note")}
        if provenance:
            prov.update(code_version=_code_version(), created_at=dt.datetime.now(dt.timezone.utc).isoformat())
        return cls(underlying, stamp or valuation.isoformat(), valuation, source or meta.get("source", "manual"),
                   cal["market"], cal["surface"], cal["heston"], slv, prov)


def to_model_scale(fixings: dict, spot_scale: float) -> dict:
    """Real index fixings ({underlying: {date: level}}) divided by the snapshot's scale, to the model's spot."""
    if not spot_scale or spot_scale <= 0:
        raise ValueError("spot_scale must be a positive number (real spot / model spot)")
    return {name: {d: float(v) / spot_scale for d, v in levels.items()} for name, levels in fixings.items()}
