# Operations

How to run the desk: start and stop it, accounts, backups and restorations, the demo data, the job queue, the
containers, releases, and what to do when something goes wrong.

Every command is `optlab ...` (`uv run optlab --help` lists them). `--data DIR` before a command, or `OPTLAB_DATA`,
picks the data folder; every other path comes from its settings (`<data>/config/optlab.env`,
[ADR-0012](adr/0012-settings.md)). There are two ways to run it:

- **natively on Windows**, for development: the sections up to "The job queue";
- **in containers in WSL**, the production path ([ADR-0009](adr/0009-containers-wsl2.md)): from "Containers" on. Their
  database and lake live in Docker volumes, apart from the Windows data folder, which stays the development one.

## Where things are

| What | Where (default) |
|---|---|
| data folder | `optlab-data` beside the checkout |
| database (trades, fixings, quotes, accounts, audit, jobs, schedules, risk runs) | `<data>/store/optlab.sqlite` |
| lake (Parquet and its raw files) | `<data>/lake` |
| showcase book | `<data>/showcase/optlab.sqlite` |
| logs (JSON lines, a file a day, 14 kept) | `<data>/logs/api.log`, `worker.log` |
| backups | `<data>/backups/nightly`, `manual`, `pre-restore`, `lake` |
| settings file (outside the repository) | `<data>/config/optlab.env` |

## Start and stop

```bash
uv sync --locked --all-extras                       # after a pull: the environment of the lock
uv run optlab serve                                 # http://127.0.0.1:8765/api/v1, its worker in the same process
uv run optlab serve --store <data>/showcase/optlab.sqlite --market <snapshot id> --workers 6 --open
```

The API serves the front when `OPTLAB_FRONTEND` names its build (`frontend/dist`). Ctrl+C stops it: the running jobs
stop at their next batch and end in error (Interrupted), never resumed; queued jobs wait for the next start. The
database migrates itself to the code's schema when it is opened; `optlab migrate` does it alone, and imports an old
data folder with `--v0`.

To run the worker on its own (the GPU in one process, the API in another):

```bash
OPTLAB_WORKER=external uv run optlab serve
uv run optlab worker --concurrency 2 --engine auto
```

One worker serves a database: a second one waits for the lease (15 s after the first one stops answering).
`GET /api/v1/ready` answers 503 while no worker serves the queue.

## Accounts

```bash
uv run optlab users create <name> --roles admin      # the password is asked twice, never an argument
uv run optlab users list
uv run optlab users roles <name> trader,risk         # its sessions are closed
uv run optlab users password <name>                  # a lost admin password is reset here
uv run optlab users disable <name>
```

