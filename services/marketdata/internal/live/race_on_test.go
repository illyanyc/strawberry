//go:build race

package live

// The race detector slows the pipeline 5-10x; latency targets are asserted only without it.
const raceEnabled = true
