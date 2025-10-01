# Personal Assistant (Graphiti-powered)

Personal Assistant is a Graphiti-powered, local-first knowledge graph that continuously ingests Gmail, Google Drive, Google Calendar, Slack, and MCP/Cursor activity so your assistant can answer time-aware questions. This repository contains the product requirements, task plan, and a full Python reference implementation with pollers, persistence helpers, health checks, and an acceptance harness.

The guide below walks a new operator through the entire setup — from installing prerequisites and creating API credentials to running the pollers, verifying health, and backing up state.

## Quick Start Overview

1. Install Docker Desktop or another Docker runtime.
2. Clone the repository and start all services via Docker Compose (no local Python needed).
3. Configure Personal Assistant through the web admin at <http://localhost:8128>.
4. Create OAuth credentials for Google Workspace APIs and generate a user token for Slack.
5. Store the tokens under `~/.graphiti_sync/` and confirm Personal Assistant can read them.
6. The poller daemon runs continuously in the background on configurable intervals.
7. Use the admin UI's manual loaders to backfill ~365 days of history for Gmail, Drive, Calendar, and Slack.
8. Monitor poller logs with `docker compose logs -f poller` and let the built-in 02:00 EST backup job archive state.

Each step is detailed below.

## Prerequisites

- **Operating System:** macOS (primary target) or Linux with Docker installed.
- **Python:** 3.11 or newer (3.12 tested).
- **Neo4j:** Local instance reachable at `bolt://localhost:7687` (Docker recipe provided).
- **Google APIs:** Workspace account with Gmail, Drive, and Calendar enabled plus access to the Google Cloud Console.
- **Slack:** Workspace admin rights to generate a user OAuth token with read scopes.
- **Optional:** `uvicorn` (for FastAPI health endpoint) and `pytest` (for running the acceptance harness).

## Step-by-Step Setup

### 1. Clone the repository

```bash
git clone https://github.com/otreva/personal-assistant.git
cd personal-assistant
```

### 2. Launch all services with Docker Compose

Personal Assistant ships with a `docker-compose.yml` file that boots three services:

1. **Neo4j** - Graph database on ports 7687 (Bolt) and 7474 (HTTP)
2. **Poller Daemon** - Background service that continuously polls Gmail, Drive, Calendar, and Slack
3. **Web Admin** - Admin UI on <http://localhost:8128>

Start all services:

```bash
docker compose up -d
```

This runs everything in the background. The compose stack persists data to volumes so you can tear the stack down without losing the database or configuration.

**View logs:**
```bash
docker compose logs -f poller   # Watch poller daemon logs
docker compose logs -f web      # Watch web admin logs
docker compose logs -f neo4j    # Watch Neo4j logs
```

**Stop services:**
```bash
docker compose down
```

The first compose run builds the Docker images; subsequent runs start instantly.

### 3. Configure Personal Assistant from the web admin

Visit <http://localhost:8128> after starting the services. The admin UI detects existing
settings from `~/.graphiti_sync/config.json` (created on first launch) and provides a dark
mode/light mode aware form for editing them. 

**Key configuration settings:**
- **Neo4j connection** - Pre-configured for the Docker network
- **Polling intervals** - How often pollers run (configurable per service):
  - Gmail/Drive/Calendar: Default 3600 seconds (1 hour)
  - Slack Active: Default 30 seconds
  - Slack Idle: Default 3600 seconds (1 hour)
- **Backfill defaults** - How many days of history to load (365 days by default)
- **Slack search queries** - Comma-separated queries (e.g., `in:general, from:@me, has:link`)
- **Summarisation settings** - Text processing behavior
- **Backup directory and retention** - Automated backup configuration

Click **Save configuration** to persist changes. The poller daemon automatically reloads the configuration on its next polling cycle.

> **Note:** Environment variables and `.env` files are no longer required. The admin UI
> persists configuration to `~/.graphiti_sync/config.json` and the compose stack creates
> the state directory with secure permissions on first launch.

### 4. Review the state directory

Personal Assistant stores OAuth tokens, poller checkpoints, and configuration under
`~/.graphiti_sync/`. The admin UI surfaces the active paths in the **Backups & Logging**
tab and automatically manages file permissions—no manual commands required.

### 5. Create Google API credentials

1. In the Google Cloud Console, create an OAuth client (Desktop or Web application) for the
   Gmail, Drive, and Calendar APIs.
2. Copy the generated client ID and client secret.
3. In the admin UI open **Connections → Google Workspace OAuth**, paste the client ID and
   secret, click **Save configuration**, and then click **Sign in with Google**. Complete the
   consent flow to grant the requested read-only scopes. The admin UI stores the refresh token
   and keeps the secret masked while it lives on disk.

### 6. Generate a Slack user token

