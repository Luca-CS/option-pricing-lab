# ADR-0022: The containers' lake lives in a WSL volume

- Status: Proposed (A7, 13 November 2026), as ADR-0009 planned: decided by its measurement; for Luca to accept in
  the A7 pull request
- Follows: ADR-0009 ("the lake: A7 times one reference DuckDB query from `/mnt/c` and from WSL's ext4 disk")

## Context

The lake (340 MB of Parquet, append-only, read by DuckDB) is on Windows, in `optlab-data/lake`. A container in WSL
reads a Windows folder through `/mnt/c`, a 9P file share; a Docker volume is a folder of WSL's own ext4 disk.
ADR-0009 set the rule: above a 2x gap, the production copy lives in a WSL volume.

## Measurement

The same three reads in the `worker` image (`optlab-worker:dev`), best of five after a warm-up, twice. The same script
ran natively on Windows (NTFS) with the same code. The script is `deploy/wsl/lake_timing.py`: run it with
`python lake_timing.py <lake folder> <label>`.

| Read | `/mnt/c` (9P) | volume (ext4) | Windows native (NTFS) | 9P / ext4 |
|---|---|---|---|---|
| `Lake.coverage()`: every Parquet file of every table | 17.8 s | 0.96 s | 2.21 s | 19x |
| `read("prices_eod", SPX)`: the closes, versions resolved | 2.64 s | 0.054 s | 0.168 s | 49x |
| `market_at(SPX, 2026-11-04)`: curves, carry, surface | 7.35 s | 0.20 s | 0.52 s | 36x |

The second run gave the same figures within 12 %. A copy of the whole lake from `/mnt/c` into the volume took 32 s
(`deploy/wsl/seed-volumes.sh`).

## Decision

- **The containers' lake is the named volume `optlab_lake`** (ext4 in WSL). The `worker` writes it (the `lake` and
  `lake_backup` schedules, `optlab lake update`), and the `api` mounts it read-only.
- `deploy/wsl/seed-volumes.sh` fills it once from the Windows lake. After that the two lakes live apart: the containers'
  is the production one, and the Windows one serves development (`optlab serve` natively, the tests marked `lake`).
  Updating both is two commands, one per side, and nothing synchronises them behind anyone's back.
- The database is never on `/mnt/c` either. SQLite's WAL needs shared memory and the locks of a real local file
  system, which a 9P share does not give: it lives in the `optlab_store` volume.

## Consequences

- A container reads the lake 2 to 3 times faster than Windows does natively, and 19 to 49 times faster than through
  `/mnt/c`.
- Two lakes can drift apart. Each says what it holds, with the `origin` and the ingestion time of every row and the
  Data screen's coverage. The weekly copy (`lake_backup`) runs where the production lake is, in the containers.
- To refresh the development lake from the production one, or the reverse, copy a lake copy
  (`<data>/backups/lake/<stamp>`, checked by its manifest) with `optlab restore --lake` on the receiving side.

## Alternatives considered

- **Bind-mount `/mnt/c/.../optlab-data/lake`**: one lake, but 19 to 49 times slower on every read, against a rule of 2x.
- **Move the Windows lake into WSL** and let Windows read it through `\\wsl$`: the same 9P penalty, on the other side,
  and the development tools would depend on WSL running.
