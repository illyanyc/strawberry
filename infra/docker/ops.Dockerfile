# syntax=docker/dockerfile:1.10
# Operator toolbox: tbctl (db/bus init, audit verify, secrets), pg_dump/pg_restore 17, age, sops.
# Used by the one-shot db-init/bus-init services and by make backup/restore/secrets targets.

ARG GO_IMAGE=golang:1.27-alpine@sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d
ARG ALPINE_IMAGE=alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8

FROM --platform=$BUILDPLATFORM ${GO_IMAGE} AS build
ARG TARGETOS TARGETARCH
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
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags "-s -w" -o /out/tbctl ./tools/tbctl

FROM ${ALPINE_IMAGE}
RUN apk add --no-cache postgresql17-client age sops tar zstd coreutils bash jq diffutils ca-certificates \
 && addgroup -S -g 10001 ops && adduser -S -u 10001 -G ops -H ops
COPY --from=build /out/tbctl /usr/local/bin/tbctl
COPY infra/ops/ /usr/local/lib/tb-ops/
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/tbctl"]
