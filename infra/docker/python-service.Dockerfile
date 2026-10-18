# syntax=docker/dockerfile:1.10
# Hardened image for Python services (OP-040). Build context: repo root.
#   docker build -f infra/docker/python-service.Dockerfile --build-arg SERVICE=monitor .
# The uv workspace is installed non-editable into /app/.venv; the runtime image has no build tools.

ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
ARG SERVICE
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY contracts/gen/python/ contracts/gen/python/
COPY libs/py/ libs/py/
COPY services/assistant/ services/assistant/
COPY services/backtest/ services/backtest/
COPY services/broker-gateway-ibkr/ services/broker-gateway-ibkr/
COPY services/context-data/ services/context-data/
COPY services/ml/ services/ml/
COPY services/monitor/ services/monitor/
COPY services/optimizer/ services/optimizer/
COPY services/strategy-runtime/ services/strategy-runtime/
RUN --mount=type=cache,target=/root/.cache/uv \
    test -n "$SERVICE" && uv sync --frozen --no-dev --no-editable --package "$SERVICE" && \
    ln -s "/app/.venv/bin/$SERVICE" /app/service

FROM ${PYTHON_IMAGE}
# /var/lib/broker-gateway-ibkr is the IBKR adapter's execution cursor; a named volume mounted there
# inherits this ownership on first use, so the read-only, non-root container can write it.
# libgomp1: LightGBM's wheel links the system OpenMP runtime, and every service that hosts
# tbcore strategies loads it (model strategies, SM-015).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* && \
    groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app && \
    install -d -o 10001 -g 10001 -m 0700 /var/lib/broker-gateway-ibkr
COPY --from=build /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["/app/service"]
