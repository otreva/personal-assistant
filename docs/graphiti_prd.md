# Personal + Enterprise Assistant Memory (Graphiti)

## 0. TL;DR
Build a local, privacy-first knowledge graph that continuously ingests Gmail, Google Drive, Google Calendar, and Slack (via polling), and logs all MCP/Cursor chats as episodes into Graphiti. The assistant can then answer time-aware questions across mail, docs, calendar, Slack, and prior chats. The system runs locally on a MacBook, is resilient to being offline for days, and requires no Google Cloud Platform services.

## 1. Goals & Non-Goals
### 1.1 Goals
- Unified, temporal memory across Gmail, Drive, Calendar, Slack, and MCP.
- Hourly polling (configurable) with reliable resume after downtime.
- Simple ingestion model with full-document replacement on Drive changes; version each item as a new episode.
- Deterministic idempotency using each system’s native IDs and version markers.
- Production-ready query surface: semantic, keyword, and graph queries; as-of temporal queries; shortest paths.

### 1.2 Non-Goals (Phase 1)
- No cloud push/webhooks (Pub/Sub/Cloud Run).
- No ERP/WMS integration.
- No per-paragraph diffing of Google Docs (can add later).
- No GUI admin; configuration via files or environment variables.

## 2. Personas & Top Use Cases
- **Mike (IC/Product Lead):** “Prep my 1:1” → last 14 days of related Gmail threads, Slack mentions, Drive docs, Calendar items, plus prior agent notes.
- **Mike (Founder/Planner):** “What changed in Supplier Scorecard last month?” → timeline of doc versions and relevant emails and internal chats.
- **Mike (Ops):** “Who owns OMS exceptions now?” → shortest path to owner across entities and episodes.

## 3. System Overview
### 3.1 Components
- **Local Graph DB:** Neo4j (Docker) or FalkorDB.
- **Graphiti Library:** entity extraction, semantic/keyword retrieval, temporal modeling.
- **Poller Daemon (macOS):** hourly (Launchd); polls Gmail, Drive, and Calendar; Slack more frequently (15–60s) or hourly when idle.
- **MCP/Cursor Hook:** logs user/assistant/tool turns as episodes; provides retrieval tools to the IDE.

### 3.2 High-Level Flow
1. Poller authenticates to Google and Slack via user tokens.
2. For each source, load deltas since last checkpoint (historyId/pageToken/syncToken/timestamp).
3. Normalize to Graphiti episodes and ingest.
4. MCP server logs chat turns to Graphiti in real-time.
5. Assistant queries Graphiti (hybrid search + temporal + paths) to answer.

## 4. Functional Requirements
### 4.1 Ingestion (per source)
**Gmail**
- Use `users.history.list` as the primary delta feed.
- On expired/invalid history IDs, fallback to `newer_than:<N>d` search then reseed history.
- Create one full episode per message (headers plus optional snippet/body).
- Keys: `message_id` (primary), `thread_id` (optional), version = `internalDate` (ms) or `historyId`.

**Google Drive**
- Use `changes.list` with stored `pageToken`.
- On any file change, fetch full doc text (Google Docs API) or appropriate content extraction for other MIME types.
- Create a full episode representing the entire doc at that revision.
- Keys: `file_id`, version = `revisionId` (preferred) or `modifiedTime`.

**Google Calendar**
- Use `events.list` with `syncToken` (seed once).
- Create/replace event episodes; handle cancellations (`status=cancelled`).
- Keys: `event_id`; for recurring events also store `recurringEventId` and `originalStartTime`. Version = `updated`.

**Slack (Polling)**
- Token has read scopes (no app creation).
- Enumerate channels, DMs, and MPIMs the token can see to seed metadata caches.
- Execute the configured `search.messages` query with `sort=timestamp` and `oldest=<last_seen_ts>`.
- Fetch full message bodies when search snippets are truncated and enrich with cached user/channel metadata.
- Create one episode per user message (skip non-user subtypes by default).
- Keys: `channel_id` + `ts` (and `thread_ts` when present). Version = message `ts`.

**MCP / Cursor**
- For every turn, record `source="mcp"`, role `user|assistant|tool`, `thread_id`, and `message_id`.
- Store full user and assistant text; tool I/O summarized or truncated.
- Keys: `message_id` (episode id), version = wall-clock `valid_at`.

### 4.2 Versioning & Idempotency
- Compose deterministic episode key: `{source}:{native_id}:{version}`.
- Inserting a new version sets the previous version’s `invalid_at = new.valid_at` (strict temporal lineage).
- Deletions/cancellations write a lightweight tombstone episode and close the prior version.

### 4.3 Queries (required behaviors)
- **Hybrid Search:** lexical + embeddings; return ranked episodes with snippets and source links.
- **Temporal “as-of” Queries:** respond based on versions valid at a timestamp.
- **Relationship/Path:** shortest paths between concepts (e.g., “OMS exceptions” → “owner”).
- **Context assembly macro:** “Prep my 1:1 with <name>” gathers recent episodes across sources and outputs a condensed brief.

