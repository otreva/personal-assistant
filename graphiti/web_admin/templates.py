"""HTML templates for the web admin UI."""
from __future__ import annotations

from html import escape
from string import Template

from .models import ConfigPayload


def oauth_result_page(success: bool, message: str) -> str:
    """Generate an OAuth result page that posts a message to the opener window."""
    status = "success" if success else "error"
    safe_message = escape(message)
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Google OAuth</title>
    <style>
      body {{{{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        margin: 0;
        padding: 2rem;
        background: #0b0c10;
        color: #f5f5f5;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
      }}}}
      .panel {{{{
        max-width: 420px;
        background: rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 2rem;
        box-shadow: 0 18px 45px rgba(0, 0, 0, 0.45);
        text-align: center;
      }}}}
      .panel.success {{{{
        border: 1px solid rgba(74, 222, 128, 0.6);
      }}}}
      .panel.error {{{{
        border: 1px solid rgba(248, 113, 113, 0.6);
      }}}}
      h1 {{{{
        font-size: 1.5rem;
        margin-bottom: 1rem;
      }}}}
      p {{{{
        margin: 0;
        line-height: 1.6;
      }}}}
    </style>
    <script>
      window.addEventListener('load', () => {{{{
        try {{{{
          if (window.opener && typeof window.opener.postMessage === 'function') {{{{
            window.opener.postMessage({{{{
              type: 'google-oauth',
              status: '{status}',
              message: '{safe_message}',
            }}}}, '*');
          }}}}
        }}}} catch (error) {{{{
          console.warn('Unable to notify parent window', error);
        }}}}
        setTimeout(() => window.close(), 500);
      }}}});
    </script>
  </head>
  <body>
    <div class="panel {status}">
      <h1>Google OAuth</h1>
      <p>{safe_message}</p>
    </div>
  </body>
