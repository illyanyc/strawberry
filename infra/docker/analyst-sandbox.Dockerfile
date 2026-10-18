# syntax=docker/dockerfile:1.10
# Analyst sandbox (AN-071, ADR-0043): runs candidate feature code from the AI analyst. Minimal on
# purpose: pinned Python + pinned numpy + the standalone worker. No tbcore, no assistant code, no
# evaluator modules, no shell tools beyond the base image. Run with network_mode: none, read-only.
#   docker build -f infra/docker/analyst-sandbox.Dockerfile .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

FROM ${PYTHON_IMAGE}
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# numpy pinned to the workspace lock (uv.lock); wheels only.
RUN pip install --only-binary=:all: numpy==2.5.4 && \
    groupadd --system --gid 10001 sandbox && useradd --system --uid 10001 --gid sandbox --no-create-home sandbox && \
    install -d -o 10001 -g 10001 -m 0700 /jobs
COPY services/assistant/sandbox/worker.py /opt/sandbox/worker.py
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "/opt/sandbox/worker.py", "watch", "/jobs"]
