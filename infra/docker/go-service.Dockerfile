# syntax=docker/dockerfile:1.10
# Hardened image for every Go service (OP-040). Build context: repo root.
#   docker build -f infra/docker/go-service.Dockerfile --build-arg SERVICE=ledger --target service .
#   docker build -f infra/docker/go-service.Dockerfile --build-arg SERVICE=api-gateway --target gateway .
# Go and Node stages run on the build platform and cross-compile, so multi-arch (linux/arm64 for the
# Mac Mini, linux/amd64 for CI) builds need no emulation.

ARG GO_IMAGE=golang:1.27-alpine@sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d
ARG NODE_IMAGE=node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402
ARG RUNTIME_IMAGE=gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS build
ARG TARGETOS TARGETARCH
ARG SERVICE
ARG VERSION=dev
WORKDIR /src
COPY go.work go.work.sum ./
COPY contracts/gen/go/ contracts/gen/go/
COPY libs/go/ libs/go/
COPY tools/tbctl/ tools/tbctl/
COPY tests/e2e/ tests/e2e/
COPY services/api-gateway/ services/api-gateway/
COPY services/broker-gateway/ services/broker-gateway/
COPY services/execution/ services/execution/
COPY services/ledger/ services/ledger/
COPY services/marketdata/ services/marketdata/
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    test -n "$SERVICE" && \
    if [ "$SERVICE" = "tbctl" ]; then pkg=./tools/tbctl; else pkg=./services/${SERVICE}/cmd/${SERVICE}; fi && \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath \
      -ldflags "-s -w -X github.com/illyanyc/strawberry/libs/go/ops.Version=${VERSION}" \
      -o /out/service "$pkg"

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS web
WORKDIR /src
RUN corepack enable && corepack prepare pnpm@10.33.4 --activate
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
COPY contracts/gen/ts/package.json contracts/gen/ts/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --filter "@strawberry/web..."
COPY apps/web/ apps/web/
RUN pnpm --filter @strawberry/web build

# Plain Go service: static binary on distroless (no shell, non-root, CA certs only).
FROM ${RUNTIME_IMAGE} AS service
COPY --from=build /out/service /app/service
USER 65532:65532
ENTRYPOINT ["/app/service"]

# api-gateway: the same binary plus the static SPA build.
FROM ${RUNTIME_IMAGE} AS gateway
COPY --from=build /out/service /app/service
COPY --from=web /src/apps/web/dist /app/web
USER 65532:65532
ENTRYPOINT ["/app/service"]