Every command is in the audit log, as the system. `--database FILE` acts on another database (the showcase's).

## Backups

| Copy | When | Kept | Where |
|---|---|---|---|
| the database (`VACUUM INTO`, consistent while the desk runs) | every night at 03:00 (schedule `backup`) | 14 days | `<data>/backups/nightly` |
| the lake (unchanged files are hard links to the last copy) | Sundays at 04:00 (schedule `lake_backup`) | 4 copies | `<data>/backups/lake/<stamp>` |
| either, now | `optlab backup [--lake]` | until removed | `<data>/backups/manual`, `.../lake` |

A missed schedule (the machine was off or asleep) runs once at the next start of a worker. An admin edits or runs them
in Admin › Schedules (`GET /api/v1/admin/pipelines`, `PATCH .../{name}`, `POST .../{name}/run`). A lake copy waits
while the lake is being updated. Its `MANIFEST.json`, written last, holds the size and sha256 of every file: a folder
without it is an unfinished copy, removed at the next one.

These copies live on the same disk as the data: keep another copy of `<data>/backups` elsewhere.

## Restore

```bash
uv run optlab restore latest                         # the newest copy of the database (nightly or manual)
uv run optlab restore <data>/backups/nightly/optlab-20261113-030000-000.sqlite
uv run optlab restore latest --lake                  # the newest copy of the lake
```

Stop the API and the worker first: a restoration refuses while a worker serves the database. It checks the copy
(SQLite's integrity check, a schema revision this code knows), asks for a confirmation (`--yes` skips it), saves the
database it replaces in `<data>/backups/pre-restore`, then migrates the copy to the code's revision, and closes every
session. A lake copy is checked file by file against its manifest; the current lake is moved aside
(`<data>/lake.before-restore-<stamp>`), never deleted.

## Demo data

`optlab serve --demo-data` serves a data folder made only of made-up numbers: the synthetic lake (with option chains
and basket pseudo-quotes), the simulated calibration, a book of four trades struck on its past markets, and an account
`demo` whose password is printed once. It is built in `~/.cache/optlab/demo` the first time (about two minutes).
`optlab demo data --to DIR [--password-stdin]` builds it elsewhere, for the front's development and its end-to-end
tests; then `optlab --data DIR serve`. Nothing in it comes from a vendor.

## The job queue

```bash
uv run optlab jobs list                              # every user's, newest first
uv run optlab jobs wait <id>                         # follows its progress
uv run optlab jobs cancel <id>
uv run optlab risk var --as-job                      # leave a long run to the worker that serves the database
```

## Containers (WSL)

Two containers in WSL's Docker Engine, `api` (no GPU) and `worker` (the GPU, the job queue, the schedules), on named
volumes. `deploy/compose.yaml` holds the safety rules: ports on 127.0.0.1 only, read-only root file systems, no
capability, an unprivileged user, bounded memory. Every `docker` command runs in Ubuntu as root: from Windows, prefix
it with `wsl -d Ubuntu -u root --` (below, `D=deploy/compose.yaml`, run from the repository through `/mnt/c`). Adding
a user to the `docker` group would spare the prefix, but makes that account root-equivalent in WSL.

Install, once (the undo command is in the script's header):

```bash
wsl -d Ubuntu -u root -- sh deploy/wsl/install-docker.sh     # Docker Engine, NVIDIA Container Toolkit, the daemon's rules
```

Build, fill the volumes, create an account, start:

```bash
docker compose -f $D build                                     # api about 1.5 min, worker about 3.5 min (CUDA for sm_61 to sm_120)
sh deploy/wsl/seed-volumes.sh /mnt/c/<data folder> /mnt/c/<repository> [DATABASE]
docker compose -f $D run --rm --no-deps -it api users create <name> --roles admin    # before up; once up: exec -it api optlab users ...
docker compose -f $D up -d                                     # http://127.0.0.1:8765/api/v1 from Windows
docker compose -f $D ps                                        # both "healthy"/"running"
docker compose -f $D exec worker nvidia-smi                    # the GPU, seen by the worker only
```

`seed-volumes.sh` copies the calibrations and the Windows lake into the volumes (about 30 s for the lake), and, when
given a database file, restores it into the `store` volume through `optlab restore`. From then on the containers'
lake is theirs ([ADR-0022](adr/0022-lake-in-a-wsl-volume.md)): their worker updates it.

| Volume | Holds | api | worker |
|---|---|---|---|
| `optlab_store` | the database (SQLite in WAL: never on `/mnt/c`) | read-write | read-write |
| `optlab_lake` | the production lake | read-only | read-write |
| `optlab_archive` | the calibrations | read-only | read-write |
| `optlab_backups` | nightly, manual and lake copies | read-only | read-write |
| `optlab_logs` | `api.log`, `worker.log` (JSON lines) | read-write | read-write |
| `optlab_config` | `optlab.env` | read-only | read-only |

Stop, logs:

```bash
docker compose -f $D down                                      # stops both; the volumes stay
docker compose -f $D logs -f worker                            # the console; the JSON log files are in optlab_logs
```

Run the images through compose only: a bare `docker run -p` would not publish on 127.0.0.1.

### Releases, deploys and rollbacks

A release is a tag `vX.Y.Z` (or `vX.Y.Z-rc.N`) on `main`, whose packages carry the version `X.Y.Z`. Pushing the tag
runs `.github/workflows/release.yml`: the api and worker images are built from the tag, pushed to
`ghcr.io/luca-cs/optlab-api` and `optlab-worker` with their SBOM and a provenance attestation, and the GitHub release
lists their digests ([ADR-0019](adr/0019-cd-ghcr-manual-deploy.md)). Rebuilding a release is running that workflow on
its tag again: the lock (hashes) and the base images (digests) are in the tree.

Deploy, from the repository checked out at the release's tag (its `compose.yaml`), in WSL as root:

```bash
gh attestation verify oci://ghcr.io/luca-cs/optlab-api@sha256:<digest> --owner Luca-CS      # from Windows, once per release
sh deploy/wsl/deploy.sh vX.Y.Z          # pull by digest, copy the database, migrate, restart, check /ready
sh deploy/wsl/deploy.sh --status        # what runs, what ran before, the copy a rollback restores
sh deploy/wsl/deploy.sh --rollback      # the version before, with the database as it was before the last deploy
```

The script pulls the images by the digests of the GitHub release (never by a moving tag) and tags them
`optlab-api:<tag>` here; copies the database (SQLite's online backup, read-only on the live file) to
`backups/manual/optlab-<stamp>-before-<tag>.sqlite`; stops api and worker; lets the new worker image migrate the
database; starts both on the new images; and waits up to 180 s for `/api/v1/ready`. If a step after the copy fails, the
previous images come back with that copy and the script exits 1. `--rollback` does the same on purpose: whatever the
desk wrote since the deploy is lost with it (it stays in `backups/pre-restore`). The state (current tag, previous tag,
the copy) is in `/var/lib/optlab-deploy/optlab` in WSL. `OPTLAB_PROJECT=<name>` runs the same steps on another compose
project with its own volumes, for a drill; `--local TAG` deploys images already built here (`OPTLAB_TAG=TAG docker
compose -f $D build`) instead of pulling.

The scripts of `deploy/` keep LF line endings in a Windows checkout (`.gitattributes`): `sh` in WSL stops at a CRLF.

Backups and restorations in containers: the worker's schedules write into `optlab_backups`. To restore, stop both
services first, then:

```bash
docker compose -f $D stop api worker
docker compose -f $D run --rm --no-deps worker restore latest --yes      # or: restore latest --lake --yes
docker compose -f $D up -d
```

To keep a copy outside WSL (another disk), copy the volume out with the worker's own image:
`docker run --rm --network none --entrypoint cp -v optlab_backups:/b:ro -v /mnt/d/optlab-backups:/out optlab-worker:<tag> -R /b/. /out/`.

### After a reboot, and around the clock

WSL stops its VM a few minutes after its last `wsl.exe` session, and nothing starts it with Windows: the containers
would stop with it. The scheduled task **"optlab WSL"** runs at the user's logon, without a window or administrator
rights, and holds a WSL session; when WSL stops anyway (`wsl --shutdown`, an update), it starts it again within a
minute. With WSL up, systemd starts Docker, and Docker the containers it ran (`restart: unless-stopped`): the API
answers about 30 s later. `docker compose down` stops them for good; the task only keeps WSL running.

```powershell
powershell -ExecutionPolicy Bypass -File deploy\wsl\register-wsl-task.ps1     # register (or update) the task
Get-ScheduledTask -TaskName "optlab WSL"                                       # Running
Unregister-ScheduledTask -TaskName "optlab WSL" -Confirm:$false                # undo
```

The schedules need the machine awake; a missed one runs once at the next start.

### Open to the LAN (off by default)

The `lan` profile puts Caddy in front of the API with TLS by its internal CA, for the names of `OPTLAB_LAN_NAMES`
only (any other `Host` gets a 403). It needs three things outside the repository, each a deliberate choice of the
machine's owner ([ADR-0002](adr/0002-never-exposed.md)):

1. WSL is behind NAT: a Windows port forward from the LAN address to WSL's
   (`netsh interface portproxy add v4tov4 listenaddress=<LAN IP> listenport=8443 connectaddress=<WSL IP>
   connectport=8443`, as administrator);
2. a Windows firewall rule for that port, the Private profile and the local subnet only;
3. each device that opens the desk trusts Caddy's root certificate once (`docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .`).

```bash
OPTLAB_LAN=true OPTLAB_LAN_NAMES="optlab.home.arpa, <LAN IP>" OPTLAB_LAN_ADDRESS=0.0.0.0 \
  docker compose -f $D --profile lan up -d
```

Admin › System (`GET /api/v1/admin/network`) reads what still blocks it on Windows. Never a router port forward, UPnP
or a tunnel. Behind `netsh portproxy`, every LAN client reaches Caddy from the Windows host's address: the sign-in
throttle by address and the audit log then see one address (the per-account lockout still holds).

## When something goes wrong

| Symptom | Where to look, what to do |
|---|---|
| a job ended in error | its message: `optlab jobs show <id>` or `GET /api/v1/jobs/{id}`; its trace: `<data>/logs/worker.log` (or `api.log` with the embedded worker) |
| jobs stay queued | no worker: `GET /api/v1/ready` (503), Admin › System (`workers`); start `optlab worker` or the API without `OPTLAB_WORKER=external` |
| "another worker serves this database" | one is running (another terminal, a container); or wait 15 s after a crash |
| the database is damaged | `optlab restore latest` (after stopping everything) |
| a command prints `error: ...` | the library's message: a wrong option, a missing file or table (`optlab lake derive` before the steps that read it) |
| an account is locked | it unlocks after `OPTLAB_LOGIN_LOCKOUT_MINUTES` (15); or `optlab users password <name>` |
