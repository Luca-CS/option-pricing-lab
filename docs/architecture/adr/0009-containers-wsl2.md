# ADR-0009: Linux containers in WSL2, with Docker Engine and the NVIDIA Container Toolkit

- Status: Accepted (A0 checkpoint, 7 November 2026); the lake's location was decided by A7's measurement (ADR-0022)
- Handoff §1: Exécution; §4.7

## Context

Answer 12: containers, and every CUDA function must keep working. WSL2 Ubuntu is installed (stopped). No Docker
Desktop: its licence and account are excluded (answer 1). The Windows NVIDIA driver (617.42) already provides CUDA to
WSL; installing a Linux driver inside WSL would break it.

## Decision

- **Install, inside Ubuntu, as root through `wsl -d Ubuntu -u root`** (no password typed): Docker Engine from
  Docker's official apt repository, the NVIDIA Container Toolkit from NVIDIA's repository, `systemd` enabled in
  `/etc/wsl.conf`. A `.wslconfig` (example in `deploy/`) bounds memory and processors (for example 16 GB of 24). Each
  installation is logged with the command that undoes it.
- **Images**: `api` on Python slim with `uv`, no CUDA; `worker` built in stages, `nvidia/cuda:12.9-devel` to compile,
  `-runtime` to run. Non-root user, read-only root filesystem with a `tmpfs`, `no-new-privileges`, `cap_drop: ALL`,
  resource limits, base images pinned by digest.
- **Network**: the Docker daemon listens on its unix socket only (never `tcp://2375`), no container mounts the socket,
  every published port is `127.0.0.1:...`, WSL stays in NAT mode.
- **Restart**: `restart: unless-stopped`. A7 measures whether WSL stops without an open terminal and whether the stack
  comes back after a Windows reboot. If a Windows scheduled task is needed to start WSL at boot, Luca is asked at that
  moment.
- **The lake**: A7 times one reference DuckDB query from `/mnt/c` (9P) and from WSL's ext4 disk. Above a 2x gap the
  production copy lives in a WSL volume (339 MB), fed by the worker, and Windows development reads a copy; below, the
  worker mounts `optlab-data/lake` from `/mnt/c`. A follow-up ADR records the numbers.
- Checked in containers: golden numbers (tolerance of `0017`), times of the showcase Greeks, VaR and a quote against
  the Windows reference, `nvidia-smi` in the worker, and that the VaR runs on the GPU.

## Consequences

- Development stays possible natively on Windows (`optlab serve`, `optlab worker`); containers are the production
  path.
- Windows and Linux results differ in the last bits (libm, compiler): A0 and A7 measure the gap, then a relative
  tolerance is fixed and written down (ADR-0017).
- `docs/architecture/operations.md` (A7) covers install, start, stop, update, backup, restore, opening to the LAN and
  troubleshooting, and lists what Luca sets himself (sleep).

## Alternatives considered

- **Docker Desktop**: licence and account. Excluded.
- **Podman in WSL**: rootless by default, but GPU support through CDI is less trodden than the NVIDIA toolkit with
  Docker Engine; can be reconsidered.
- **No containers** (Windows services): no image to sign or scan, no reproducible runtime for the release.

## Implementation (A7)

- Installed in WSL Ubuntu 24.04 (systemd already on) by `deploy/wsl/install-docker.sh`, as root through
  `wsl -d Ubuntu -u root`: Docker Engine 29.9.3 and Compose v5.7.1 from Docker's signed repository, NVIDIA Container
  Toolkit 1.20.2 from NVIDIA's. The daemon listens on its unix socket only (checked: no tcp port), and a port published
  without an address binds 127.0.0.1 (`"ip": "127.0.0.1"` in `daemon.json`). No `.wslconfig` was written: WSL's
  defaults (half the RAM, 11 GB here; every processor) ran everything below; `deploy/wsl/wslconfig.example` is
  Luca's to use.
- Images (`deploy/`), base images pinned by digest in the `FROM` lines (Dependabot, ecosystems `docker` and
  `docker-compose`):
  - `api`: `python:3.14.7-slim-trixie`, the lock's runtime packages installed as wheels, the engine built for the CPU;
    1.1 GB, built in 85 s.
  - `worker`: built in CUDA 12.9.1's `devel` image for sm_61 to sm_120 (`OPTLAB_CUDA_ARCHS=portable`), run in its
    `base` image (the engine links cudart statically and needs no other CUDA library: 1.69 GB, not the 6.3 GB of
    `runtime`); uv's Python 3.14.7; built in 200 s.
  - Both run as uid 10001 on a read-only root with a tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, bounded
    memory and processes, `restart: unless-stopped` (checked with `docker inspect`). Neither holds data: the
    `.dockerignore` lets only `pyproject.toml`, `uv.lock` and `packages/` (without tests) in.
