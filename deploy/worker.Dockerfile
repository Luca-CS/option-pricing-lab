# syntax=docker/dockerfile:1
# The `worker` image (ADR-0009): the job queue's worker, the only process that sees the GPU. Multi-stage: CUDA's
# `devel` image compiles the engine (nvcc, for sm_61 to sm_120: `OPTLAB_CUDA_ARCHS=portable`, since a build has no
# GPU to ask), CUDA's `base` image runs it (the engine links cudart statically and needs no other CUDA library).
# Python 3.14 is uv's managed build, in /opt/python in both stages. No NVIDIA driver inside: the host's (Windows',
# through WSL) is mounted by the NVIDIA Container Toolkit. Built from the repository's root:
#
#     docker build -f deploy/worker.Dockerfile -t optlab-worker .
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM nvidia/cuda:13.5.0-devel-ubuntu24.04@sha256:d3fa71793540a2b76ab227ee00016e754da2758b7a51b49bc8a72543af5a8bc2 AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ make cmake ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    OPTLAB_WITH_CUDA=ON OPTLAB_CUDA_ARCHS=portable
RUN uv python install 3.14.7
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages packages
RUN uv sync --locked --no-dev --no-editable --extra yahoo --python 3.14.7 \
 && rm -rf /opt/python/*/lib/python3.14/site-packages/pip /opt/python/*/lib/python3.14/site-packages/pip-*.dist-info \
           /opt/python/*/lib/python3.14/ensurepip/_bundled /opt/python/*/bin/pip*
# no pip at run time: the venv is uv's, and pip carries its own copies of certifi, urllib3 and others

FROM nvidia/cuda:13.5.0-base-ubuntu24.04@sha256:8c30b31b13a97ee353c21e666b227dea44f4331d38a221f257de70fc04b437d0
# the distribution's security updates since the base image was built (July 2025)
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --user-group --home-dir /nonexistent --shell /usr/sbin/nologin optlab \
 && mkdir -p /data/store /data/logs /data/lake /data/archive /data/config /data/backups \
 && chown -R 10001:10001 /data
COPY --from=build /opt/python /opt/python
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    OPTLAB_DATA=/data OPTLAB_CONTAINER=true OPTLAB_WORKER=external \
    NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["optlab"]
CMD ["worker", "--engine", "auto"]