</html>"""


def render_index_page(payload: ConfigPayload) -> str:
    """Render the main admin UI page."""
    search_queries = ", ".join(payload.slack_search_queries)
    calendars = ", ".join(payload.calendar_ids)
    redaction_lines = "\n".join(
        f"{rule.pattern} => {rule.replacement}" for rule in payload.redaction_rules
    )
    
    # Read the template from a separate file
    template = Template(_INDEX_TEMPLATE)
    
    return template.safe_substitute(
        neo4j_uri=escape(payload.neo4j_uri),
        neo4j_user=escape(payload.neo4j_user),
        neo4j_password=escape(payload.neo4j_password),
        google_client_id=escape(payload.google_client_id),
        google_client_secret=escape(payload.google_client_secret),
        group_id=escape(payload.group_id),
        poll_gmail_drive_calendar_seconds=payload.poll_gmail_drive_calendar_seconds,
        poll_slack_active_seconds=payload.poll_slack_active_seconds,
        poll_slack_idle_seconds=payload.poll_slack_idle_seconds,
        gmail_fallback_days=payload.gmail_fallback_days,
        gmail_backfill_days=payload.gmail_backfill_days,
        drive_backfill_days=payload.drive_backfill_days,
        calendar_backfill_days=payload.calendar_backfill_days,
        slack_backfill_days=payload.slack_backfill_days,
        slack_search_queries=escape(search_queries),
        calendars=escape(calendars),
        summarization_strategy=escape(payload.summarization_strategy),
        summarization_threshold=payload.summarization_threshold,
        summarization_max_chars=payload.summarization_max_chars,
        summarization_sentence_count=payload.summarization_sentence_count,
        redaction_rules_path=escape(payload.redaction_rules_path or ""),
        redaction_lines=escape(redaction_lines),
        backup_directory=escape(payload.backup_directory),
        backup_retention_days=payload.backup_retention_days,
        log_retention_days=payload.log_retention_days,
        logs_directory=escape(payload.logs_directory or ""),
    )


# The large HTML template string - keeping it at the bottom for readability
_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Personal Assistant Admin</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--fg);
    }
    .layout {
      display: flex;
      min-height: 100vh;
      background: var(--bg);
    }
    .sidebar {
      width: 240px;
      padding: 2rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      background: var(--sidebar-bg);
      border-right: 1px solid var(--border);
    }
    .tab-button {
      padding: 0.85rem 1.2rem;
      border-radius: 12px;
      border: 1px solid transparent;
      background: transparent;
      color: inherit;
      font-weight: 600;
      text-align: left;
      cursor: pointer;
      transition: background 0.2s ease, color 0.2s ease;
    }
    .tab-button:hover {
      background: var(--hover-bg);
    }
    .tab-button.active {
      background: var(--accent);
      color: white;
      box-shadow: 0 12px 25px var(--shadow);
    }
    .content {
      flex: 1;
      max-width: 1040px;
      margin: 0 auto;
      padding: 2.5rem 1.75rem 4rem;
    }
    header {
      margin-bottom: 2rem;
    }
    h1 {
      font-size: 2.1rem;
      margin: 0 0 0.5rem 0;
    }
    h2 {
      font-size: 1.35rem;
      margin: 0 0 0.85rem 0;
    }
    p {
      margin-top: 0;
      line-height: 1.6;
    }
    .card {
      margin-bottom: 1.6rem;
      border: 1px solid var(--border);
      border-radius: 18px;
      background: var(--panel-bg);
      box-shadow: 0 18px 45px var(--shadow);
      padding: 1.8rem;
    }
    .card p.hint {
      color: var(--muted);
      margin-bottom: 1.2rem;
    }
    label {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      font-weight: 600;
    }
    input, textarea, select {
      padding: 0.65rem 0.85rem;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--input-bg);
      color: inherit;
      font-size: 1rem;
    }
    textarea {
      min-height: 120px;
      resize: vertical;
    }
    button {
      padding: 0.75rem 1.6rem;
      border-radius: 999px;
      border: none;
      background: var(--accent);
      color: white;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.15s ease, box-shadow 0.2s ease, background 0.2s ease;
    }
    button:hover {
      transform: translateY(-1px);
      box-shadow: 0 12px 24px rgba(37, 99, 235, 0.28);
    }
    button:disabled {
      opacity: 0.6;
      cursor: progress;
      box-shadow: none;
      transform: none;
    }
    .form-grid {
      display: grid;
      gap: 1.1rem;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }
    .picker-row {
      display: flex;
      gap: 0.6rem;
      align-items: center;
    }
    .picker-row input {
      flex: 1 1 auto;
    }
    .path-button {
      padding: 0.6rem 1.1rem;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--input-bg);
      color: inherit;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease, color 0.2s ease, box-shadow 0.2s ease;
      transform: none;
      box-shadow: none;
    }
    .path-button:hover {
      background: var(--hover-bg);
      color: var(--accent);
      box-shadow: 0 6px 18px var(--shadow);
      transform: none;
    }
    .path-button.secondary {
      background: transparent;
    }
    .path-button.secondary:hover {
      background: var(--hover-bg);
      color: inherit;
    }
    .button-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.85rem;
      margin-top: 1rem;
    }
    .status-line {
      margin-top: 0.85rem;
      min-height: 1.4rem;
      font-weight: 600;
      color: var(--muted);
    }
    .status-line.error {
      color: var(--danger);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }
    table thead {
      background: rgba(255, 255, 255, 0.05);
    }
    table th {
      text-align: left;
      padding: 0.75rem;
      border-bottom: 2px solid var(--border);
      font-weight: 600;
    }
    table td {
      padding: 0.75rem;
      border-bottom: 1px solid var(--border);
    }
    table tbody tr:hover {
      background: rgba(255, 255, 255, 0.05);
    }
    .episode-row {
      transition: background 0.15s ease;
    }
    .episode-row:hover {
      background: rgba(74, 222, 128, 0.08) !important;
    }
    .source-badge {
      display: inline-block;
      padding: 0.25rem 0.6rem;
      background: rgba(74, 222, 128, 0.2);
      border: 1px solid rgba(74, 222, 128, 0.4);
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 600;
      text-transform: lowercase;
    }
    .manual-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
    }
    .logs {
      background: var(--input-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.1rem;
      max-height: 360px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: 'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace;
      font-size: 0.95rem;
    }
    .log-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      align-items: flex-end;
    }
    .log-controls label {
      flex: 1 1 180px;
    }
    .form-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 1rem;
      margin-top: 1.2rem;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.35rem 0.75rem;
      border-radius: 999px;
      background: rgba(37, 99, 235, 0.12);
      color: var(--accent);
      font-size: 0.85rem;
      font-weight: 600;
    }
    ul.channel-list {
      list-style: none;
      padding: 0;
      margin: 0.75rem 0 0 0;
      display: grid;
      gap: 0.4rem;
    }
    ul.channel-list li {
      padding: 0.5rem 0.75rem;
      border-radius: 8px;
      background: var(--input-bg);
      border: 1px solid var(--border);
      font-family: 'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace;
      font-size: 0.9rem;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #05070e;
        --fg: #f5f7fb;
        --sidebar-bg: rgba(12, 17, 26, 0.85);
        --panel-bg: rgba(15, 20, 31, 0.88);
        --input-bg: rgba(8, 11, 19, 0.92);
        --border: rgba(148, 163, 184, 0.18);
        --hover-bg: rgba(37, 99, 235, 0.16);
        --accent: #3b82f6;
        --shadow: rgba(8, 15, 37, 0.55);
        --muted: rgba(226, 232, 240, 0.78);
        --danger: #fca5a5;
      }
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f7f8fb;
        --fg: #111827;
        --sidebar-bg: rgba(244, 246, 255, 0.95);
        --panel-bg: rgba(255, 255, 255, 0.92);
        --input-bg: rgba(255, 255, 255, 0.95);
        --border: rgba(15, 23, 42, 0.12);
        --hover-bg: rgba(37, 99, 235, 0.12);
        --accent: #2563eb;
        --shadow: rgba(15, 23, 42, 0.18);
        --muted: rgba(55, 65, 81, 0.72);
        --danger: #b91c1c;
      }
    }
    @media (max-width: 960px) {
      .layout {
        flex-direction: column;
      }
      .sidebar {
        width: 100%;
        flex-direction: row;
        overflow-x: auto;
        position: sticky;
        top: 0;
        z-index: 2;
        gap: 0.5rem;
      }
      .tab-button {
        flex: 1 1 auto;
        text-align: center;
      }
      .content {
        padding: 1.5rem 1rem 3.5rem;
      }
    }
  </style>
</head>
<body>
  <div class="layout">
    <nav class="sidebar" role="tablist" aria-label="Personal Assistant configuration sections">
      <button class="tab-button active" data-tab="episodes" aria-pressed="true">Episodes</button>
      <button class="tab-button" data-tab="connections" aria-pressed="false">Connections</button>
      <button class="tab-button" data-tab="google" aria-pressed="false">Google Workspace</button>
      <button class="tab-button" data-tab="slack" aria-pressed="false">Slack</button>
      <button class="tab-button" data-tab="redaction" aria-pressed="false">Redaction &amp; Summaries</button>
      <button class="tab-button" data-tab="backups" aria-pressed="false">Backups</button>
      <button class="tab-button" data-tab="logs" aria-pressed="false">Logs</button>
      <button class="tab-button" data-tab="operations" aria-pressed="false">Operations</button>
    </nav>
    <main class="content">
      <header>
        <h1>Personal Assistant Admin</h1>
        <p>Configure data sources, schedule ingestion, and monitor state without leaving your browser.</p>
      </header>

      <section class="card" data-tab-panel="connections">
        <h2>Neo4j Graph</h2>
        <p class="hint">Update the Neo4j connection credentials and the group identifier Personal Assistant uses for all episodes.</p>
        <form id="connections-form" data-config-form autocomplete="off">
          <div class="form-grid">
            <label>URI<input type="text" name="neo4j_uri" required value="$neo4j_uri" /></label>
            <label>User<input type="text" name="neo4j_user" required value="$neo4j_user" /></label>
            <label>Password<input type="password" name="neo4j_password" required value="$neo4j_password" autocomplete="current-password" /></label>
            <label>Group ID<input type="text" name="group_id" required value="$group_id" /></label>
          </div>
          <div class="form-actions">
            <button type="submit">Save connections</button>
            <div class="status-line" id="connections-status" data-default-status></div>
          </div>
        </form>
      </section>

      <section class="card" data-tab-panel="google">
        <h2>Google Workspace</h2>
        <p class="hint">Configure OAuth credentials, polling intervals, and calendar defaults for Gmail, Drive, and Calendar.</p>
        <form id="google-config-form" data-config-form autocomplete="off">
          <div class="form-grid">
            <label>Client ID<input type="text" name="google_client_id" value="$google_client_id" placeholder="xxxxxxxx.apps.googleusercontent.com" /></label>
            <label>Client Secret<input type="password" name="google_client_secret" value="$google_client_secret" autocomplete="new-password" placeholder="Your OAuth secret" /></label>
            <label>Gmail/Drive/Calendar Interval (seconds)<input type="number" min="1" name="poll_gmail_drive_calendar_seconds" value="$poll_gmail_drive_calendar_seconds" required /></label>
            <label>Gmail Fallback (days)<input type="number" min="1" name="gmail_fallback_days" value="$gmail_fallback_days" required /></label>
            <label>Calendar IDs<input type="text" name="calendar_ids" placeholder="primary, team@domain.com" value="$calendars" /></label>
          </div>
          <div class="form-grid">
            <label>Gmail Backfill (days)<input type="number" min="1" name="gmail_backfill_days" value="$gmail_backfill_days" required /></label>
            <label>Drive Backfill (days)<input type="number" min="1" name="drive_backfill_days" value="$drive_backfill_days" required /></label>
            <label>Calendar Backfill (days)<input type="number" min="1" name="calendar_backfill_days" value="$calendar_backfill_days" required /></label>
          </div>
          <div class="button-row">
            <button type="button" id="google-signin">Sign in with Google</button>
            <span class="badge">Scopes: gmail, drive, calendar</span>
          </div>
          <div class="status-line" id="google-auth-status"></div>
          <div class="form-actions">
            <button type="submit">Save Google settings</button>
            <div class="status-line" id="google-config-status"></div>
          </div>
        </form>
      </section>

      <section class="card" data-tab-panel="slack">
        <h2>Slack Workspace</h2>
        <p class="hint">Tune polling behaviour for Slack and manage workspace credentials.</p>
        <form id="slack-settings-form" data-config-form autocomplete="off">
          <div class="form-grid">
            <label>Slack Active Interval (seconds)<input type="number" min="1" name="poll_slack_active_seconds" value="$poll_slack_active_seconds" required /></label>
            <label>Slack Idle Interval (seconds)<input type="number" min="1" name="poll_slack_idle_seconds" value="$poll_slack_idle_seconds" required /></label>
            <label>Slack Backfill (days)<input type="number" min="1" name="slack_backfill_days" value="$slack_backfill_days" required /></label>
            <label>Slack Search Queries (comma-separated)<input type="text" name="slack_search_queries" placeholder="in:general, from:@user, has:link" value="$slack_search_queries" /></label>
          </div>
          <div class="form-actions">
            <button type="submit">Save Slack settings</button>
            <div class="status-line" id="slack-config-status"></div>
          </div>
        </form>
        <h3>Slack Credentials</h3>
        <p class="hint">Paste your Slack token (xoxc-...) and cookie value. Both are required for authentication. Once saved, values are masked for security.</p>
        <form id="slack-form" class="form-grid">
          <label>Workspace Label<input type="text" id="slack-workspace" placeholder="acme-corp" /></label>
          <label>Slack Token<input type="password" id="slack-token" placeholder="xoxc-..." autocomplete="off" /></label>
          <label>Slack Cookie<input type="password" id="slack-cookie" placeholder="xoxd-..." autocomplete="off" /></label>
          <div class="button-row">
            <button type="submit">Save Slack Credentials</button>
            <button type="button" id="slack-inventory">Inventory Slack Channels</button>
          </div>
        </form>
        <div class="status-line" id="slack-status"></div>
        <div class="status-line" id="slack-inventory-status"></div>
        <div id="slack-channels"></div>
      </section>

      <section class="card" data-tab-panel="redaction">
        <h2>Summaries &amp; Redaction</h2>
        <form id="redaction-form" data-config-form autocomplete="off">
          <div class="form-grid">
            <label>Strategy<input type="text" name="summarization_strategy" value="$summarization_strategy" required /></label>
            <label>Threshold (characters)<input type="number" min="1" name="summarization_threshold" value="$summarization_threshold" required /></label>
            <label>Max Summary Length<input type="number" min="1" name="summarization_max_chars" value="$summarization_max_chars" required /></label>
            <label>Sentence Count<input type="number" min="1" name="summarization_sentence_count" value="$summarization_sentence_count" required /></label>
            <label>Redaction Rules Path<input type="text" name="redaction_rules_path" value="$redaction_rules_path" placeholder="Optional JSON file" /></label>
          </div>
          <label>Inline Redaction Rules<textarea name="redaction_rules" placeholder="sensitive@example.com =&gt; [REDACTED]">$redaction_lines</textarea></label>
          <div class="form-actions">
            <button type="submit">Save redaction settings</button>
            <div class="status-line" id="redaction-status"></div>
          </div>
        </form>
      </section>

      <section class="card" data-tab-panel="backups">
        <h2>Backups</h2>
        <form id="backups-form" data-config-form autocomplete="off">
          <div class="form-grid">
            <label>Backup Directory
              <div class="picker-row">
                <input type="text" name="backup_directory" required value="$backup_directory" placeholder="Select a directory" data-directory-input="backup_directory" />
                <button type="button" class="path-button" data-directory-target="backup_directory">Choose…</button>
              </div>
            </label>
            <label>Backup Retention (days)<input type="number" min="0" name="backup_retention_days" value="$backup_retention_days" required /></label>
          </div>
          <div class="form-actions">
            <button type="submit">Save backup settings</button>
            <div class="status-line" id="backups-status"></div>
          </div>
        </form>
        <h3>Manual Backup</h3>
        <p class="hint">Create a timestamped archive of the state directory immediately.</p>
        <div class="button-row">
          <button type="button" id="run-backup">Run Backup</button>
        </div>
        <div class="status-line" id="backup-status"></div>
      </section>

      <section class="card" data-tab-panel="episodes" style="max-width: 1920px;">
        <h2>Episodes Browser</h2>
        <p class="hint">Browse ingested episodes from all sources. Episodes are the canonical units of knowledge in your graph.</p>
        
        <div id="episode-stats" style="margin-bottom: 1.5rem; padding: 1rem; background: rgba(255,255,255,0.05); border-radius: 8px;">
          Loading stats...
        </div>
        
        <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1rem;">
          <label style="display: flex; gap: 0.5rem; align-items: center;">
            Filter by source:
            <select id="episode-source-filter" class="input">
              <option value="">All Sources</option>
              <option value="gmail">Gmail</option>
              <option value="gdrive">Google Drive</option>
              <option value="calendar">Calendar</option>
              <option value="slack">Slack</option>
              <option value="mcp">MCP</option>
            </select>
          </label>
          <button type="button" id="episodes-refresh" class="button">Refresh</button>
        </div>
        
        <div id="episodes-list" style="overflow-x: auto;">
          Loading episodes...
        </div>
        
        <div class="status-line" id="episodes-status"></div>
      </section>

      <section class="card" data-tab-panel="logs">
        <h2>Logs</h2>
        <form id="logs-form" data-config-form autocomplete="off">
          <div class="form-grid">
            <label>Log Retention (days)<input type="number" min="0" name="log_retention_days" value="$log_retention_days" required /></label>
            <label>Logs Directory
              <div class="picker-row">
                <input type="text" name="logs_directory" value="$logs_directory" placeholder="Defaults to ~/.graphiti_sync/logs" data-directory-input="logs_directory" />
                <button type="button" class="path-button" data-directory-target="logs_directory">Choose…</button>
                <button type="button" class="path-button secondary" data-directory-clear="logs_directory">Clear</button>
              </div>
            </label>
          </div>
          <div class="form-actions">
            <button type="submit">Save log settings</button>
            <div class="status-line" id="logs-config-status"></div>
          </div>
        </form>
        <div class="log-controls">
          <label>Category
            <select id="log-category">
              <option value="system">system</option>
              <option value="episodes">episodes</option>
            </select>
          </label>
          <label>Limit<input type="number" id="log-limit" value="200" min="1" /></label>
          <label>Since (days)<input type="number" id="log-since" value="0" min="0" /></label>
          <button type="button" id="refresh-logs">Refresh</button>
        </div>
        <div class="status-line" id="logs-status"></div>
        <pre class="logs" id="logs"></pre>
      </section>

      <section class="card" data-tab-panel="operations">
        <h2>Manual Historical Load</h2>
        <p class="hint">Run backfills for each service. Override the default number of days before launching.</p>
        <div class="manual-grid">
          <label>Gmail Days<input type="number" name="gmail_manual_days" data-default="$gmail_backfill_days" value="$gmail_backfill_days" min="1" /></label>
          <label>Drive Days<input type="number" name="drive_manual_days" data-default="$drive_backfill_days" value="$drive_backfill_days" min="1" /></label>
          <label>Calendar Days<input type="number" name="calendar_manual_days" data-default="$calendar_backfill_days" value="$calendar_backfill_days" min="1" /></label>
          <label>Slack Days<input type="number" name="slack_manual_days" data-default="$slack_backfill_days" value="$slack_backfill_days" min="1" /></label>
        </div>
        <div class="button-row">
          <button type="button" data-service="gmail">Run Gmail Backfill</button>
          <button type="button" data-service="drive">Run Drive Backfill</button>
          <button type="button" data-service="calendar">Run Calendar Backfill</button>
          <button type="button" data-service="slack">Run Slack Backfill</button>
        </div>
        <div class="status-line" id="loader-status"></div>
      </section>

      <section class="card" data-tab-panel="operations">
        <h2>Run Pollers Once</h2>
        <p class="hint">Trigger an incremental sync for each connector to verify live ingestion.</p>
        <div class="button-row">
          <button type="button" data-poller="gmail">Run Gmail Poller</button>
          <button type="button" data-poller="drive">Run Drive Poller</button>
          <button type="button" data-poller="calendar">Run Calendar Poller</button>
          <button type="button" data-poller="slack">Run Slack Poller</button>
        </div>
        <div class="status-line" id="poller-status"></div>
      </section>
    </main>
  </div>
  <script src="/static/admin.js"></script>
</body>
</html>"""


__all__ = ["oauth_result_page", "render_index_page"]

