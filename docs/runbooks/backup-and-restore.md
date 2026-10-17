# Runbook: backup and restore

Rules OP-050, OP-051, SE-024. Alerts: `BackupStale`, `BackupNeverRun`.

## Back up
`make backup` writes `backups/strawberry-<UTC>.tar.zst.age`, containing logical dumps of `platform`, `mlflow`, `prefect`, role definitions (no passwords), the lake and MLflow artifacts, per-table checksums, the audit head, and `SHA256SUMS`, encrypted to `.secrets/backup_age_recipient`. It also updates `tb_backup_last_success_timestamp_seconds`.

Schedule it (until Prefect jobs exist) with a LaunchDaemon or cron on the host running `make -C <repo> backup` nightly. Copy backups off-host (Phase 6 adds restic to DigitalOcean Spaces).

## Restore
1. `make up` (fresh or existing stack). Secrets must exist (`make secrets-decrypt AGE_KEY_FILE=...` on a new host).
2. `make restore BACKUP=backups/strawberry-<UTC>.tar.zst.age` stops app services, decrypts with `.secrets/backup_age_identity` (or set `BACKUP_IDENTITY`), verifies file checksums, restores databases and files, then requires table checksums and the audit head to match the manifest before restarting services.

## Drill
`make backup-drill`: backup → destroy all volumes and move the lake aside → fresh `make up` → restore → verify tables, audit chain, and lake byte-for-byte. Run monthly and after any schema change.

## If verification fails
Do not start trading services. Keep the failed stack for analysis (`make down`, not `make clean`), restore an older backup into a scratch clone, and compare `tbctl db checksum` output.
