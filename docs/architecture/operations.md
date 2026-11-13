# Operations

How the desk runs on this machine: start and stop it, accounts, backups and their restoration, the demo data, the job
queue, and what to do when something goes wrong. Every command is `optlab ...` (`uv run optlab --help` lists them);
`--data DIR` before a command, or `OPTLAB_DATA`, picks the data folder, and every other path comes from its settings
(`<data>/config/optlab.env`, ADR-0012). Two ways to run it: natively on Windows (the first sections, for development)
and in containers in WSL (the production path, ADR-0009, section "Containers").

## Where things are

| What | Where (default) |
|---|---|
| data folder | `optlab-data` beside the main checkout (`C:\Users\Luca\Code\projets\optlab-data`) |
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

Ctrl+C stops it: the running jobs stop at their next batch and end `error` (Interrupted), never resumed; queued jobs
wait for the next start. The database migrates itself to the code's schema when it is opened (`optlab migrate` does
it alone, and imports a v0 folder with `--v0`).

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

A missed schedule (the machine was off) runs once at the next start of a worker. An admin edits or runs them:
`GET /api/v1/admin/pipelines`, `PATCH .../{name}` (`enabled`, `days`, `at`, `params`: `keep_days`, `keep`),
`POST .../{name}/run`; `GET /api/v1/admin/system` lists the last copies. A lake copy waits when the lake is being
updated. Its `MANIFEST.json`, written last, holds the size and sha256 of every file: a folder without it is an
unfinished copy, removed at the next one.

All of these live on the same disk as the data: a copy elsewhere (another disk) is Luca's to make, from
`<data>/backups`.

## Restore

```bash
uv run optlab restore latest                         # the newest copy of the database (nightly or manual)
uv run optlab restore <data>/backups/nightly/optlab-20261113-030000-000.sqlite
uv run optlab restore latest --lake                  # the newest copy of the lake
```

