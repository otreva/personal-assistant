# Personal Assistant Implementation Task Plan

This plan decomposes the Graphiti PRD into tightly scoped engineering tasks that can be executed and tracked independently. Tasks are grouped by milestone and sequenced to minimize cross-component blocking. Each task includes a goal, key steps, completion criteria, and dependencies.

## Milestone M0 — Core Graph & Poller

### 1. Project Skeleton & Local Configuration Baseline
- **Goal:** Establish repository structure, configuration loading, and local state directory per PRD Section 7.
- **Steps:**
  1. Create `graphiti` Python package with `__init__.py` and placeholder modules for `config`, `state`, `episodes`, and `cli`.
  2. Implement configuration loader supporting environment variables and `.env` file with defaults for Neo4j credentials, polling cadences, and group ID.
  3. Scaffold local state directory manager that ensures `~/.graphiti_sync/` exists with correct permissions and typed accessors for `tokens.json` and `state.json`.
- **Completion Criteria:** Running `python -m graphiti.cli status` prints loaded config values and confirms state directory path. Unit tests cover config fallback order and state directory creation.
- **Dependencies:** None.

### 2. Neo4j Connector & Episode Persistence Layer
- **Goal:** Provide reusable API for reading/writing episodes in Neo4j aligned with PRD Section 6.
- **Steps:**
  1. Add Neo4j driver dependency and connection factory using config from Task 1.
  2. Define episode data model (Pydantic or dataclass) capturing `group_id`, `source`, `valid_at`, `invalid_at`, `text`, `json`, and `metadata`.
  3. Implement persistence methods: `upsert_episode`, `invalidate_previous_version`, and `fetch_latest_episode_by_native_id` following deterministic keys from Appendix C.
  4. Write unit tests against an in-memory Neo4j test container (or mocked driver) verifying lineage updates.
- **Completion Criteria:** Test suite confirms episodes insert with correct keys, prior versions receive `invalid_at`, and retrieval returns expected node properties.
- **Dependencies:** Task 1.

### 3. Gmail Poller with History and Fallback Logic
- **Goal:** Implement Gmail ingestion honoring delta polling, fallback, and idempotency rules from PRD Section 4.1.
- **Steps:**
  1. Build Gmail API client wrapper handling OAuth token refresh using local `tokens.json`.
  2. Implement history polling loop using `users.history.list` with stored `last_history_id`; process new message IDs and fetch full message payloads.
  3. Handle 404/`historyId` invalidation by performing a fallback `newer_than:<N>d` search and reseeding `last_history_id`.
  4. Normalize messages into episode objects and persist via Task 2 API.
  5. Update `state.json` checkpoints and log counts per run.
- **Completion Criteria:** Integration test (with fixtures/mocks) demonstrates successful incremental run and recovery from invalid history ID scenario. CLI `sync gmail --once` processes mocked messages and updates state.
- **Dependencies:** Tasks 1–2.

### 4. Drive Poller with Full-Document Versioning
- **Goal:** Ingest Google Drive changes via `changes.list` and version entire documents per PRD Section 4.1.
- **Steps:**
  1. Add Drive client wrapper storing and refreshing `pageToken` in state.
  2. For each change, fetch full content (Docs API) or metadata fallback for unsupported MIME types.
  3. Create episode payloads keyed by `file_id` and `revisionId`/`modifiedTime`; persist using Task 2 methods.
  4. Implement tombstone episodes for deletions.
- **Completion Criteria:** Mocked integration test verifies new revisions create new episodes and deletions write tombstones. CLI `sync drive --once` updates state token.
- **Dependencies:** Tasks 1–2.

### 5. Calendar Poller with Sync Token Recovery
- **Goal:** Process Google Calendar events with delta sync and cancellation handling (PRD Section 4.1).
- **Steps:**
  1. Manage per-calendar `syncToken` persistence in `state.json`.
  2. Poll events via `events.list`, convert to structured episode payloads (store in `json` field).
  3. Handle recurring events, cancellations, and `410 Gone` by reseeding sync token.
- **Completion Criteria:** Tests confirm cancelled events create tombstone episodes and reseed logic works. CLI `sync calendar --once` updates state.
- **Dependencies:** Tasks 1–2.

### 6. CLI Sync Commands & Scheduler Stub
- **Goal:** Provide operator interface for one-off syncs and expose placeholders for launchd scheduling (PRD Section 3.1, 4.4).
- **Steps:**
  1. Extend CLI with subcommands `sync gmail|drive|calendar --once` invoking pollers from Tasks 3–5.
  2. Add `sync status` command summarizing last run times from state and health of connections.
  3. Implement scheduler stub that can be invoked hourly (actual launchd config documented, not automated yet).
- **Completion Criteria:** Manual CLI runs execute pollers, print metrics, and exit with status code 0. Unit tests cover CLI argument parsing.
- **Dependencies:** Tasks 1, 3–5.

## Milestone M1 — Slack Poller

### 7. Slack Client & Search Support
- **Goal:** Build Slack API wrapper for executing search queries per PRD Section 4.1.
- **Steps:**
  1. Implement token-based authentication and rate-limit aware HTTP client.
  2. Provide helpers for `search.messages` with pagination, timestamp filtering, and graceful handling of truncated results.
  3. Persist discovered channel and user metadata in local state for reuse and caching.
- **Completion Criteria:** Tests simulate search responses and confirm metadata caching. CLI `sync slack --list-channels` prints channel IDs.
- **Dependencies:** Task 1.