### 4.4 Resiliency
- Offline for days must resume cleanly.
- Gmail fallback window configurable (default 7–14 days).
- Drive/Calendar tokens robust; on token expiry, perform full resync (seed new tokens).
- Slack resumes via timestamp checkpoints for the configured search query and cached metadata.

## 5. Non-Functional Requirements
- **Privacy:** all data local; tokens stored locally; nothing uploaded.
- **Reliability:** no data loss across reboots/offline windows up to 14 days.
- **Performance:**
  - Initial backfill ≤ 15 minutes for last 14 days (typical volume).
  - Poll loop ≤ 30 seconds to process hourly deltas.
  - Resource usage: low CPU at idle; memory < 2 GB for poller + DB under typical load.
- **Observability:** local logs; lightweight metrics (counts, last sync times).

## 6. Data Model
### 6.1 Episode (canonical)
```json
{
  "group_id": "mike_assistant",
  "source": "gmail" | "gdrive" | "calendar" | "slack" | "mcp",
  "valid_at": "ISO-8601",
  "invalid_at?": "ISO-8601",
  "text?": "string",
  "json?": {},
  "metadata": {}
}
```
- `text` captures emails, docs, Slack messages, and chat turns.
- `json` captures structured payloads such as calendar events.
- `metadata` stores native IDs, URLs/permalinks, and auxiliary fields (participants, owners, thread IDs, etc.).

### 6.2 Source-Specific Metadata (minimum)
- **Gmail:** `message_id`, `thread_id`, `from`, `to`, `message_id_hdr?`
- **Drive:** `file_id`, `name`, `url`, `revisionId?`, `owners[]`
- **Calendar:** `event_id`, `recurringEventId?`, `attendees[]`, `location?`
- **Slack:** `channel`, `ts`, `thread_ts?`, `user`, `permalink?`
- **MCP:** `thread_id`, `message_id`, `role`, `tool_name?`

## 7. State & Config
### 7.1 Local State (defaults)
- Path: `~/.graphiti_sync/`
- `tokens.json` (Google OAuth tokens)
- `state.json` (checkpoints):
  - `gmail.last_history_id`, `gmail.fallback_after_days`
  - `drive.page_token`
  - `calendar.sync_tokens.{calendarId}`
  - `slack.channels.{channel_id}.last_seen_ts`
  - `slack.threads.{channel_id}.{thread_ts}.last_seen_ts`