Stop the API and the worker first: a restoration refuses while a worker serves the database. It checks the copy
(SQLite's integrity check, a schema revision this code knows), asks for a confirmation (`--yes` skips it), saves the
database it replaces in `<data>/backups/pre-restore`, then migrates the copy to the code's revision. A lake copy is
checked file by file against its manifest; the current lake is moved aside (`<data>/lake.before-restore-<stamp>`),
never deleted. Both are tested (`packages/server/tests/test_cli.py`).

## Demo data

`optlab serve --demo-data` serves a data folder made only of made-up numbers (the synthetic lake, the simulated
calibration, a book of four trades struck on its past markets, an account `demo` whose password is printed once),
built in `~/.cache/optlab/demo` (the user's profile) the first time (about two minutes). `optlab demo data --to DIR [--password-stdin]`
builds it elsewhere, for the front's development and its end-to-end tests (handoff §7.7); then
`optlab --data DIR serve`. Nothing in it comes from a vendor.

## The job queue

```bash
uv run optlab jobs list                              # every user's, newest first
uv run optlab jobs wait <id>                         # follows its progress
uv run optlab jobs cancel <id>
uv run optlab risk var --as-job                      # leave a long run to the worker that serves the database
```

## Containers (WSL)

The production path (ADR-0009): two containers in WSL's Docker Engine, `api` (no GPU) and `worker` (the GPU, the
job queue, the schedules), on named volumes; `deploy/compose.yaml` holds the safety rules (ports on 127.0.0.1 only,
read-only root file systems, no capability, an unprivileged user, bounded memory). Every `docker` command runs in
Ubuntu as root: from Windows, prefix it with `wsl -d Ubuntu -u root --` (below, `D=deploy/compose.yaml`, run from the
repository through `/mnt/c`). Adding `luca` to the `docker` group would spare the prefix, but makes that account
root-equivalent in WSL: Luca's choice.

Install, once (done in A7; the undo command is in the script's header):

```bash
wsl -d Ubuntu -u root -- sh deploy/wsl/install-docker.sh     # Docker Engine, NVIDIA Container Toolkit, the daemon's rules
```

Build, fill the volumes, create an account, start:

```bash
docker compose -f $D build                                     # api about 1.5 min, worker about 3.5 min (CUDA for sm_61 to sm_120)
sh deploy/wsl/seed-volumes.sh /mnt/c/Users/Luca/Code/projets/optlab-data /mnt/c/<repository> [DATABASE]
docker compose -f $D run --rm --no-deps -it api users create <name> --roles admin
docker compose -f $D up -d                                     # http://127.0.0.1:8765/api/v1 from Windows
docker compose -f $D ps                                        # both "healthy"/"running"
docker compose -f $D exec worker nvidia-smi                    # the GPU, seen by the worker only
```

`seed-volumes.sh` copies the calibrations and the Windows lake into the volumes (32 s for the lake), and, when given
a database file, restores it into the `store` volume through `optlab restore` (checked; nothing is copied back to
Windows). From then on the containers' lake is theirs (ADR-0022): their worker updates it, and the Windows lake
stays the development one.

| Volume | Holds | api | worker |
|---|---|---|---|
| `optlab_store` | the database (SQLite in WAL: never on `/mnt/c`) | read-write | read-write |
| `optlab_lake` | the production lake | read-only | read-write |
| `optlab_archive` | the calibrations | read-only | read-write |
| `optlab_backups` | nightly, manual and lake copies | read-only | read-write |
| `optlab_logs` | `api.log`, `worker.log` (JSON lines) | read-write | read-write |
| `optlab_config` | `optlab.env` (settings, no secret today) | read-only | read-only |

Stop, update, logs:

```bash
docker compose -f $D down                                      # stops both; the volumes stay
git pull && docker compose -f $D up -d --build                 # until A9's `optlab deploy` (images from GHCR, rollback)
docker compose -f $D logs -f worker                            # the console; the JSON log files are in optlab_logs
```

Backups and restore in containers: the worker's schedules write into `optlab_backups` as on Windows. To restore,
stop both services first, then:

```bash
docker compose -f $D stop api worker
docker compose -f $D run --rm --no-deps worker restore latest --yes      # or: restore latest --lake --yes
docker compose -f $D up -d
```

To keep a copy outside WSL (another disk), copy the volume out:
`docker run --rm --network none --entrypoint cp -v optlab_backups:/b:ro -v /mnt/d/optlab-backups:/out optlab-worker:dev -R /b/. /out/`
(the worker's own image: no other image is pulled).

### After a reboot, and around the clock

WSL stops its VM a few minutes after its last `wsl.exe` session, and nothing starts it with Windows: the containers
would stop with it. The scheduled task **"optlab WSL"** (registered on 13 November 2026 with Luca's agreement) runs at
his logon, without a window, and holds a WSL session; when WSL stops anyway (`wsl --shutdown`, an update), it starts
it again within a minute. With WSL up, systemd starts Docker, and Docker the containers it ran (`restart:
unless-stopped`): the API answers about 30 s later. `docker compose down` stops them for good; the task only keeps WSL
running.

```powershell
powershell -ExecutionPolicy Bypass -File deploy\wsl\register-wsl-task.ps1     # register (or update) the task
Get-ScheduledTask -TaskName "optlab WSL"                                       # Running
Unregister-ScheduledTask -TaskName "optlab WSL" -Confirm:$false                # undo
```

The machine must not sleep for the schedules to run: Luca's setting (a missed schedule runs once at the next start).

### Open to the LAN (off; Luca's agreement first, ADR-0002)

The `lan` profile puts Caddy in front of the API with TLS by its internal CA, for the names of `OPTLAB_LAN_NAMES`
only (any other `Host` gets a 403). It needs three things outside the repository, each with Luca's agreement at that
moment:

1. WSL is behind NAT: a Windows port forward from the LAN address to WSL's
   (`netsh interface portproxy add v4tov4 listenaddress=<LAN IP> listenport=8443 connectaddress=<WSL IP>
   connectport=8443`, as administrator);
2. a Windows firewall rule for that port, the Private profile and the local subnet only;
3. each device that opens the desk trusts Caddy's root certificate once (`docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt .`).

```bash
OPTLAB_LAN=true OPTLAB_LAN_NAMES="optlab.home.arpa, 192.168.1.198" OPTLAB_LAN_ADDRESS=0.0.0.0 \
  docker compose -f $D --profile lan up -d
```

`GET /api/v1/admin/network` (netcheck) reads what still blocks it on Windows. Never a router port forward, UPnP or a
tunnel to this instance.

## When something goes wrong

| Symptom | Where to look, what to do |
|---|---|
| a job ended `error` | its message: `optlab jobs show <id>` or `GET /api/v1/jobs/{id}`; its trace: `<data>/logs/worker.log` (or `api.log` with the embedded worker) |
| jobs stay `queued` | no worker: `GET /api/v1/ready` (503), `GET /api/v1/admin/system` (`workers`); start `optlab worker` or the API without `OPTLAB_WORKER=external` |
| "another worker serves this database" | one is running (another terminal, a container); or wait 15 s after a crash |
| the database is damaged | `optlab restore latest` (after stopping everything) |
| a command prints `error: ...` | the library's message: a wrong option, a missing file or table (`optlab lake derive` before the steps that read it) |
| an account is locked | it unlocks after `OPTLAB_LOGIN_LOCKOUT_MINUTES` (15); or `optlab users password <name>` |

What Luca sets himself: the machine's sleep and power settings (a sleeping machine runs no schedule; a missed one
runs at the next start), any Windows firewall rule, and the LAN profile (ADR-0002).
