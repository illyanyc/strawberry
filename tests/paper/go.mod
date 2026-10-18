module github.com/illyanyc/strawberry/tests/paper

go 1.27.2

replace (
	github.com/illyanyc/strawberry/contracts/gen/go => ../../contracts/gen/go
	github.com/illyanyc/strawberry/libs/go => ../../libs/go
	github.com/illyanyc/strawberry/services/broker-gateway => ../../services/broker-gateway
	github.com/illyanyc/strawberry/services/execution => ../../services/execution
	github.com/illyanyc/strawberry/services/ledger => ../../services/ledger
)

require (
	github.com/illyanyc/strawberry/contracts/gen/go v0.0.0-00010101000000-000000000000
	github.com/illyanyc/strawberry/libs/go v0.0.0-00010101000000-000000000000
	github.com/illyanyc/strawberry/services/broker-gateway v0.0.0-00010101000000-000000000000
	github.com/illyanyc/strawberry/services/execution v0.0.0-00010101000000-000000000000
	github.com/illyanyc/strawberry/services/ledger v0.0.0-00010101000000-000000000000
	github.com/nats-io/nats-server/v2 v2.15.1
	github.com/nats-io/nats.go v1.54.0
	github.com/oklog/ulid/v2 v2.1.2
	github.com/shopspring/decimal v1.5.0
	google.golang.org/grpc v1.84.0
	google.golang.org/protobuf v1.36.12
)

require (
	github.com/antithesishq/antithesis-sdk-go v0.8.0-default-no-op // indirect
	github.com/beorn7/perks v1.0.1 // indirect
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/google/go-tpm v0.9.8 // indirect
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	github.com/jackc/pgx/v5 v5.11.0 // indirect
	github.com/jackc/puddle/v2 v2.2.2 // indirect
	github.com/klauspost/compress v1.20.1 // indirect
	github.com/minio/highwayhash v1.0.4 // indirect
	github.com/munnerz/goautoneg v0.0.0-20191010083416-a7dc8b61c822 // indirect
	github.com/nats-io/jwt/v2 v2.8.2 // indirect
	github.com/nats-io/nkeys v0.4.16 // indirect
	github.com/nats-io/nuid v1.0.1 // indirect
	github.com/prometheus/client_golang v1.25.0 // indirect
	github.com/prometheus/client_model v0.6.3 // indirect
	github.com/prometheus/common v0.72.0 // indirect
	github.com/prometheus/procfs v0.22.0 // indirect
	go.opentelemetry.io/otel v1.47.0 // indirect
	go.opentelemetry.io/otel/trace v1.47.0 // indirect
	golang.org/x/crypto v0.58.0 // indirect
	golang.org/x/net v0.60.0 // indirect
	golang.org/x/sync v0.24.0 // indirect
	golang.org/x/sys v0.49.0 // indirect
	golang.org/x/text v0.43.0 // indirect
	golang.org/x/time v0.16.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260928230214-8a89bd6388cc // indirect
)
