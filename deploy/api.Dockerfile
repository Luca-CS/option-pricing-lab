# syntax=docker/dockerfile:1
# The `api` image (ADR-0009): Python 3.14, the six packages from the lock, the compiled engine for the CPU only (no
# CUDA: the GPU belongs to the worker). Built from the repository's root:
#
#     docker build -f deploy/api.Dockerfile -t optlab-api .
#
# Base images are pinned by digest in the FROM lines (where Dependabot finds them and proposes their updates); the process runs as an unprivileged user on a
# read-only root file system (compose.yaml), with its data on volumes under /data.
FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM python:3.14.7-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ make cmake \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    OPTLAB_WITH_CUDA=OFF
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages packages
# the lock with its hashes, the runtime only (no dev tools), installed as wheels: the image holds no source tree
RUN uv sync --locked --no-dev --no-editable --extra yahoo --python /usr/local/bin/python3.14

FROM python:3.14.7-slim-trixie@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
RUN useradd --system --uid 10001 --user-group --home-dir /nonexistent --shell /usr/sbin/nologin optlab \
 && mkdir -p /data/store /data/logs /data/lake /data/archive /data/config /data/backups \
 && chown -R 10001:10001 /data
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    OPTLAB_DATA=/data OPTLAB_CONTAINER=true OPTLAB_HOST=0.0.0.0
USER 10001:10001
WORKDIR /tmp
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=4).status != 200)"]
ENTRYPOINT ["optlab"]
CMD ["serve"]
