# ADR-0009: Linux containers in WSL2, with Docker Engine and the NVIDIA Container Toolkit

- Status: Accepted (A0 checkpoint, 7 November 2026); the lake's location is decided by a measurement in A7
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
