# syntax=docker/dockerfile:1
# The `worker` image (ADR-0009): the job queue's worker, the only process that sees the GPU. Multi-stage: CUDA's
# `devel` image compiles the engine (nvcc, for sm_61 to sm_120: `OPTLAB_CUDA_ARCHS=portable`, since a build has no
# GPU to ask), CUDA's `base` image runs it (the engine links cudart statically and needs no other CUDA library).
# Python 3.14 is uv's managed build, in /opt/python in both stages. No NVIDIA driver inside: the host's (Windows',
# through WSL) is mounted by the NVIDIA Container Toolkit. Built from the repository's root:
#
#     docker build -f deploy/worker.Dockerfile -t optlab-worker .
FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM nvidia/cuda:12.9.1-devel-ubuntu24.04@sha256:020bc241a628776338f4d4053fed4c38f6f7f3d7eb5919fecb8de313bb8ba47c AS build
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
RUN uv sync --locked --no-dev --no-editable --extra yahoo --python 3.14.7

FROM nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:29e5e3425e2e0f5a4e97c9fb4695ba4887cd78210a43cf94c3bcafc6ab01c5e6
RUN useradd --system --uid 10001 --user-group --home-dir /nonexistent --shell /usr/sbin/nologin optlab \
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