- Inside a container the API listens on 0.0.0.0 of the container (`OPTLAB_CONTAINER=true`, the only case `Settings`
  allows it without the LAN profile); compose publishes it on `127.0.0.1:8765` of WSL, which WSL relays to Windows'
  loopback. Checked from Windows: `127.0.0.1:8767` answers, the LAN address (192.168.1.198) and WSL's vEthernet
  address refuse the connection, a foreign `Host` gets a 403.
- The lake: ADR-0022 (a WSL volume; 9P is 19 to 49 times slower). The database is in a volume too (WAL on 9P is
  unsafe).
- The GPU in the worker: `nvidia-smi` sees the RTX 2070 Super (driver 617.42), the engine reports `gpu, cpu (12
  threads), numpy`; the api's image has `cpu, numpy` only.
- Golden numbers in the worker image (copies of the showcase and of the local golden files in a volume, the lake and
  calibrations read-only), with ADR-0017's rule across machines:
  - `closed_forms`, `mc_products`, `presets_simulated`, `presets_spx_numpy`, `presets_spx_gpu`, `pnl_cascade`: within
    1e-12;
  - `calibration_simulated` and `structured_extra`: the two gaps A2 measured under Linux, identical (an American put on
    a recalibrated SLV leverage 0.316 SE, the Phoenix's SLV provision 0.128 SE; non-blocking in CI as decided);
  - `native_gpu`: the fixed products to 0 SE (prices within 2e-6), the float paths within 1.4e-7 (local vol) and
    2.8e-6 (Heston), the SLV paths and the leverage, recalibrated, within 1 %;
  - `native_cpu`: the CPU reference of the CUDA study draws its normals with `std::normal_distribution`, whose
    sequence differs between MSVC and libstdc++: its fixed products are independent samples on each platform (gaps of
    0.01 to 2.7 SE, two independent estimates); the path engine (Philox normals, double) agrees within 3e-9 (local
    vol, baskets) and 2.8e-6 (Heston), the Phoenix's coupon within 2e-10, the leverage within 1.5 %;
  - `lake_derived`: values within 1e-12, one snapshot id differs (a content hash over the floats' last bits);
  - `showcase_book`: within 1e-12 except Greeks of the order of 1e-13 (zero up to rounding: a rho bucket of a position
    that does not depend on that rate);
  - `showcase_var`: within 1.2e-10 relative (well under 0.01 SE): the VaR ran on the GPU in the container, as its
    float paths agree with the Windows GPU run where NumPy's double paths could not.
- Times, measured back to back on this machine (the GPU warm on the second run):
  - the showcase's book and Greeks: 204 and 213 s in the container, 189 and 187 s natively on Windows: 8 to 14 %
    slower in WSL, where CUDA goes through the paravirtualised GPU (each launch and copy crosses the VM);
  - the showcase's VaR (`engine=gpu` in its five processes): 867 s in the container, 900 s natively the
    same day (that run overlapped the containers' start for a few minutes); A6 had measured 702 s natively on
    another day: no container penalty shows beyond the spread from one day to the next.
- Restart: WSL stops its VM within 3 minutes of the last `wsl.exe` session (measured: stopped at 3 and 8 minutes, the
  API silent), and nothing starts it after a Windows reboot. Once WSL starts, systemd starts Docker and Docker the two
  containers: the API is healthy 30 s after `wsl.exe` is called (13 s of VM boot).
- So a Windows scheduled task, "optlab WSL" (`deploy/wsl/register-wsl-task.ps1`), keeps WSL up. It runs at Luca's
  logon, as Luca, without a window or administrator rights. Its loop holds a `wsl.exe ... sleep infinity` session and
  starts it again 30 s after it ends. Luca agreed in the chat on 13 November 2026 (handoff §0), and it was registered
  that day. Measured:
  - with the task, WSL was still running 4.5 minutes later, the API answering;
  - after `wsl --shutdown`, WSL and the API were back within 52 s.

  A first version without the loop stayed stopped after `wsl --shutdown`: the scheduler restarts a failed task, not one
  that ended with code 0. A Windows reboot itself was not tested (it is Luca's machine); the logon trigger covers it,
  to be confirmed at the next reboot.
- Luca's real instance stays native on Windows until A9 (`optlab deploy`, his answer of 13 November 2026). The
  containers ran on copies; the test stack is down, and its volumes hold the seeded lake and calibrations and an
  empty test database.
