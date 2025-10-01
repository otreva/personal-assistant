# Personal Assistant (Graphiti-powered)

Personal Assistant is a Graphiti-powered, local-first knowledge graph that continuously ingests Gmail, Google Drive, Google Calendar, Slack, and MCP/Cursor activity so your assistant can answer time-aware questions. This repository contains the product requirements, task plan, and a full Python reference implementation with pollers, persistence helpers, health checks, and an acceptance harness.

The guide below walks a new operator through the entire setup — from installing prerequisites and creating API credentials to running the pollers, verifying health, and backing up state.

## Quick Start Overview

1. Install Docker Desktop or another Docker runtime.
2. Clone the repository and start the bundled admin UI via Docker (no local Python needed).
3. Configure Personal Assistant through the web admin at <http://localhost:8128>.
4. Create OAuth credentials for Google Workspace APIs and generate a user token for Slack.
5. Store the tokens under `~/.graphiti_sync/` and confirm Personal Assistant can read them.
6. Use the admin UI's manual loaders to backfill ~365 days of history for Gmail, Drive, Calendar, and Slack.
7. Run the Gmail, Drive, Calendar, and Slack pollers once to confirm incremental sync.
8. Let the built-in 02:00 EST backup job archive state (or trigger a manual backup from the UI) and monitor logs directly in the browser.

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

### 2. Launch the Dockerised admin UI

Personal Assistant ships with a `docker-compose.yml` file that now boots both the admin UI and a
Neo4j 5 instance. The compose stack mounts your repository, caches Python dependencies,
and persists Neo4j data to `./neo4j/` so you can tear the stack down without losing the
database. No local Python environment is required.

- **Using Docker Compose** (recommended for day-to-day operation):

  ```bash
  docker compose up
  ```

  This command starts the admin UI on <http://localhost:8128> and exposes Neo4j on
  <bolt://localhost:7687>. Press `Ctrl+C` to stop the stack and rerun the command to
  resume from the persisted volumes.

- **Using the helper script** (runs a one-off container for the admin UI only):

  ```bash
  ./scripts/docker-run.sh
  ```

  The script is handy for quick checks but does not manage Neo4j or reuse cached
  dependencies between invocations.

Both approaches expose the admin UI on <http://localhost:8128>. The first compose run
builds the Python environment; subsequent runs reuse the cached volume for faster starts.

### 3. Configure Personal Assistant from the web admin

Visit <http://localhost:8128> after starting the container. The admin UI detects existing
settings from `~/.graphiti_sync/config.json` (created on first launch) and provides a dark
mode/light mode aware form for editing them. Populate the Neo4j connection, polling
intervals, historical backfill defaults (365 days by default for each service), and
summarisation settings. Configure the backup directory, retention window, and optional
custom logs directory in the **Backups & Logging** section, then click **Save
configuration**. The values are persisted back to `~/.graphiti_sync/config.json` with
secure permissions.

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

### 10. (Optional) Log MCP / Cursor turns

Integrate your MCP or Cursor workflow by creating `McpTurn` objects and logging them through `graphiti.mcp.logger.McpEpisodeLogger`. The logger batches turns and writes them via the same episode pipeline used by the pollers.

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

## Additional Documentation

- [Product Requirements](docs/graphiti_prd.md)
- [Implementation Task Plan](docs/task_plan.md)
- [Operational Playbook](docs/ops.md)
- [Deployment Notes](docs/deployment.md)

These documents expand on the architecture, API expectations, and operational procedures described above.