### 8. Slack Message Poller with Thread Support
- **Goal:** Poll channel histories and threads, creating per-message episodes (PRD Section 4.1, 4.4).
- **Steps:**
  1. For each channel from Task 7, call `conversations.history` with stored `last_seen_ts` and persist checkpoint after successful run.
  2. Fetch thread replies via `conversations.replies` when new thread messages appear, storing per-thread `last_seen_ts`.
  3. Normalize user messages into episode payloads and persist using Task 2 APIs; skip bot/system subtypes by default.
  4. Handle rate-limit (429) responses with exponential backoff.
- **Completion Criteria:** Mock-based test ensures new messages create episodes and checkpoints advance. CLI `sync slack --once` processes fixtures and updates state.
- **Dependencies:** Tasks 1–2, 7.

## Milestone M2 — MCP / Cursor Integration

### 9. MCP Episode Logger
- **Goal:** Capture MCP conversation turns as episodes in near real-time (PRD Section 4.1).
- **Steps:**
  1. Define lightweight logging API for MCP server to call with turn metadata.
  2. Normalize turn payloads into episodes keyed by `message_id` and `valid_at`.
  3. Provide batching or queue mechanism to persist turns without blocking the MCP thread.
- **Completion Criteria:** Unit tests confirm helper creates episodes with required metadata and persists them. Mock integration ensures queue flush writes to Neo4j.
- **Dependencies:** Tasks 1–2.

### 10. Cursor Retrieval Tools
- **Goal:** Expose Graphiti query capabilities to Cursor IDE (PRD Sections 3.1, 4.3).
- **Steps:**
  1. Implement query service supporting hybrid search, temporal as-of queries, and shortest path retrieval against Neo4j.
  2. Create MCP tool definitions that call these queries with validated parameters.
  3. Add tests verifying query results formatting and error handling for invalid inputs.
- **Completion Criteria:** Local MCP server exposes tools returning structured JSON; unit tests cover each query mode with fixture data.
- **Dependencies:** Tasks 1–2, 9.

## Milestone M3 — Quality & Operations

### 11. Payload Redaction & Summarization Hooks
- **Goal:** Provide configurable redaction and summarization prior to episode ingestion (PRD Section 8, 11).
- **Steps:**
  1. Implement regex-based redaction pipeline configurable via YAML or env var.
  2. Integrate optional summarization module for large documents/messages (pluggable LLM call or heuristic summarizer).
  3. Update pollers to run payloads through these hooks before persistence.
- **Completion Criteria:** Unit tests ensure sensitive strings are redacted and summarization triggers based on size thresholds.
- **Dependencies:** Tasks 3–5, 8, 9.

### 12. Health Endpoint & Status Dashboard
- **Goal:** Deliver observability tooling described in Appendix C and Milestone M3.
- **Steps:**
  1. Build lightweight FastAPI/Flask service exposing `/health` with last sync timestamps and error counts per source.
  2. Create CLI `status` enhancement that renders textual dashboard summarizing metrics and upcoming poll windows.
  3. Write tests for HTTP handler and CLI output formatting.
- **Completion Criteria:** Manual `curl http://localhost:PORT/health` returns JSON with required fields; tests validate serialization.
- **Dependencies:** Tasks 3–8.

### 13. Backup & Restore Scripts
- **Goal:** Automate backups of `~/.graphiti_sync/` and document restore process (PRD Section 11).
- **Steps:**
  1. Implement CLI commands `backup state` and `restore state` to tar/untar the state directory with timestamps.
  2. Ensure file permissions maintained on restore.
  3. Document usage and retention recommendations in `docs/ops.md`.
- **Completion Criteria:** Tests (or manual dry run) confirm backup archive created and restore recreates identical files with correct modes.
- **Dependencies:** Task 1.

## Cross-Cutting Tasks

### 14. End-to-End Acceptance Test Harness
- **Goal:** Validate PRD acceptance criteria (Section 10) via automated scenario tests.
- **Steps:**
  1. Create fixture dataset representing 14 days of Gmail/Drive/Calendar/Slack/MCP data.
  2. Script orchestrated run that seeds state, executes pollers, and runs representative queries.
  3. Assert episodes exist, versions update correctly, and query results satisfy AC-1 through AC-6.
- **Completion Criteria:** CI job runs harness and reports pass/fail; documentation describes how to execute locally.
- **Dependencies:** Tasks 3–10.

### 15. Deployment Documentation & Launchd Recipe
- **Goal:** Provide operators with detailed setup instructions for macOS deployment (supports PRD Section 3.1 and Definition of Done).
- **Steps:**
  1. Write `docs/deployment.md` covering prerequisites, OAuth setup, Neo4j Docker instructions, and initial sync commands.
  2. Include sample `launchd` plist files for poller scheduling and guidance on log rotation.
  3. Document troubleshooting tips for token expiry and network outages.
- **Completion Criteria:** Markdown docs reviewed; following steps on clean machine reproduces working environment.
- **Dependencies:** Tasks 1, 3–8.

## Recommended Sequencing Summary
1. Complete Tasks 1–6 to deliver Milestone M0 baseline.
2. Add Slack functionality via Tasks 7–8.
3. Implement MCP integration (Tasks 9–10).
4. Finish quality/ops enhancements (Tasks 11–13).
5. Capstone with cross-cutting Tasks 14–15 to ensure acceptance criteria and operational readiness.

Following this plan ensures each phase produces tangible value while maintaining alignment with the PRD’s goals, resiliency requirements, and definition of done.
