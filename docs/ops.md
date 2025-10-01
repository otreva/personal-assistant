# Graphiti Operations Guide

This document outlines day-to-day operational tasks for running Graphiti in a local environment, including observability, data hygiene, and backup/restore workflows.

## 1. Health Monitoring

- Run `graphiti sync status` to view a textual dashboard summarising the last successful run for Gmail, Drive, Calendar, Slack, and MCP.
- Append `--json` to emit the raw payload for scripting integrations.
- Start the lightweight health service by embedding `graphiti.health.create_health_app()` into a WSGI/FastAPI runner. The `/health` endpoint returns a JSON document with last run timestamps, error counters, and overall status (`ok`, `stale`, or `error`).
- Investigate any source marked as `stale` (no run within two polling intervals) or `error` (non-zero error count).

## 2. Payload Redaction & Summarisation

- Configure regex redactions via the `REDACTION_RULES` environment variable (JSON array or `pattern=>replacement` pairs) or a YAML/JSON file referenced by `REDACTION_RULES_PATH`.
- Summarisation is enabled by default for text bodies longer than `SUMMARY_THRESHOLD` (default 4000 characters). Adjust with the following environment variables:
  - `SUMMARY_THRESHOLD`: characters before summarisation triggers.
  - `SUMMARY_MAX_CHARS`: maximum summary length.
  - `SUMMARY_SENTENCE_COUNT`: number of leading sentences retained in the heuristic summariser.
- Transformation metadata is attached to each episode under `metadata.graphiti_processing` for auditability.

## 3. Backup & Restore

Graphiti stores OAuth tokens and poller checkpoints under `~/.graphiti_sync/`. Regular backups protect against accidental deletion or workstation migrations.

### 3.1 Creating Backups

```bash
graphiti backup state --output ~/Backups
```

- If `--output` is omitted, the archive is created in the current working directory.
- The command emits the final archive path (e.g. `graphiti-state-YYYYmmddHHMMSS.tar.gz`).
- Recommended cadence: nightly via `launchd` or a cron-equivalent.

### 3.2 Restoring from Backups

```bash
graphiti restore state /path/to/graphiti-state-20240101120000.tar.gz
```

- Existing state contents are replaced with the archive contents after safety validation (no path traversal).
- File permissions are reset to `0700` for directories and `0600` for files to preserve local-only access.
- After restoring, rerun the relevant `graphiti sync ... --once` command to resume polling from the restored checkpoints.

### 3.3 Retention Recommendations

- Keep at least seven days of rolling backups for disaster recovery and short-term audit trails.
- Copy archives to an encrypted external volume for off-machine redundancy if desired. Never place backups in a shared cloud folder because they contain OAuth tokens and checkpoint metadata.

## 4. Incident Runbook

- **Health endpoint reports `stale`:** run the associated `graphiti sync <source> --once` command. If it fails, inspect `~/.graphiti_sync/state.json` for corrupted cursors and restore from the latest backup.
- **Authentication failures:** delete only the provider-specific token entry in `tokens.json`, rerun the poller, and complete OAuth re-authentication when prompted.
- **Disk usage growth:** review the size of `graphiti-state-*.tar.gz` archives and prune old backups beyond the retention window.

Maintaining these operational habits ensures Graphiti remains resilient, auditable, and recoverable even when offline for extended periods.

