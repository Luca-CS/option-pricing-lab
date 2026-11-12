"""The historical data lake: immutable raw files with a manifest, curated Parquet, DuckDB for reading.

Layers (the desk's DESIGN.md, packages/server/src/optlab/server/DESIGN.md, section 8):
- raw/: files as downloaded, one per content hash, listed in raw/manifest.jsonl (URL, date, sha256);
- curated/<table>/[underlying=X/]year=YYYY/<run>.parquet: normalised rows, append-only;
- DuckDB reads the Parquet directly (in memory, nothing to administer). The desk's SQLite stays the operational store.

Bitemporal: a row is identified by its table keys plus `source` and `obs_date`. Reloading the same content adds
nothing; a changed value adds a new version with a later `ingested_at`, and the old one stays readable with `as_of`.
A row missing from a later file is not deleted: vendors restate, they do not rewrite what was known.

Every curated row carries `obs_date`, `ingested_at`, `source`, `run_id`, `quality` and `origin`. `origin` says how much
to trust it and goes up to the screen: observed, derived, reconstructed or synthetic.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from optlab.core.log import get_logger

LOG = get_logger(__name__)
ORIGINS = ("observed", "derived", "reconstructed", "synthetic")
STANDARD = ("obs_date", "ingested_at", "source", "run_id", "quality", "origin", "row_hash")
ARROW = {"str": pa.string(), "float": pa.float64(), "int": pa.int64(), "date": pa.date32(), "bool": pa.bool_()}


@dataclass(frozen=True)
class Table:
    keys: dict  # column -> type, on top of obs_date and source
    values: dict  # column -> type
    partition: tuple = ()  # key columns used as folders, before the year


TABLES = {
    "instruments": Table(
        {"instrument": "str"},
        {
            "name": "str",
            "asset_class": "str",
            "currency": "str",
            "place": "str",
            "yahoo": "str",
            "cboe": "str",
            "vol_index": "str",
            "total_return": "str",
            "enabled": "bool",
        },
    ),
    "calendars": Table({"place": "str"}, {"is_open": "bool"}),
    "prices_eod": Table(
        {"instrument": "str"},
        {"open": "float", "high": "float", "low": "float", "close": "float", "adj_close": "float", "volume": "float"},
        partition=("instrument",),
    ),
    "corporate_actions": Table({"instrument": "str", "kind": "str"}, {"value": "float"}),
    "vol_indices": Table({"index_name": "str"}, {"open": "float", "high": "float", "low": "float", "close": "float"}),
    "option_quotes": Table(
        {"underlying": "str", "root": "str", "expiry": "date", "strike": "float", "cp": "str"},
        {
            "bid": "float",
            "ask": "float",
            "iv": "float",
            "delta": "float",
            "gamma": "float",
            "theta": "float",
            "vega": "float",
            "volume": "float",
            "open_interest": "float",
            "underlying_close": "float",
            "settlement": "str",
        },
        partition=("underlying",),
    ),
    "rate_points": Table({"curve": "str", "tenor": "float"}, {"rate": "float"}),
    "discount_curves": Table({"curve": "str", "tenor": "float"}, {"zero_rate": "float", "discount": "float"}),
    "fx_spots": Table({"pair": "str"}, {"rate": "float"}),
    "fx_forwards": Table({"pair": "str", "tenor": "float"}, {"forward": "float", "points": "float"}),
    "fx_vols": Table({"pair": "str", "window": "int", "method": "str"}, {"vol": "float"}),
    "carry_implied": Table(
        {"underlying": "str", "tenor": "float", "method": "str"},
        {"carry": "float", "discount": "float", "forward_ratio": "float"},
    ),
    "credit_spreads": Table({"entity": "str", "tenor": "float"}, {"spread": "float"}),
    "correl_implied": Table({"index_name": "str", "tenor": "float"}, {"correlation": "float"}),
    "correl_realized": Table({"name_a": "str", "name_b": "str", "window": "int"}, {"correlation": "float"}),
    "basket_spreads": Table(
        {"basket": "str", "kind": "str", "tenor": "float", "strike": "float"},
        {
            "price": "float",
            "price_se": "float",
            "basket_price": "float",
            "members_price": "float",
            "basket_vol": "float",
            "rho": "float",
            "implied_rho": "float",
        },
    ),
    "vol_surfaces": Table(
        {"underlying": "str", "layer": "str"},
        {"surface": "str", "t_max_observed": "float", "extrapolated_from": "float", "inputs": "str", "fit": "str"},
        partition=("underlying",),
    ),
    "calibration_runs": Table(
        {"underlying": "str", "calibration_id": "str"},
        {"model": "str", "params": "str", "fit": "str", "seconds": "float", "code_version": "str"},
    ),
    "ingestion_runs": Table(
        {"run": "str", "dataset": "str"},
        {
            "n_input": "int",
            "n_new": "int",
            "n_changed": "int",
            "started": "str",
            "seconds": "float",
            "raw_sha256": "str",
            "status": "str",
        },
    ),
    "quality_issues": Table(
        {"dataset": "str", "item": "str", "check_name": "str"}, {"severity": "str", "detail": "str"}
    ),
}


def sql_str(value) -> str:
    """A SQL string literal. Names reach the lake's queries from the desk's API: they are data, never SQL."""
    return "'" + str(value).replace("'", "''") + "'"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def new_run_id(source: str) -> str:
    return f"{source}-{_utcnow():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True)
class RawFile:
    path: Path
    sha256: str
    url: str
    fetched_at: str
    content: bytes


class Lake:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.curated = self.root / "curated"
        self._manifest = self.raw / "manifest.jsonl"

    # --- raw files ---
    def store_raw(self, source: str, name: str, content: bytes, url: str = "") -> RawFile:
        """Keep a downloaded file by content: the same bytes are stored once; every download is in the manifest."""
        sha = hashlib.sha256(content).hexdigest()
        path = self.raw / source / f"{sha[:16]}_{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        now = _utcnow().isoformat(timespec="seconds")
        with open(self._manifest, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "source": source,
                        "name": name,
                        "url": url,
                        "fetched_at": now,
                        "sha256": sha,
                        "bytes": len(content),
                        "path": str(path.relative_to(self.root)),
                    }
                )
                + "\n"
            )
        return RawFile(path, sha, url, now, content)

    def manifest(self) -> list[dict]:
        if not self._manifest.exists():
            return []
        return [json.loads(line) for line in self._manifest.read_text(encoding="utf-8").splitlines() if line.strip()]

    # --- curated rows ---
    def ingest(
        self,
        table: str,
        rows: pd.DataFrame,
        source: str,
        origin: str,
        run_id: str | None = None,
        quality: str = "ok",
        raw_sha256: str = "",
    ) -> dict:
        """Append what is new or changed in `rows` (keys, values, obs_date). Idempotent: the same rows twice add
        nothing. Returns the counts, also recorded in ingestion_runs."""
        spec = TABLES[table]
        if origin not in ORIGINS:
            raise ValueError(f"unknown origin {origin!r}, expected one of {', '.join(ORIGINS)}")
        started, t0 = _utcnow(), dt.datetime.now()
        run_id = run_id or new_run_id(source)
        n_new = n_changed = 0
        n_input = len(rows)
        if n_input:
            frame = self._normalise(table, rows, source, origin, run_id, quality)
            n_input = len(frame)
            cols = list(spec.partition) + ["year"]
            on = list(spec.keys) + ["obs_date", "source"]
            for values, group in frame.groupby(cols, sort=False):
                part = dict(zip(cols, values if isinstance(values, tuple) else (values,)))
                known = self._known(table, part, group)
                merged = group.merge(known, how="left", on=on)
                fresh = merged[merged["row_hash_old"].isna() | (merged["row_hash_old"] != merged["row_hash"])]
                n_changed += int(fresh["row_hash_old"].notna().sum())
                n_new += int(fresh["row_hash_old"].isna().sum())
                if len(fresh):
                    self._write(table, part, fresh.drop(columns="row_hash_old"), run_id)
        summary = {"table": table, "run_id": run_id, "n_input": n_input, "n_new": n_new, "n_changed": n_changed}
        if table != "ingestion_runs":
            row = pd.DataFrame(
                [
                    {
                        "run": run_id,
                        "dataset": table,
                        "obs_date": started.date(),
                        "n_input": n_input,
                        "n_new": n_new,
                        "n_changed": n_changed,
                        "started": started.isoformat(timespec="seconds"),
                        "seconds": (dt.datetime.now() - t0).total_seconds(),
                        "raw_sha256": raw_sha256,
                        "status": "ok",
                    }
                ]
            )
            self.ingest("ingestion_runs", row, source, "observed", run_id)
        return summary

    def _normalise(self, table, rows, source, origin, run_id, quality) -> pd.DataFrame:
        spec = TABLES[table]
        missing = [c for c in [*spec.keys, "obs_date"] if c not in rows.columns]
        if missing:
            raise ValueError(f"{table}: rows without {', '.join(missing)}")
        rows = rows.reset_index(drop=True)
        frame = pd.DataFrame(index=rows.index)
        frame["obs_date"] = pd.to_datetime(rows["obs_date"]).dt.date
        for col, kind in {**spec.keys, **spec.values}.items():
            frame[col] = _cast(rows[col] if col in rows.columns else None, kind, len(rows))
        before = len(frame)
        frame = frame.drop_duplicates(list(spec.keys) + ["obs_date"], keep="last")
        if len(frame) != before:
            LOG.warning("%s: %s duplicate rows in one load, last kept", table, before - len(frame))
        frame["row_hash"] = pd.util.hash_pandas_object(frame[list(spec.values)], index=False).astype(str)
        frame["ingested_at"] = _utcnow().replace(microsecond=0)
        frame["source"], frame["run_id"], frame["origin"] = source, run_id, origin
        frame["quality"] = rows.loc[frame.index, "quality"] if "quality" in rows.columns else quality
        frame["year"] = [d.year for d in frame["obs_date"]]
        return frame.reset_index(drop=True)

    def _folder(self, table: str, part: dict) -> Path:
        folder = self.curated / table
        for col in TABLES[table].partition:
            folder = folder / f"{col}={part[col]}"
        return folder / f"year={part['year']}"

    def _known(self, table, part, group) -> pd.DataFrame:
        """Key and latest hash of the rows already stored in this partition."""
        spec = TABLES[table]
        files = sorted(self._folder(table, part).glob("*.parquet"))
        cols = list(spec.keys) + ["obs_date", "source"]
        if not files:
            return pd.DataFrame({**{c: group[c].iloc[:0] for c in cols}, "row_hash_old": pd.Series(dtype=object)})
        stored = [c for c in cols if c not in spec.partition]
        select = ", ".join(f'"{c}"' for c in stored)
        con = duckdb.connect()
        try:
            known = con.execute(
                f"SELECT {select}, arg_max(row_hash, ingested_at) AS row_hash_old FROM "
                f"read_parquet(?, union_by_name=true, hive_partitioning=false) GROUP BY {select}",
                [[str(f) for f in files]],
            ).df()
        finally:
            con.close()
        for c in spec.partition:
            known[c] = part[c]
        known["obs_date"] = pd.to_datetime(known["obs_date"]).dt.date
        for col, kind in spec.keys.items():
            if kind == "date":
                known[col] = pd.to_datetime(known[col]).dt.date
            elif col in known and kind == "str":
                known[col] = known[col].astype(object).where(known[col].notna(), None)
        return known

    def _write(self, table, part, rows, run_id) -> Path:
        spec = TABLES[table]
        types = {**spec.keys, **spec.values}
        names = [c for c in spec.keys if c not in spec.partition] + list(spec.values)
        schema = pa.schema(
            [
                *((c, ARROW[types[c]]) for c in names),
                ("obs_date", pa.date32()),
                ("ingested_at", pa.timestamp("s")),
                *((c, pa.string()) for c in ("source", "run_id", "quality", "origin", "row_hash")),
            ]
        )
        arrow = pa.Table.from_pandas(rows[[f.name for f in schema]], schema=schema, preserve_index=False)
        folder = self._folder(table, part)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{run_id}.parquet"
        if path.exists():  # a second load of the same partition within one run
            path = folder / f"{run_id}-{uuid.uuid4().hex[:6]}.parquet"
        tmp = path.with_suffix(".tmp")
        pq.write_table(arrow, tmp, compression="zstd")
        tmp.replace(path)
        return path

    # --- reading ---
    def has(self, table: str) -> bool:
        return (self.curated / table).is_dir() and next((self.curated / table).rglob("*.parquet"), None) is not None

    def _pattern(self, table: str) -> str:
        return str(self.curated / table / "**" / "*.parquet").replace("\\", "/")

    def sql_source(self, table: str, as_of=None, where: str = "") -> str:
        """A FROM clause giving the latest version of each row known at `as_of` (default: now), filtered first."""
        spec = TABLES[table]
        keys = ", ".join(f'"{c}"' for c in [*spec.keys, "obs_date", "source"])
        filters = [f"({where})"] if where else []
        if as_of is not None:
            filters.append(f"ingested_at <= TIMESTAMP '{pd.Timestamp(as_of):%Y-%m-%d %H:%M:%S}'")
        clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        return (
            f"(SELECT * EXCLUDE (_rn, year) FROM (SELECT *, row_number() OVER (PARTITION BY {keys} "
            f"ORDER BY ingested_at DESC, run_id DESC) AS _rn FROM read_parquet('{self._pattern(table)}', "
            f"hive_partitioning=true, union_by_name=true, hive_types_autocast=false) {clause}) WHERE _rn = 1)"
        )

    def read(
        self, table: str, where: str = "", as_of=None, columns: str = "*", order: str = "", params=()
    ) -> pd.DataFrame:
        """Latest rows of a table as a DataFrame (empty, with the right columns, while it has no file). A value that
        comes from a request goes in `params`, bound to the `?` of `where`, never into the SQL text."""
        spec = TABLES[table]
        if not self.has(table):
            return pd.DataFrame(columns=[*spec.keys, *spec.values, *STANDARD])
        con = duckdb.connect()
        try:
            sql = f"SELECT {columns} FROM {self.sql_source(table, as_of, where)}"
            return con.execute(sql + (f" ORDER BY {order}" if order else ""), list(params)).df()
        finally:
            con.close()

    def versions(self, table: str, where: str = "") -> pd.DataFrame:
        """Every stored version of the rows, oldest first: what the bitemporal history keeps."""
        con = duckdb.connect()
        try:
            clause = f"WHERE {where}" if where else ""
            return con.execute(
                f"SELECT * FROM read_parquet('{self._pattern(table)}', hive_partitioning=true, "
                f"union_by_name=true, hive_types_autocast=false) {clause} ORDER BY ingested_at, run_id"
            ).df()
        finally:
            con.close()

    def connect(self, as_of=None) -> duckdb.DuckDBPyConnection:
        """An in-memory DuckDB with one view per non-empty table (latest versions, optionally as of a date)."""
        con = duckdb.connect()
        for table in TABLES:
            if self.has(table):
                con.execute(f"CREATE VIEW {table} AS SELECT * FROM {self.sql_source(table, as_of)}")
        return con

    def coverage(self) -> pd.DataFrame:
        """Per table, origin and source: stored rows (restated versions count), first and last dates. Read straight from
        the files, without resolving versions: the option quotes would take seconds. What the Data screen shows."""
        frames = []
        for table in TABLES:
            if not self.has(table):
                continue
            con = duckdb.connect()
            try:
                frames.append(
                    con.execute(
                        f"SELECT '{table}' AS dataset, origin, source, count(*) AS n, min(obs_date) AS first, "
                        f"max(obs_date) AS last, max(ingested_at) AS ingested FROM read_parquet('{self._pattern(table)}', "
                        "union_by_name=true, hive_partitioning=false) GROUP BY origin, source ORDER BY source"
                    ).df()
                )
            finally:
                con.close()
        if not frames:
            return pd.DataFrame(columns=["dataset", "origin", "source", "n", "first", "last", "ingested"])
        return pd.concat(frames, ignore_index=True)


def _cast(value, kind: str, n: int):
    if value is None:
        return np.full(n, np.nan) if kind == "float" else pd.Series([None] * n, dtype=object).values
    if not isinstance(value, pd.Series):
        value = pd.Series(value)
    if kind == "float":
        return pd.to_numeric(value, errors="coerce").astype(float)
    if kind == "int":
        return pd.to_numeric(value, errors="coerce").astype("Int64")
    if kind == "date":
        return pd.to_datetime(value).dt.date
    if kind == "bool":
        return value.astype(bool)
    return value.astype(object).where(value.notna(), None)
