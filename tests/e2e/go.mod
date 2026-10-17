module github.com/illyanyc/strawberry/tests/e2e

go 1.27.2

replace (
	github.com/illyanyc/strawberry/contracts/gen/go => ../../contracts/gen/go
	github.com/illyanyc/strawberry/libs/go => ../../libs/go
)

require github.com/illyanyc/strawberry/libs/go v0.0.0-00010101000000-000000000000

require (
	github.com/fxamacker/cbor/v2 v2.9.6 // indirect
	github.com/x448/float16 v0.8.4 // indirect
)
