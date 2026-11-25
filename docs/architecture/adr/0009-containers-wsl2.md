# ADR-0009: Linux containers in WSL2, with Docker Engine and the NVIDIA Container Toolkit

- Status: Accepted, 7 November 2026

## Context

The desk runs in containers, and every CUDA function must keep working. The machine runs Windows 11 with WSL2 Ubuntu.
Docker Desktop is excluded (licence and account). The Windows NVIDIA driver already provides CUDA to WSL; a Linux
driver inside WSL would break it.

## Decision

- **Installed inside Ubuntu, as root through `wsl -d Ubuntu -u root`**, by `deploy/wsl/install-docker.sh`: Docker
  Engine from Docker's signed apt repository, the NVIDIA Container Toolkit from NVIDIA's, `systemd` on.
- **Images** (`deploy/`), base images pinned by digest:
  - `api`: Python slim, the lock's packages installed as wheels, the engine built for the CPU only;
  - `worker`: built in CUDA's `devel` image for sm_61 to sm_120, run in its `base` image (the engine links cudart
    statically).

  Both run as uid 10001 on a read-only root with a tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, bounded memory
  and processes, `restart: unless-stopped`. Neither holds data: the `.dockerignore` is an allow-list.
- **Network**: the Docker daemon listens on its unix socket only, no container mounts the socket, every published port
  is `127.0.0.1:...` (and `"ip": "127.0.0.1"` in `daemon.json` for any port published without an address), WSL stays
  in NAT mode. Inside a container the API listens on the container's `0.0.0.0` (`OPTLAB_CONTAINER=true`, refused
  outside a container); WSL relays `127.0.0.1:8765` to Windows' loopback.
- **The lake and the database** live in WSL volumes ([ADR-0022](0022-lake-in-a-wsl-volume.md)).
- **Restart**: WSL stops its VM a few minutes after the last `wsl.exe` session, and nothing starts it after a Windows
  reboot. A Windows scheduled task, "optlab WSL" (`deploy/wsl/register-wsl-task.ps1`), keeps it up: it runs at the
  user's logon, without a window or administrator rights, holds a `wsl.exe ... sleep infinity` session and starts it
  again 30 s after it ends. Once WSL starts, systemd starts Docker and Docker the containers: the API is healthy about
  30 s later.

## Consequences

- Development stays possible natively on Windows (`optlab serve`, `optlab worker`); containers are the production
  path.
- Windows and Linux results differ in their last bits (libm, compilers); the golden numbers' tolerance across machines
  is in [ADR-0017](0017-tests-and-golden-numbers.md).
- CUDA in WSL goes through the paravirtualised GPU: the book's Greeks run 8 to 14 % slower than natively; the VaR shows
  no penalty beyond its day-to-day spread.

## Alternatives considered

- **Docker Desktop**: licence and account. Excluded.
- **Podman in WSL**: rootless by default, but GPU support through CDI is less trodden than the NVIDIA toolkit with
  Docker Engine.
- **No containers** (Windows services): no image to scan or sign, no reproducible runtime for a release.