### 7.2 Config (env / file)
- Graph DB: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASS`
- Group: `GROUP_ID` (default `mike_assistant`)
- Polling cadence: `POLL_GMAIL_DRIVE_CAL=3600s`, `POLL_SLACK_ACTIVE=30s`, `POLL_SLACK_IDLE=3600s`
- Gmail fallback window: `GMAIL_FALLBACK_DAYS=7`
- Search configuration: `SLACK_SEARCH_QUERY`; optional `DRIVE_MIME_ALLOWLIST`

## 8. Security & Compliance
- Least-privilege scopes: Gmail/Drive/Calendar read-only; Slack read scopes only.
- Secret storage: file permissions set to user-only; optional future macOS Keychain integration.
- Redaction hook: configurable regex removal for secrets/keys before ingestion.
- Local-only processing (Phase 1). No telemetry.

## 9. Error Handling & Recovery
- **Google 410 Gone (Calendar syncToken):** full resync, reseed token.
- **Gmail invalid startHistoryId:** fallback time-window query, reseed latest historyId.
- **Slack rate limits:** exponential backoff; persist cursors; resume next tick.
- **Doc fetch failures:** retry with backoff; skip large binaries (ingest metadata only).

## 10. Acceptance Criteria
- **AC-1:** After first run, last 14 days of Gmail/Drive/Calendar and current Slack channels are ingested (episodes exist, counts logged).
- **AC-2:** Power off laptop for 48h → on next run, no gaps (Drive/Calendar via tokens; Gmail via fallback; Slack via timestamps).
- **AC-3:** Query “Prep my 1:1 with Jenna” returns a list containing: latest meeting event, recent related emails, recent Slack mentions, and recent Drive doc versions.
- **AC-4:** Updating a Google Doc produces a new episode and prior version shows `invalid_at`.
- **AC-5:** Deleting a calendar event or cancelling meeting produces a tombstone episode.
- **AC-6:** MCP conversation turns appear as episodes within seconds; searchable.

## 11. Milestones & Deliverables
- **M0: Core Graph & Poller**
  - Neo4j/Graphiti running locally.
  - Pollers for Gmail/Drive/Calendar with state and fallback logic.
  - CLI `sync:once` and `sync:status`.
- **M1: Slack Poller**
  - Channel/thread checkpoints; DMs/MPIMs included (if scopes allow).
  - Configurable search query with cached channel/user metadata.
- **M2: MCP/Cursor**
  - Turn logging (episodes for user/assistant/tool).
  - Retrieval tools (search/as-of/paths) callable from Cursor.
- **M3: Quality & Ops**
  - Redaction, summarization for large payloads.
  - `/health` endpoint (local) + textual dashboard.
  - Backup/restore of `~/.graphiti_sync/`.

## 12. Risks & Mitigations
- **Gmail history expiry:** fallback time window + reseed.
- **Slack token scope limits:** start with available scopes, rely on search query filtering; DM if blocked.
- **Large Docs:** size cap + optional summarization; still version with metadata.
- **Token revocation:** prompt re-auth on failure; keep clear errors.

## 13. Example Workflows (Informative)
- **Doc Edit:** Drive change → fetch full text → new episode (`valid_at=modifiedTime`) → old episode invalidated → as-of queries return correct snapshot.
- **Meeting Prep:** Upcoming 1:1 detected → pull last 14 days of episodes where Jenna mentioned → output bullet brief.
- **Decision Recall:** Search MCP + Gmail + Slack episodes for “OMS exceptions decision” → surface final assistant reply + linked email + doc version.

## 14. Definition of Done
- All acceptance criteria pass.
- End-to-end run on a stock macOS with OAuth login, zero manual DB steps.
- Pollers survive sleep/offline/reboots; status command shows healthy cursors.
- Query demos (CLI) produce expected outputs with timestamps and links.

## 15. Appendix A — Episode Payload Examples
### Gmail (message version)
```json
{
  "group_id": "mike_assistant",
  "source": "gmail",
  "valid_at": "2025-09-29T14:02:41Z",
  "text": "Email: Q3 inventory review – OMS exception rates by vendor...",
  "metadata": {
    "message_id": "18c4a7b3f...",
    "thread_id": "18c4a7b3e...",
    "from": "jenna@example.com",
    "to": "mike@example.com"
  }
}
```

### Drive (full replacement on change)
```json
{
  "group_id": "mike_assistant",
  "source": "gdrive",
  "valid_at": "2025-09-28T11:09:00Z",
  "text": "Supplier Scorecard Policy v2.3 ... (full doc text)",
  "metadata": {
    "file_id": "1AbCDeFg...",
    "name": "Supplier Scorecard Policy",
    "url": "https://docs.google.com/document/d/1AbCDeFg...",
    "revisionId": "0123456789"
  }
}
```

### Calendar (structured)
```json
{
  "group_id": "mike_assistant",
  "source": "calendar",
  "valid_at": "2025-09-30T12:02:00Z",
  "json": {
    "title": "1:1 Mike <> Jenna",
    "start": {"dateTime": "2025-09-30T13:00:00-04:00"},
    "end": {"dateTime": "2025-09-30T13:30:00-04:00"},
    "location": "Zoom",
    "attendees": ["mike@example.com", "jenna@example.com"]
  },
  "metadata": {
    "event_id": "dkj3lkj23",
    "recurringEventId": null
  }
}
```

### Slack (message)
```json
{
  "group_id": "mike_assistant",
  "source": "slack",
  "valid_at": "2025-09-27T15:31:45Z",
  "text": "Carrier X late again to FC-Brooklyn; escalate by Friday.",
  "metadata": {
    "channel": "C12345678",
    "ts": "1727457105.123456",
    "user": "U1234",
    "thread_ts": null,
    "permalink": "https://slack.com/archives/C12345678/p1727457105123456"
  }
}
```

### MCP (assistant turn)
```json
{
  "group_id": "mike_assistant",
  "source": "mcp",
  "valid_at": "2025-09-30T10:15:00Z",
  "text": "[assistant] Here’s the agenda for your 1:1 with Jenna...",
  "metadata": {
    "thread_id": "cursor-run-abc",
    "message_id": "ulid_01HXYZ...",
    "role": "assistant"
  }
}
```

## 16. Appendix B — Deterministic Keys & Versioning Rules
- Episode ID format: `{source}:{native_id}:{version}`.
- **Gmail:** `gmail:{message_id}:{internalDate}`.
- **Drive:** `gdrive:{file_id}:{revisionId|modifiedTime}`.
- **Calendar:** `calendar:{event_id}:{updated}`.
- **Slack:** `slack:{channel}:{ts}`.
- **MCP:** `mcp:{message_id}:{valid_at}`.
- On insert: find prior version by native ID and set `invalid_at = new.valid_at`.
- Tombstones: `{deleted:true}` payload with `valid_at=deletionTs`.

## 17. Appendix C — Observability & Ops
- Logs: per-source sync summary (items processed, new versions, errors).
- Status CLI: prints last run time, current tokens/cursors, and next scheduled run.
- Health: optional local HTTP `/health` returns JSON with last sync timestamps per source.
- Backups: tar `~/.graphiti_sync/` nightly; DB volume snapshot weekly.

## 18. Future (Post-PRD)
- GCP push mode (Pub/Sub) as an alternative deployment.
- Doc diff episodes for granular change tracking.
- Keychain storage for tokens.
- Minimal web UI for search/timelines.
