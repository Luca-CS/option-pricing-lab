# syntax=docker/dockerfile:1
# The `api` image (ADR-0009): Python 3.14, the six packages from the lock, the compiled engine for the CPU only (no
# CUDA: the GPU belongs to the worker), and the front's build. Built from the repository's root:
#
#     docker build -f deploy/api.Dockerfile -t optlab-api .
#
# Base images are pinned by digest in the FROM lines (where Dependabot finds them and proposes their updates); the process runs as an unprivileged user on a
# read-only root file system (compose.yaml), with its data on volumes under /data.
FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

# the front's static build (ADR-0024), served by the API on the same origin; Node stays in this stage
FROM node:26.13.1-trixie-slim@sha256:abe7e5a82b61a8ef67b315b85497c9e65173387ccf2f4a029248184b8e020be5 AS front
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY api/openapi.json /src/api/openapi.json
COPY frontend ./
RUN npm run build

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
# the distribution's security updates since the base image was built; no pip (the venv is uv's, and pip carries its
# own copies of certifi, urllib3 and others, which Trivy rightly counts)
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/python3.14/site-packages/pip /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14 /usr/local/lib/python3.14/ensurepip/_bundled \
 && useradd --system --uid 10001 --user-group --home-dir /nonexistent --shell /usr/sbin/nologin optlab \
 && mkdir -p /data/store /data/logs /data/lake /data/archive /data/config /data/backups \
 && chown -R 10001:10001 /data
COPY --from=build /opt/venv /opt/venv
COPY --from=front /src/frontend/dist /opt/frontend
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    OPTLAB_DATA=/data OPTLAB_CONTAINER=true OPTLAB_HOST=0.0.0.0 OPTLAB_FRONTEND=/opt/frontend
USER 10001:10001
WORKDIR /tmp
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=4).status != 200)"]
ENTRYPOINT ["optlab"]
CMD ["serve"]
