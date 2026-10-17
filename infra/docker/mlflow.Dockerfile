# syntax=docker/dockerfile:1.10
# MLflow 3 tracking server (D8): Postgres backend store, filesystem artifact store (no MinIO).

ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && VIRTUAL_ENV=/app/.venv uv pip install "mlflow==3.17.1" "psycopg[binary]==3.3.6"

FROM ${PYTHON_IMAGE}
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /tmp app
COPY --from=build /app /app
COPY infra/docker/mlflow-entrypoint.sh /app/entrypoint.sh
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 HOME=/tmp
USER 10001:10001
ENTRYPOINT ["/app/entrypoint.sh"]
