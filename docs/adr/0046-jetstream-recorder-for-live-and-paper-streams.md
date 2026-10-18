# ADR-0046: JetStream recorder for live and paper streams

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D64
- **Related:** MN-045, MN-037, BT-070, BT-071; ADR-0031 (two-tier backtester, `RecordedMessage`), ADR-0045; research [06 §2](../research/06-architecture-and-stack.md)

## Context

Daily divergence (section 13) and replay parity (section 08) need the exact bus stream a paper or live strategy saw. Only the in-process paper harness wrote `.tbrec` recordings (`tests/paper/recorder.go`); production had none. The `MARKETDATA` stream is memory-backed with one hour of retention, so bars cannot be pulled from JetStream after the fact, and the monitor's analytics windows were in memory and lost on every restart.

## Decision

1. **Where:** a recorder task inside the monitor process (`tb_monitor.recorder`), on durable JetStream pull consumers (`monitor-rec-<stream>`) so a restart resumes from the last acknowledged message within each stream's retention.
2. **What:** final and corrected bars (`md.bar.*.*`, `md.bar.correction.>`), feature vectors (`feat.>`), signals (`sig.>`), intents, decisions, and OMS orders (`ord.>`), executions (`exec.>`), kill events (`risk.kill`), ledger updates (`ledger.update.>`), and reconciliation results (`recon.result.>`). Configurable with `TB_RECORDER_SUBJECTS`.
3. **Format:** one file per XNYS session, `${TB_RECORDINGS_DIR}/<YYYY-MM-DD>.tbrec`, of length-delimited `tb.backtest.v1.RecordedMessage` (subject, payload, `Nats-Msg-Id`, recorded time, stream sequence), the format the backtester and `tbcore.parity` already read. Messages outside regular hours go to the session they follow.
4. **Durability and duplicates:** a message is acknowledged only after it is written and flushed; the file is fsynced every 5 s and at rollover. On start the recorder scans the current file for the highest stream sequence per stream and skips anything at or below it, so redelivery after a crash never duplicates a message.
5. **Completeness:** a JSON manifest per session (`<date>.json`, atomic rename) records start and stop times, counts by subject class, and gaps. A session is `complete` only when the recorder ran from before the open to after the close and no consumer fell behind its stream's first sequence (messages expired before delivery). Divergence on an incomplete recording is `INSUFFICIENT_DATA`, never a breach.
6. **Retention:** `TB_RECORDER_RETENTION_DAYS` (default 180, enough for swing graduation of at least three months); older sessions are deleted after the manifest is checked.
7. **Use:** the monitor replays the last `TB_MONITOR_HISTORY_SESSIONS` (default 60) session files at start to rebuild its windows, and runs the daily parity on the closed session's file.

## Alternatives considered

| Option | Why not |
|---|---|
| Increase `MARKETDATA` retention and pull from JetStream at parity time | Memory-backed stream sized for live use; a file archive is independent of NATS restarts |
| Separate recorder service | Another image and NATS user for one consumer loop; the monitor already consumes these streams and owns the parity run |
| Lake-backed recordings (Parquet) | The backtester's replay input is `RecordedMessage`; a second format would need a converter and could drift |
| Record everything (`>`) | Quotes and trades are high volume and unused by replay |

## Consequences

- Positive: production parity and divergence on the same format as the Phase 3 gate; monitor history survives restarts; recordings are reproducible inputs for event-driven reruns (N5).
- Negative / risks: about 20–50 MB per session with 1-minute bars over a few hundred names; a monitor outage longer than one hour loses bars for that session (the manifest marks it incomplete).
- Follow-up: include `data/recordings` in backups (restic path list).

## Validation

`services/monitor/tests/test_recorder.py`: round trip into `tbcore.backtest.recording`, no duplicates after redelivery, session rollover, completeness and gap marking, retention; the Gate 5 harness replays a recorded paper session through `compare_recording`.
