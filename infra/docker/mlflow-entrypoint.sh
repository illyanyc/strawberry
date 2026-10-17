#!/bin/sh
# Builds the backend-store URI from the mounted password inside the process only (never in Compose
# or `docker inspect`), then starts the MLflow server with a filesystem artifact store (D8).
set -eu
pw="$(cat /run/secrets/pg_mlflow_password)"
export MLFLOW_BACKEND_STORE_URI="postgresql+psycopg://mlflow:${pw}@${TB_DB_HOST:-postgres}:5432/mlflow"
unset pw
exec mlflow server \
  --host 0.0.0.0 --port 5000 \
  --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
  --artifacts-destination /mlflow/artifacts \
  --serve-artifacts \
  --allowed-hosts "mlflow:5000,mlflow,localhost:5000,127.0.0.1:5000" \
  --workers 2
