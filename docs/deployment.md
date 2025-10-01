# Personal Assistant Deployment Guide (macOS)

This guide walks through deploying Personal Assistant (powered by Graphiti) on a fresh macOS workstation, from prerequisites to
launchd scheduling and day-two operations.

## 1. Prerequisites

- macOS 13 or later with Homebrew installed.
- Python 3.12 (via `brew install python@3.12`).
- Docker Desktop (for Neo4j) or an existing Neo4j instance reachable over `bolt://`.
- Google Workspace and Slack tokens with read-only scopes as outlined in the PRD.
- Optional: `uvicorn` for exposing the health endpoint outside the admin UI.

## 2. Initial Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/otreva/personal-assistant.git
   cd personal-assistant
   ```

2. **Start the admin UI and Neo4j via Docker Compose**
   ```bash
   docker compose up
   ```

   The Compose stack launches the admin UI container (`personal-assistant`) alongside Neo4j. It mounts the repository, reuses a
   dedicated Python package cache, and persists Neo4j data to `./neo4j/`.

3. **(Optional) One-off admin UI container**
   ```bash
   ./scripts/docker-run.sh
   ```

   This helper script starts only the admin UI for quick configuration checks. It reuses persistent Docker volumes named
   `personal_assistant_state` and `personal_assistant_pip_cache` so state and cached dependencies survive across runs.

## 3. Configure Personal Assistant

1. Visit <http://localhost:8128> after the container starts. The UI loads existing settings from `~/.graphiti_sync/config.json`
   and displays them in grouped sections.
2. Populate Neo4j credentials, polling cadences, backfill defaults, and summarisation options.
3. Use the **Backups & Logging** controls to select paths via the OS-native directory picker. Both the backup directory and the
   optional logs directory inputs accept spaces and update `~/.graphiti_sync/config.json` automatically when saved.
4. Provide Google Workspace OAuth client credentials and authorise the Gmail, Drive, and Calendar scopes. Tokens are stored under
   `~/.graphiti_sync/tokens.json`.
5. Add your Slack user token, inventory workspace channels, and define the `slack_search_query` (for example `in:general has:link`).
   The Slack poller uses the Search API, automatically resolves truncated messages, and caches user/channel metadata locally to
   minimise API calls.
6. Save the configuration. Future launches read the stored config and only require edits when credentials change.

## 4. Launchd Scheduling

Create two launchd property lists in `~/Library/LaunchAgents/` once configuration is complete.

### 4.1 Gmail/Drive/Calendar Poller (Hourly)

`com.personal-assistant.poller.hourly.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.personal-assistant.poller.hourly</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd /path/to/personal-assistant && source .venv/bin/activate && python -m graphiti.cli sync scheduler --once</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>/tmp/personal-assistant-hourly.log</string>
  <key>StandardErrorPath</key><string>/tmp/personal-assistant-hourly.err</string>
</dict>
</plist>
```

### 4.2 Slack Active Poller (Every 30 Seconds)

`com.personal-assistant.poller.slack.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.personal-assistant.poller.slack</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd /path/to/personal-assistant && source .venv/bin/activate && python -m graphiti.cli sync slack --once</string>
  </array>
  <key>StartInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/tmp/personal-assistant-slack.log</string>
  <key>StandardErrorPath</key><string>/tmp/personal-assistant-slack.err</string>
</dict>
</plist>
```

Load the jobs with `launchctl load ~/Library/LaunchAgents/com.personal-assistant.poller.hourly.plist` (and the Slack variant).
Verify they are active with `launchctl list | grep personal-assistant`.

## 5. Observability & Operations

- Run `python -m graphiti.cli sync status` to display the Personal Assistant sync dashboard. Append `--json` for automation.
- Start the lightweight health service via `uvicorn "graphiti.health:create_health_app" --factory` if you need a HTTP endpoint.
- Trigger backups with `python -m graphiti.cli backup state --output ~/Backups` and restore with
  `python -m graphiti.cli restore state <archive.tar.gz>`. Archives are timestamped and retain secure file permissions.
- The admin UI surfaces recent poll runs, backup results, log entries, and Slack inventory metadata inline so you can inspect
  health without leaving the browser.

## 6. Troubleshooting

- **Neo4j unavailable:** restart the container (`docker compose restart neo4j` if using Compose) and rerun
  `python -m graphiti.cli sync scheduler --once`.
- **OAuth token expired:** delete the provider entry in `~/.graphiti_sync/tokens.json` and rerun the relevant poller to trigger
  re-authentication.
- **Slack messages missing:** confirm the `slack_search_query` matches your desired scope (e.g. `from:@me`), re-run the Slack
  poller, and check `/tmp/personal-assistant-slack.err` for API errors.
- **Health status shows `stale`:** execute the affected poller manually and review `~/.graphiti_sync/state.json` for cursor issues.
  Restore from the latest backup if corruption is detected.

Following this checklist results in a reproducible, self-healing deployment aligned with the Personal Assistant PRD.