1. Visit <https://api.slack.com/apps>, create a new app, and enable the following user token
   scopes:
   - `channels:history`, `channels:read`
   - `groups:history`, `groups:read`
   - `im:history`, `im:read`
   - `mpim:history`, `mpim:read`
2. Install the app to your workspace and copy the generated `xoxp-` token.
3. In the admin UI open **Connections → Slack Workspace**, provide an optional workspace
   label, paste the user token, and click **Save Slack Credentials**. The token is stored in
   `~/.graphiti_sync/tokens.json` with user-only permissions.

### 7. Verify configuration and inventory Slack channels

Use the **Inventory Slack Channels** button in the Slack Workspace card to refresh the cached
channel metadata. The results appear inline and complement the Slack search query configured
in the **Polling Behaviour** section, which determines which messages Personal Assistant
ingests.

### 8. Backfill the last year of history

From the admin UI scroll to **Manual Historical Load** and run the Gmail, Drive,
Calendar, and Slack backfills. The defaults load 365 days of activity and include
rate-limit friendly pauses with jitter. You can rerun the loader at any time to fetch
additional history without affecting incremental checkpoints.

### 9. Run the pollers to seed Personal Assistant

Use the **Run Pollers Once** section in the admin UI to trigger Gmail, Drive, Calendar,
and Slack pollers on demand. The UI displays the number of episodes processed and records
structured log entries for each run so you can confirm incremental sync without touching
the terminal.

### 10. (Optional) Add MCP server to Cursor

Personal Assistant includes an MCP server that lets you query your knowledge graph directly from Cursor.

To enable it, add the contents of `mcp-config.json` to your Cursor MCP configuration file (typically `~/.cursor/mcp.json` or accessible via Cursor Settings → MCP):

```bash
cat mcp-config.json
```

Copy the `"personal-assistant"` entry into your `mcpServers` section. The MCP server provides tools to search and query your ingested episodes across all sources.

You can also log MCP/Cursor conversation turns by creating `McpTurn` objects through `graphiti.mcp.logger.McpEpisodeLogger`. The logger batches turns and writes them via the same episode pipeline used by the pollers.

### 11. Monitor health and scheduling

- The **Logs** section of the admin UI lets you filter categories, change retention windows,
  and inspect structured entries without leaving the browser.
- The **Backups** card shows on-demand backup results alongside the automated overnight job.
- Status messages above each manual action (backfills, pollers, Slack inventory) record the
  last run so you can confirm ingestion health at a glance.
- Optional automation: if you need a machine-readable endpoint for external monitors, you
  can still launch `uvicorn "graphiti.health:create_health_app" --factory --reload` in a
  separate process to expose `/health`.

### 12. Back up and restore state

Personal Assistant automatically creates a timestamped `.tar.gz` backup of `~/.graphiti_sync/`
every day at **02:00 EST**. Archives are written to the directory configured in the
admin UI (default `~/.graphiti_sync/backups`) and older files are pruned according to the
retention window. Use the **Run Backup** button in the Backups card to trigger an ad-hoc
archive directly from the browser.

If you need to script restores, the existing CLI helpers remain available:

```bash
python -m graphiti.cli restore state ~/Backups/graphiti-state-YYYYMMDDHHMMSS.tar.gz
```

After restoring, rerun the pollers from the admin UI to resume incremental sync from the
recovered checkpoints.

### 13. Validate with the acceptance harness (optional)

To execute the tests inside Docker without installing Python locally, run:

```bash
docker run --rm -it \
  -v "${PWD}:/app" \
  -w /app \
  python:3.12-slim \
  bash -lc "python -m pip install --upgrade pip && pip install -r requirements.txt && pytest"
```

Developers can run the synthetic end-to-end test harness to confirm the ingestion pipeline:

```bash
pytest tests/test_acceptance_harness.py
```

## Troubleshooting

### Neo4j Authentication Errors

If you encounter `Neo.ClientError.Security.Unauthorized` authentication errors, the Neo4j database may have been initialized with a different password than the one configured in Personal Assistant (default: `localgraph`).

**To reset Neo4j authentication:**

1. Stop the Neo4j container:
   ```bash
   docker compose stop neo4j
   ```

2. Delete the Neo4j auth file:
   ```bash
   rm neo4j/data/dbms/auth.ini
   ```

3. Restart the containers:
   ```bash
   docker compose up -d
   ```

Neo4j will recreate the auth file with the password from `docker-compose.yml` (`neo4j/localgraph`), which matches the default configuration in Personal Assistant.

## Additional Documentation

- [Product Requirements](docs/graphiti_prd.md)
- [Implementation Task Plan](docs/task_plan.md)
- [Operational Playbook](docs/ops.md)
- [Deployment Notes](docs/deployment.md)

These documents expand on the architecture, API expectations, and operational procedures described above.
