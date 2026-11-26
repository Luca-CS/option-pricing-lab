# Getting started: from a fresh machine to the desk in production

A step-by-step guide. Each level builds on the one before; stop at the one you need.

| Level | What you get | Time |
|---|---|---|
| [1. Install](#1-install) | the library, its command line and its tests | 5 to 10 minutes |
| [2. The library](#2-the-library) | prices, Greeks, calibrations and structured products from Python | minutes |
| [3. The desk on demo data](#3-the-desk-on-demo-data) | the whole desk in a browser, on made-up numbers | 5 minutes |
| [4. The desk on real data](#4-the-desk-on-real-data) | the lake of public market data, the SPX calibration, the showcase book | an hour or more |
| [5. Developing](#5-developing) | the front's dev server, the tests, the public demo | |
| [6. Production in containers](#6-production-in-containers) | the desk in Docker in WSL2, releases and rollbacks | an hour |

The public demo needs none of this: [luca-cs.github.io/option-pricing-lab](https://luca-cs.github.io/option-pricing-lab/).

Commands are written for a POSIX shell (Git Bash on Windows). In PowerShell, set an environment variable with
`$env:NAME = "value"` on its own line instead of the `NAME=value command` prefix.

## 1. Install

### Prerequisites

| Tool | Version | For |
|---|---|---|
| [Git](https://git-scm.com/) | any recent | the repository |
| [uv](https://docs.astral.sh/uv/) | 0.13 or later | Python 3.14 (uv installs it), the workspace and its lock |
| A C++17 compiler | Visual Studio 2022 Build Tools (Windows), gcc 11+ (Linux) | the native engine, built by `uv sync` |
| [Node.js](https://nodejs.org/) | 24 or later (26 LTS used here) | the web front (levels 3 to 6) |
| NVIDIA driver and CUDA 12.x | optional | the GPU engine; without it the engine builds for the CPU only |
| WSL2 with Ubuntu | optional | production in containers (level 6) |

### Get the code and build it

```bash
git clone https://github.com/Luca-CS/option-pricing-lab.git
cd option-pricing-lab
uv sync --locked --all-extras           # Python 3.14, every package, the dev tools; builds the native engine
```

`uv sync` compiles the C++ engine (about 80 s with CUDA, less without) and rebuilds it only when `packages/native`
changes. To change what it builds:

```bash
OPTLAB_WITH_CUDA=OFF uv sync --locked --all-extras        # CPU engines only, even if CUDA is installed
OPTLAB_CUDA_ARCHS=portable uv sync --locked --all-extras  # every GPU from sm_61 to sm_120, not just this machine's
```

### Check it

```bash
uv run optlab version                                         # the packages and the engines found: gpu, cpu, numpy
uv run pytest -q packages/core/tests/test_closed_forms.py     # a few seconds
uv run optlab --help                                          # every command
```

Contributors also install the hooks (ruff, format, gitleaks, file checks before each commit):

```bash
uv run pre-commit install
```

## 2. The library

Every pricer takes a model and a market; every Monte Carlo price comes with its standard error.

```python
from optlab.core.market import Market
from optlab.core.models import HestonModel
from optlab.core.heston import HestonParams
from optlab.core.products import barrier_mc

market = Market.flat(100.0, r=0.04, q=0.01)
model = HestonModel(HestonParams(v0=0.04, kappa=1.5, theta=0.04, xi=0.5, rho=-0.7))
price, stderr = barrier_mc(model, market, K=100, H=80, T=1.0, kind="down-out", cp="call")
```

A structured product, quoted on the versioned SPX calibration with the desk's pricing policy:

```python
from optlab.structured import PricingContext, preset, quote

ctx = PricingContext.from_calibration("results/spx_calibration.json")
q = quote(preset("phoenix", **{"coupon.barrier": 0.65}), ctx, with_greeks=True)
print(q.table())    # the policy and its reason, the coupon solved ± SE, the control model, the flows
print(q.script)     # the payoff script it compiles to
```

On the GPU or every CPU thread: pass `engine="gpu"`, `"cpu"` or `"auto"` to `price_many`, or set
`OPTLAB_ENGINE=auto`. The default stays NumPy, so the numbers do not depend on the machine.

Ready-made runs from the command line:

```bash
uv run optlab structured examples --quick    # example quotes, the autocall model-risk table, timings
uv run optlab calib run --source simulated   # the calibration chain on a simulated market: SSVI, SVI, Heston, SLV
uv run optlab bench accel                    # NumPy against the CPU and GPU engines
```

The library's internals: [architecture/library.md](architecture/library.md).

## 3. The desk on demo data

The fastest way to see everything: a data folder of made-up numbers (a synthetic lake, a simulated calibration, a
small book), built on first use.

Build the front once:

```bash
cd frontend
npm ci --ignore-scripts
npm run build
cd ..
```

Serve the desk with it:

```bash
OPTLAB_FRONTEND=frontend/dist uv run optlab serve --demo-data --engine auto --open
```

The first start builds the demo folder in `~/.cache/optlab/demo` (about two minutes) and **prints the password of
the `demo` account once**: keep it. The browser opens `http://127.0.0.1:8765`; sign in as `demo` (an admin).

A tour in ten minutes:

1. **Structuring › Builder**: pick the Phoenix, press **Quote**. Read "The pricing model" (the model the policy chose
   and why, its control, the model reserves) and the desk's lines (overvol, overskew, digital overhedges...).
2. **Book it** from the quote (an admin holds every role), then open the trade's page.
3. **Trading › Book**: **Value the book**. **Book risk**: **Run the Greeks**, then the vega by node (K × T): pick a
   node to see the trades behind it and open its surface.
4. **Risk › EOD runs**: backfill a few days, then **Trading › P&L explained** over them. **Risk › VaR**: a short run.
5. **Quant › Vol lab**: **Fit the models** to a day's option chain. **Replay lab**: live a product on past closes.
6. **Admin**: accounts, jobs, schedules, the audit log, the system.

Every long computation is a job: its progress replaces its button, it shows in the tray, and a notification says when
it is done. `Ctrl K` searches; `?` lists the keyboard shortcuts.

To give other people an account: **Admin › Users**, or

```bash
uv run optlab --data ~/.cache/optlab/demo users create alice --roles structurer,trader
```

The roles: `viewer`, `structurer`, `trader`, `risk`, `quant`, `admin`. The API alone answers on
`http://127.0.0.1:8765/api/v1`; `OPTLAB_DOCS=true` adds its interactive page at `/api/v1/docs`.

## 4. The desk on real data

### The data folder

The desk keeps its state in a data folder: `optlab-data` beside the checkout if it exists, else the repository's
`data/` (ignored by git), or whatever `OPTLAB_DATA` (or `--data DIR` before a command) names. Inside it: the database,
the lake, logs, backups, and `config/optlab.env` for settings ([architecture/operations.md](architecture/operations.md)).

```bash
mkdir ../optlab-data                                   # optional: keep the data beside the checkout
uv run optlab users create <name> --roles admin        # the password is asked twice, never an argument
OPTLAB_FRONTEND=frontend/dist uv run optlab serve --engine auto --workers 6 --open
```

The desk starts on the versioned SPX calibration (`results/spx_calibration.json`). It listens on `127.0.0.1` only.

### The lake (public market data)

```bash
uv run optlab lake update        # prices, dividends, rates (US, ECB, Japan, UK), Cboe and STOXX indices: public sources
uv run optlab lake derive        # curves, carry, the fictitious bank, surfaces, FX, correlations, pseudo-quotes
```

The first update downloads ten years of series; later ones fetch what is new. Yahoo's data are for personal use: the
lake stays outside the repository and is never published. Option chains: `--yahoo-chains` (during US market hours
only), `--import-sample <zip>` for the historicaldata.net sample, `--dolt <folder>` for a DoltHub clone.

What the lake enables: markets at any past date (Quant › Market explorer), the vol lab on real chains, replays on real
closes, end-of-day risk and P&L, the historical VaR.

### The real SPX calibration

```bash
uv run optlab calib download-spx         # a snapshot of the SPX option chain (personal use), in <data>/raw
uv run optlab calib run --source spx     # SSVI -> SVI -> Heston -> SLV, written to results/
uv run optlab calib report               # reports/calibration.md and its figures
```

### The showcase book

A dozen products dealt on past dates, on their real closes, in a store of their own:

```bash
uv run optlab demo seed-showcase
uv run optlab users create <name> --roles admin --database <data>/showcase/optlab.sqlite
OPTLAB_FRONTEND=frontend/dist uv run optlab serve --store <data>/showcase/optlab.sqlite --workers 6 --open
```

`seed-showcase` prints the market id to pass with `--market`. The guided tour: [reports/showcase.md](../reports/showcase.md).

### Risk from the command line

```bash
uv run optlab risk eod --date 2026-11-04          # the day's end-of-day run, stored
uv run optlab risk backfill --from 2026-10-01 --to 2026-11-04
uv run optlab risk pnl --from 2026-10-01 --to 2026-11-04
uv run optlab risk var                            # 1 day, 10 days, stressed
```

Add `--as-job` to leave a long run to the desk's worker.

## 5. Developing

### The front

```bash
cd frontend
npm run dev            # Vite on http://127.0.0.1:5173, /api proxied to the desk on 8765 (OPTLAB_API changes it)
npm run typecheck      # generates the API's types from api/openapi.json, then tsc
npm run lint           # Biome
npm test               # Vitest
```

Run a desk beside it (`uv run optlab serve --demo-data`). The design system is on `/dev/gallery` in development.
Principles, spaces, bridges and the design system: [ux.md](ux.md).

After changing a route of the API, export its document (a test fails on drift):

```bash
uv run optlab api export
```

### The tests

```bash
uv run pytest -m "not slow and not gpu and not lake and not native" -n auto --dist loadfile   # what CI runs, minutes
uv run pytest -n 6 --dist loadfile                  # the whole suite: the real lake, the GPU, node when present
uv run python tests/golden/golden.py check --local  # golden numbers, bit for bit
uv run ruff check . && uv run pyright && uv run lint-imports
```

End-to-end tests of the front run against a desk on a fresh demo folder:

```bash
uv run optlab demo data --to /tmp/optlab-e2e --password-stdin <<< "a-generated-password"
cd frontend
npx playwright install chromium                    # once
E2E_DATA=/tmp/optlab-e2e E2E_PASSWORD=a-generated-password npm run e2e
```

### The public demo

The demo is the same front built in demo mode, replaying sessions recorded on a desk:

```bash
cd frontend
npm run build:demo        # dist-demo/, under /option-pricing-lab/
npm run preview:demo      # serve it locally
npm run e2e:demo          # its tests
```

To record the sessions again (after a change to a screen or an answer the stories read), on a fresh demo folder:

```bash
E2E_DATA=<fresh demo folder> E2E_PASSWORD=<its password> npm run demo:record
```

Pages publishes it from `main` (`.github/workflows/pages.yml`).

## 6. Production in containers

The production path runs the desk in two containers in WSL2's Docker Engine: `api` (no GPU) and `worker` (the GPU,
the job queue, the schedules), on named volumes, with every port on `127.0.0.1`. From Windows, run each `docker`
command in Ubuntu as root: `wsl -d Ubuntu -u root -- <command>`, from the repository.

Once:

```bash
wsl -d Ubuntu -u root -- sh deploy/wsl/install-docker.sh                 # Docker Engine and the NVIDIA Container Toolkit
sh deploy/wsl/seed-volumes.sh /mnt/c/<data folder> /mnt/c/<repository>   # in WSL: the calibrations and the lake into the volumes
```

Then deploy a release, from the repository checked out at its tag:

```bash
sh deploy/wsl/deploy.sh v2.0.1           # pull by digest, copy the database, migrate, restart, check /ready
sh deploy/wsl/deploy.sh --status         # what runs, what ran before
sh deploy/wsl/deploy.sh --rollback       # the version before, with its database
```

A release is a tag `vX.Y.Z` on `main` whose packages carry that version: `.github/workflows/release.yml` builds the
images, pushes them to GHCR with their provenance, and lists their digests in the GitHub release.

Accounts, backups and restorations, the scheduled task that keeps WSL up, opening the desk to the LAN, and what to do
when something goes wrong: [architecture/operations.md](architecture/operations.md).

## Where next

The documentation map, from the overview down: [README.md](README.md).
