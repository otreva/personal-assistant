# Graphiti Deployment Guide (macOS)

This guide walks through deploying Graphiti on a fresh macOS workstation, from prerequisites to launchd scheduling.

## 1. Prerequisites

- macOS 13 or later with Homebrew installed.
- Python 3.12 (via `brew install python@3.12`).
- Docker Desktop (for Neo4j) or an existing Neo4j instance reachable over `bolt://`.
- Google Workspace and Slack tokens with read-only scopes as outlined in the PRD.

## 2. Initial Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/graphiti.git
   cd graphiti
   ```

2. **Create a virtual environment**
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Start Neo4j locally**
   ```bash
   docker run \
     --name neo4j-graphiti \
     -p 7474:7474 -p 7687:7687 \
     -e NEO4J_AUTH=neo4j/password \
     -d neo4j:5
   ```

4. **Configure environment variables**
   - Copy `.env.example` (if provided) to `.env` and populate:
     - `NEO4J_URI=bolt://localhost:7687`
     - `NEO4J_USER=neo4j`
     - `NEO4J_PASS=password`
     - `GROUP_ID=<your_group>`
     - Optional: `SLACK_CHANNEL_ALLOWLIST`, `CALENDAR_IDS`, `REDACTION_RULES_PATH`, summarisation settings.

5. **Authenticate providers**
   - Run `graphiti sync gmail --once`, follow OAuth prompts, and verify tokens stored under `~/.graphiti_sync/tokens.json`.
   - Repeat for `drive`, `calendar`, and `slack`.

## 3. Launchd Scheduling

Create two launchd property lists in `~/Library/LaunchAgents/`:

### 3.1 Gmail/Drive/Calendar Poller (Hourly)

`com.graphiti.poller.hourly.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.graphiti.poller.hourly</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd /path/to/graphiti && source .venv/bin/activate && graphiti sync scheduler --once</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>/tmp/graphiti-hourly.log</string>
  <key>StandardErrorPath</key><string>/tmp/graphiti-hourly.err</string>
</dict>
</plist>
```

### 3.2 Slack Active Poller (Every 30 seconds)

`com.graphiti.poller.slack.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.graphiti.poller.slack</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd /path/to/graphiti && source .venv/bin/activate && graphiti sync slack --once</string>
  </array>
  <key>StartInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/tmp/graphiti-slack.log</string>
  <key>StandardErrorPath</key><string>/tmp/graphiti-slack.err</string>
</dict>
</plist>
```

Load the jobs with `launchctl load ~/Library/LaunchAgents/com.graphiti.poller.hourly.plist` (repeat for Slack). Use `launchctl list | grep graphiti` to verify they are active.

## 4. Observability & Operations

- The `/health` endpoint (see `graphiti.health`) can be served via `uvicorn` for local dashboards.
- Use `graphiti backup state --output <dir>` nightly; restore with `graphiti restore state <archive>`.
- Monitor `/tmp/graphiti-*.log` for poll results and errors; rotate logs with `newsyslog` if required.

## 5. Troubleshooting

- **Neo4j unavailable:** restart the container (`docker restart neo4j-graphiti`) and rerun `graphiti sync scheduler --once`.
- **OAuth token expired:** delete the provider entry in `~/.graphiti_sync/tokens.json` and rerun the relevant poller to re-authenticate.
- **Health status shows `stale`:** run the affected poller manually and inspect `/tmp/graphiti-*.err` for stack traces.

Following this checklist results in a reproducible, self-healing deployment aligned with the PRD's Definition of Done.

