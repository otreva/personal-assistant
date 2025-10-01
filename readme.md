# Graphiti Personal & Enterprise Assistant

Graphiti is a local-first knowledge graph that continuously ingests Gmail, Google Drive, Google Calendar, Slack, and MCP/Cursor activity so your assistant can answer time-aware questions. This repository contains the product requirements, task plan, and a full Python reference implementation with pollers, persistence helpers, health checks, and an acceptance harness.

The guide below walks a new operator through the entire setup — from installing prerequisites and creating API credentials to running the pollers, verifying health, and backing up state.

## Quick Start Overview

1. Install Python 3.11+, Docker, and the Neo4j database driver.
2. Launch a local Neo4j instance and configure Graphiti via a `.env` file.
3. Create OAuth credentials for Google Workspace APIs and generate a user token for Slack.
4. Store the tokens under `~/.graphiti_sync/` and confirm Graphiti can read them.
5. Run the Gmail, Drive, Calendar, and Slack pollers once to seed the graph.
6. Start the optional health endpoint or scheduler and monitor sync status.
7. Back up the state directory regularly to protect checkpoints and credentials.

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
git clone https://github.com/your-org/graphiti.git
cd graphiti
```

### 2. Create and activate a Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 3. Install Python dependencies

Install the runtime dependencies (Neo4j driver, FastAPI for health checks, and optional tooling):

```bash
pip install neo4j fastapi uvicorn pytest
```

### 4. Start Neo4j locally

Run Neo4j in Docker with an isolated password (change `localgraph` to your secret):

```bash
docker run -d \
  --name graphiti-neo4j \
  -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/localgraph \
  neo4j:5
```

Confirm the service is reachable:

```bash
cypher-shell -u neo4j -p localgraph "RETURN 1"
```

### 5. Configure Graphiti via `.env`

Copy the sample below into a `.env` file at the project root and adjust values to match your environment:

```bash
cat > .env <<'ENV'
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASS=localgraph
GROUP_ID=mike_assistant
POLL_GMAIL_DRIVE_CAL=3600
POLL_SLACK_ACTIVE=30
POLL_SLACK_IDLE=3600
GMAIL_FALLBACK_DAYS=7
CALENDAR_IDS=primary
ENV
```

### 6. Initialise the state directory

Graphiti stores OAuth tokens and poller checkpoints under `~/.graphiti_sync/`. Run the status command once to create the directory with the correct permissions:

```bash
python -m graphiti.cli status
```

The command prints the resolved configuration and confirms paths to `tokens.json` and `state.json`.

### 7. Create Google API credentials

1. In the Google Cloud Console, create an OAuth client for Desktop applications.
2. Download the client secrets JSON and use Google’s OAuth Playground or [`gcloud auth application-default print-access-token`](https://cloud.google.com/sdk/gcloud/reference/auth/application-default/print-access-token) to perform the OAuth consent flow for the scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/calendar.readonly`
3. Copy the resulting refresh token and client details into `~/.graphiti_sync/tokens.json` using the structure below:

```json
{
  "google": {
    "client_id": "YOUR_CLIENT_ID",
    "client_secret": "YOUR_CLIENT_SECRET",
    "refresh_token": "YOUR_REFRESH_TOKEN",
    "scopes": [
      "https://www.googleapis.com/auth/gmail.readonly",
      "https://www.googleapis.com/auth/drive.readonly",
      "https://www.googleapis.com/auth/calendar.readonly"
    ]
  }
}
```

4. Restrict file permissions to the current user (`chmod 600 ~/.graphiti_sync/tokens.json`).

### 8. Generate a Slack user token

1. Visit <https://api.slack.com/apps>, create a new app, and enable the following user token scopes:
   - `channels:history`, `channels:read`
   - `groups:history`, `groups:read`
   - `im:history`, `im:read`
   - `mpim:history`, `mpim:read`
2. Install the app to your workspace and copy the generated `xoxp-` token.
3. Extend `~/.graphiti_sync/tokens.json` with the Slack credentials:

```json
{
  "google": { ... },
  "slack": {
    "user_token": "xoxp-your-token",
    "workspace": "your-workspace"
  }
}
```

4. Limit file permissions again (`chmod 600 ~/.graphiti_sync/tokens.json`).

### 9. Verify configuration and inventory Slack channels

List available Slack channels (and persist their metadata/IDs to state):

```bash
python -m graphiti.cli sync slack --list-channels
```

The output is a JSON array of channels that Graphiti will poll. You can further restrict ingestion by setting `SLACK_CHANNEL_ALLOWLIST` in `.env` before running this command.

### 10. Run the pollers to seed Graphiti

Execute each poller once to ingest the latest activity:

```bash
python -m graphiti.cli sync gmail --once
python -m graphiti.cli sync drive --once
python -m graphiti.cli sync calendar --once
python -m graphiti.cli sync slack --once
```

Each command prints a JSON summary including how many episodes were written and the execution timestamp. Rerun the commands whenever you need a manual refresh.

### 11. (Optional) Log MCP / Cursor turns

Integrate your MCP or Cursor workflow by creating `McpTurn` objects and logging them through `graphiti.mcp.logger.McpEpisodeLogger`. The logger batches turns and writes them via the same episode pipeline used by the pollers.

### 12. Monitor health and scheduling

- **Status dashboard:**
  ```bash
  python -m graphiti.cli sync status
  ```
  This prints a textual dashboard with the last run time, next due interval, and error counts per source.

- **JSON status:**
  ```bash
  python -m graphiti.cli sync status --json
  ```

- **Health endpoint:**
  ```bash
  uvicorn "graphiti.health:create_health_app" --factory --reload
  ```
  Visit `http://localhost:8000/health` for a machine-readable summary.

- **Scheduler stub:**
  ```bash
  python -m graphiti.cli sync scheduler --once
  ```
  This sequentially runs all pollers and prints aggregate metrics. Use the output to wire Graphiti into a `launchd` or cron job.

### 13. Back up and restore state

Create a timestamped archive of `~/.graphiti_sync/`:

```bash
python -m graphiti.cli backup state --output ~/Backups
```

Restore from an archive and reapply file permissions automatically:

```bash
python -m graphiti.cli restore state ~/Backups/graphiti-state-YYYYMMDDHHMMSS.tar.gz
```

Run the relevant `sync ... --once` commands afterward to resume polling from the restored checkpoints.

### 14. Validate with the acceptance harness (optional)

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
