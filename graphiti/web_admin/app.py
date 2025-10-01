"""FastAPI application powering the Graphiti admin UI."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator

from ..cli import (
    close_episode_store,
    create_calendar_poller,
    create_drive_poller,
    create_episode_store,
    create_gmail_poller,
    create_slack_client,
)
from ..config import ConfigStore, GraphitiConfig
from ..logs import GraphitiLogStore
from ..maintenance import BackupScheduler
from ..pollers.slack import SlackPoller
from ..state import GraphitiStateStore


class RedactionRule(BaseModel):
    pattern: str = Field(..., min_length=1)
    replacement: str = Field("[REDACTED]", min_length=1)

    class Config:
        frozen = True


class ConfigPayload(BaseModel):
    neo4j_uri: str = Field(..., min_length=1)
    neo4j_user: str = Field(..., min_length=1)
    neo4j_password: str = Field(..., min_length=1)
    group_id: str = Field(..., min_length=1)
    poll_gmail_drive_calendar_seconds: int = Field(..., ge=1)
    poll_slack_active_seconds: int = Field(..., ge=1)
    poll_slack_idle_seconds: int = Field(..., ge=1)
    gmail_fallback_days: int = Field(..., ge=1)
    gmail_backfill_days: int = Field(..., ge=1)
    drive_backfill_days: int = Field(..., ge=1)
    calendar_backfill_days: int = Field(..., ge=1)
    slack_backfill_days: int = Field(..., ge=1)
    slack_channel_allowlist: list[str] = Field(default_factory=list)
    calendar_ids: list[str] = Field(default_factory=lambda: ["primary"])
    redaction_rules_path: str | None = None
    redaction_rules: list[RedactionRule] = Field(default_factory=list)
    summarization_strategy: str = Field("heuristic", min_length=1)
    summarization_threshold: int = Field(..., ge=1)
    summarization_max_chars: int = Field(..., ge=1)
    summarization_sentence_count: int = Field(..., ge=1)
    backup_directory: str = Field(..., min_length=1)
    backup_retention_days: int = Field(..., ge=0)
    log_retention_days: int = Field(..., ge=0)
    logs_directory: str | None = None

    @validator("slack_channel_allowlist", "calendar_ids", pre=True)
    def _strip_items(cls, value: Any) -> list[str]:  # noqa: D401,N805
        """Ensure list-like inputs are converted to cleaned string lists."""

        if value is None:
            return []
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
        else:
            items = [str(item).strip() for item in value if str(item).strip()]
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            result.append(item)
        return result

    @validator("logs_directory", pre=True)
    def _empty_to_none(cls, value: Any) -> str | None:  # noqa: D401,N805
        """Normalise optional path fields."""

        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def to_config(self) -> GraphitiConfig:
        return GraphitiConfig.from_json(self.model_dump())

    @classmethod
    def from_config(cls, config: GraphitiConfig) -> "ConfigPayload":
        return cls(**config.to_json())


class ManualLoadPayload(BaseModel):
    days: int = Field(..., ge=1)


def create_app(config_path: Path | None = None) -> FastAPI:
    """Create a FastAPI application exposing the admin UI."""

    store = ConfigStore(path=config_path)
    state_store = GraphitiStateStore()
    initial_config = store.load()
    log_store = GraphitiLogStore(_logs_directory(initial_config, state_store))
    log_store.prune(initial_config.log_retention_days)
    scheduler = BackupScheduler(
        state_store=state_store,
        config_store=store,
        log_store=log_store,
    )

    app = FastAPI(title="Graphiti Admin", version="1.0.0")

    def _refresh_log_store(config: GraphitiConfig) -> None:
        nonlocal log_store
        log_store = GraphitiLogStore(_logs_directory(config, state_store))
        log_store.prune(config.log_retention_days)
        scheduler.update_log_store(log_store)

    async def _run_manual_load(source: str, days: int) -> dict[str, Any]:
        config = store.load()
        episode_store = create_episode_store(config)
        processed = 0
        try:
            if source == "gmail":
                poller = create_gmail_poller(config, state_store, episode_store)
                processed = await asyncio.to_thread(poller.backfill, days)
            elif source == "drive":
                poller = create_drive_poller(config, state_store, episode_store)
                processed = await asyncio.to_thread(poller.backfill, days)
            elif source == "calendar":
                poller = create_calendar_poller(
                    config, state_store, episode_store
                )
                processed = await asyncio.to_thread(poller.backfill, days)
            elif source == "slack":
                client = create_slack_client(config, state_store)
                poller = SlackPoller(
                    client,
                    episode_store,
                    state_store,
                    allowlist=config.slack_channel_allowlist,
                    config=config,
                )
                processed = await asyncio.to_thread(poller.backfill, days)
            else:  # pragma: no cover - defensive
                raise HTTPException(status_code=404, detail="Unknown source")
        finally:
            close_episode_store(episode_store)

        log_store.append(
            "episodes",
            f"{source} backfill processed {processed} episodes",
            data={"source": source, "days": days},
            retention_days=config.log_retention_days,
        )
        log_store.append(
            "system",
            f"Manual {source} backfill completed",
            data={"processed": processed, "days": days},
            retention_days=config.log_retention_days,
        )
        return {"source": source, "processed": processed, "days": days}

    @app.on_event("startup")
    async def _startup() -> None:  # pragma: no cover - exercised in integration
        await scheduler.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover - exercised in integration
        await scheduler.stop()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        config = store.load()
        html = _render_index(ConfigPayload.from_config(config))
        return HTMLResponse(content=html)

    @app.get("/api/config", response_model=ConfigPayload)
    async def get_config() -> ConfigPayload:
        config = store.load()
        return ConfigPayload.from_config(config)

    @app.post("/api/config", response_model=ConfigPayload)
    async def update_config(payload: ConfigPayload) -> ConfigPayload:
        try:
            config = payload.to_config()
        except ValueError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        saved = store.save(config)
        _refresh_log_store(saved)
        return ConfigPayload.from_config(saved)

    @app.post("/api/manual-load/{source}")
    async def manual_load(source: str, payload: ManualLoadPayload) -> dict[str, Any]:
        return await _run_manual_load(source, payload.days)

    @app.post("/api/backup/run")
    async def trigger_backup() -> dict[str, Any]:
        archive = await scheduler.trigger()
        config = store.load()
        message = "Backup triggered"
        if archive:
            log_store.append(
                "system",
                "Manual backup created",
                data={"archive": str(archive)},
                retention_days=config.log_retention_days,
            )
        else:
            message = "Backup request queued"
        return {"archive": str(archive) if archive else None, "status": message}

    @app.get("/api/logs")
    async def get_logs(
        category: str = "system",
        limit: int = 200,
        since_days: int | None = None,
    ) -> dict[str, Any]:
        config = store.load()
        since = None
        if since_days and since_days > 0:
            since = datetime.now(timezone.utc) - timedelta(days=since_days)
        records = log_store.tail(category, limit=max(limit, 1), since=since)
        payload = [record.to_json() for record in records]
        log_store.prune(config.log_retention_days)
        return {"category": category, "records": payload}

    @app.get("/api/logs/categories")
    async def get_log_categories() -> dict[str, Any]:
        categories = set(log_store.categories()) | {"system", "episodes"}
        return {"categories": sorted(categories)}

    return app


def _render_index(payload: ConfigPayload) -> str:
    allowlist = ", ".join(payload.slack_channel_allowlist)
    calendars = ", ".join(payload.calendar_ids)
    redaction_lines = "\n".join(
        f"{rule.pattern} => {rule.replacement}" for rule in payload.redaction_rules
    )
    template = Template("""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Graphiti Admin</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
    body {
      margin: 0;
      padding: 0;
      background-color: var(--bg);
      color: var(--fg);
    }
    .container {
      max-width: 1000px;
      margin: 0 auto;
      padding: 2rem 1.5rem 4rem;
    }
    header {
      margin-bottom: 2rem;
    }
    h1 {
      font-size: 2rem;
      margin: 0 0 0.5rem 0;
    }
    section {
      margin-bottom: 2rem;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.5rem;
      background: var(--panel-bg);
      box-shadow: 0 10px 30px var(--shadow);
    }
    section h2 {
      margin-top: 0;
      font-size: 1.25rem;
    }
    label {
      display: flex;
      flex-direction: column;
      font-weight: 600;
      margin-bottom: 1rem;
      gap: 0.4rem;
    }
    input, textarea, select {
      padding: 0.6rem 0.8rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--input-bg);
      color: inherit;
      font-size: 1rem;
    }
    textarea {
      min-height: 110px;
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
      transition: background 0.2s ease;
    }
    button:disabled {
      opacity: 0.6;
      cursor: progress;
    }
    .status-line {
      margin-top: 0.75rem;
      min-height: 1.25rem;
      font-weight: 600;
    }
    .manual-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      align-items: end;
    }
    .manual-grid label {
      margin-bottom: 0.5rem;
    }
    .log-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      align-items: flex-end;
    }
    .log-controls label {
      flex: 1 1 160px;
    }
    .logs {
      background: var(--input-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
      max-height: 320px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0b0c10;
        --fg: #f3f4f6;
        --panel-bg: rgba(18, 21, 26, 0.8);
        --border: rgba(255, 255, 255, 0.08);
        --input-bg: rgba(12, 13, 17, 0.9);
        --accent: #3b82f6;
        --shadow: rgba(0, 0, 0, 0.45);
      }
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f7f8fb;
        --fg: #1f2937;
        --panel-bg: rgba(255, 255, 255, 0.9);
        --border: rgba(15, 23, 42, 0.1);
        --input-bg: rgba(255, 255, 255, 0.9);
        --accent: #2563eb;
        --shadow: rgba(15, 23, 42, 0.12);
      }
    }
  </style>
</head>
<body>
  <div class=\"container\">
    <header>
      <h1>Graphiti Admin</h1>
      <p>Configure data sources, scheduling, and backfills in one place.</p>
    </header>
    <form id=\"config-form\" autocomplete=\"off\">
      <section>
        <h2>Neo4j Connection</h2>
        <label>URI<input type=\"text\" name=\"neo4j_uri\" required value=\"$neo4j_uri\" /></label>
        <label>User<input type=\"text\" name=\"neo4j_user\" required value=\"$neo4j_user\" /></label>
        <label>Password<input type=\"password\" name=\"neo4j_password\" required value=\"$neo4j_password\" /></label>
        <label>Group ID<input type=\"text\" name=\"group_id\" required value=\"$group_id\" /></label>
      </section>
      <section>
        <h2>Polling Behaviour</h2>
        <label>Gmail/Drive/Calendar Interval (seconds)<input type=\"number\" min=\"1\" name=\"poll_gmail_drive_calendar_seconds\" value=\"$poll_gmail_drive_calendar_seconds\" required /></label>
        <label>Slack Active Interval (seconds)<input type=\"number\" min=\"1\" name=\"poll_slack_active_seconds\" value=\"$poll_slack_active_seconds\" required /></label>
        <label>Slack Idle Interval (seconds)<input type=\"number\" min=\"1\" name=\"poll_slack_idle_seconds\" value=\"$poll_slack_idle_seconds\" required /></label>
        <label>Gmail Fallback (days)<input type=\"number\" min=\"1\" name=\"gmail_fallback_days\" value=\"$gmail_fallback_days\" required /></label>
        <label>Slack Channel Allowlist<input type=\"text\" name=\"slack_channel_allowlist\" placeholder=\"C1, C2, project\" value=\"$allowlist\" /></label>
        <label>Calendar IDs<input type=\"text\" name=\"calendar_ids\" placeholder=\"primary, team@domain.com\" value=\"$calendars\" /></label>
      </section>
      <section>
        <h2>Historical Import Defaults</h2>
        <label>Gmail Backfill (days)<input type=\"number\" min=\"1\" name=\"gmail_backfill_days\" value=\"$gmail_backfill_days\" required /></label>
        <label>Drive Backfill (days)<input type=\"number\" min=\"1\" name=\"drive_backfill_days\" value=\"$drive_backfill_days\" required /></label>
        <label>Calendar Backfill (days)<input type=\"number\" min=\"1\" name=\"calendar_backfill_days\" value=\"$calendar_backfill_days\" required /></label>
        <label>Slack Backfill (days)<input type=\"number\" min=\"1\" name=\"slack_backfill_days\" value=\"$slack_backfill_days\" required /></label>
      </section>
      <section>
        <h2>Summarisation & Redaction</h2>
        <label>Strategy<input type=\"text\" name=\"summarization_strategy\" value=\"$summarization_strategy\" required /></label>
        <label>Threshold (characters)<input type=\"number\" min=\"1\" name=\"summarization_threshold\" value=\"$summarization_threshold\" required /></label>
        <label>Max Summary Length<input type=\"number\" min=\"1\" name=\"summarization_max_chars\" value=\"$summarization_max_chars\" required /></label>
        <label>Sentence Count<input type=\"number\" min=\"1\" name=\"summarization_sentence_count\" value=\"$summarization_sentence_count\" required /></label>
        <label>Redaction Rules Path<input type=\"text\" name=\"redaction_rules_path\" value=\"$redaction_rules_path\" /></label>
        <label>Inline Redaction Rules<textarea name=\"redaction_rules\" placeholder=\"sensitive@example.com =&gt; [REDACTED]\">$redaction_lines</textarea></label>
      </section>
      <section>
        <h2>Backups & Logging</h2>
        <label>Backup Directory<input type=\"text\" name=\"backup_directory\" required value=\"$backup_directory\" /></label>
        <label>Backup Retention (days)<input type=\"number\" min=\"0\" name=\"backup_retention_days\" value=\"$backup_retention_days\" required /></label>
        <label>Log Retention (days)<input type=\"number\" min=\"0\" name=\"log_retention_days\" value=\"$log_retention_days\" required /></label>
        <label>Logs Directory<input type=\"text\" name=\"logs_directory\" value=\"$logs_directory\" placeholder=\"Defaults to state directory\" /></label>
        <button type=\"button\" id=\"run-backup\">Run backup now</button>
        <div id=\"backup-status\" class=\"status-line\" role=\"status\"></div>
      </section>
      <div>
        <button type=\"submit\">Save configuration</button>
        <div id=\"status\" class=\"status-line\" role=\"status\"></div>
      </div>
    </form>

    <section>
      <h2>Manual Historical Load</h2>
      <p>Trigger on-demand backfills when onboarding or reseeding data.</p>
      <div class=\"manual-grid\">
        <div>
          <label>Gmail (days)<input type=\"number\" min=\"1\" name=\"gmail_manual_days\" value=\"$gmail_backfill_days\" data-default=\"$gmail_backfill_days\" /></label>
          <button type=\"button\" data-service=\"gmail\">Run Gmail Backfill</button>
        </div>
        <div>
          <label>Drive (days)<input type=\"number\" min=\"1\" name=\"drive_manual_days\" value=\"$drive_backfill_days\" data-default=\"$drive_backfill_days\" /></label>
          <button type=\"button\" data-service=\"drive\">Run Drive Backfill</button>
        </div>
        <div>
          <label>Calendar (days)<input type=\"number\" min=\"1\" name=\"calendar_manual_days\" value=\"$calendar_backfill_days\" data-default=\"$calendar_backfill_days\" /></label>
          <button type=\"button\" data-service=\"calendar\">Run Calendar Backfill</button>
        </div>
        <div>
          <label>Slack (days)<input type=\"number\" min=\"1\" name=\"slack_manual_days\" value=\"$slack_backfill_days\" data-default=\"$slack_backfill_days\" /></label>
          <button type=\"button\" data-service=\"slack\">Run Slack Backfill</button>
        </div>
      </div>
      <div id=\"loader-status\" class=\"status-line\" role=\"status\"></div>
    </section>

    <section>
      <h2>Logs</h2>
      <div class=\"log-controls\">
        <label>Category<select id=\"log-category\"></select></label>
        <label>Limit<input type=\"number\" id=\"log-limit\" value=\"200\" min=\"1\" /></label>
        <label>Since (days)<input type=\"number\" id=\"log-since\" min=\"0\" placeholder=\"30\" /></label>
        <button type=\"button\" id=\"refresh-logs\">Refresh</button>
      </div>
      <div id=\"logs-status\" class=\"status-line\" role=\"status\"></div>
      <pre id=\"logs-output\" class=\"logs\" aria-live=\"polite\"></pre>
    </section>
  </div>
  <script>
    const form = document.getElementById('config-form');
    const statusEl = document.getElementById('status');
    const backupStatus = document.getElementById('backup-status');
    const loaderStatus = document.getElementById('loader-status');
    const logsStatus = document.getElementById('logs-status');
    const logsOutput = document.getElementById('logs-output');
    const logCategorySelect = document.getElementById('log-category');
    const logLimitInput = document.getElementById('log-limit');
    const logSinceInput = document.getElementById('log-since');

    const parseList = (value) => value.split(',').map((item) => item.trim()).filter(Boolean);

    const parseRedaction = (value) => {
      return value.split('\\n').map((line) => line.trim()).filter(Boolean).map((line) => {
        const [pattern, replacement] = line.split('=>').map((part) => part.trim());
        if (!pattern) {
          return null;
        }
        return { pattern, replacement: replacement || '[REDACTED]' };
      }).filter(Boolean);
    };

    const setStatus = (element, message, isError = false) => {
      if (!element) return;
      element.textContent = message || '';
      element.style.color = isError ? '#ef4444' : 'var(--accent)';
    };

    const submit = async (event) => {
      event.preventDefault();
      setStatus(statusEl, '');
      const data = new FormData(form);
      const payload = Object.fromEntries(data.entries());
      payload.poll_gmail_drive_calendar_seconds = Number(payload.poll_gmail_drive_calendar_seconds);
      payload.poll_slack_active_seconds = Number(payload.poll_slack_active_seconds);
      payload.poll_slack_idle_seconds = Number(payload.poll_slack_idle_seconds);
      payload.gmail_fallback_days = Number(payload.gmail_fallback_days);
      payload.gmail_backfill_days = Number(payload.gmail_backfill_days);
      payload.drive_backfill_days = Number(payload.drive_backfill_days);
      payload.calendar_backfill_days = Number(payload.calendar_backfill_days);
      payload.slack_backfill_days = Number(payload.slack_backfill_days);
      payload.backup_retention_days = Number(payload.backup_retention_days);
      payload.log_retention_days = Number(payload.log_retention_days);
      payload.summarization_threshold = Number(payload.summarization_threshold);
      payload.summarization_max_chars = Number(payload.summarization_max_chars);
      payload.summarization_sentence_count = Number(payload.summarization_sentence_count);
      payload.slack_channel_allowlist = parseList(payload.slack_channel_allowlist || '');
      payload.calendar_ids = parseList(payload.calendar_ids || '');
      payload.redaction_rules = parseRedaction(payload.redaction_rules || '');

      try {
        form.querySelector('button[type=\"submit\"]').disabled = true;
        const response = await fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || 'Unable to save configuration');
        }
        setStatus(statusEl, 'Configuration saved successfully.');
        await loadLogCategories().catch(() => {});
      } catch (error) {
        setStatus(statusEl, error.message, true);
      } finally {
        form.querySelector('button[type=\"submit\"]').disabled = false;
      }
    };

    form.addEventListener('submit', submit);

    const runBackup = async () => {
      const button = document.getElementById('run-backup');
      if (!button) return;
      setStatus(backupStatus, 'Creating backup...');
      button.disabled = true;
      try {
        const response = await fetch('/api/backup/run', { method: 'POST' });
        if (!response.ok) {
          throw new Error('Unable to trigger backup');
        }
        const data = await response.json();
        const message = data.status || 'Backup completed.';
        setStatus(backupStatus, message);
        if (data.archive) {
          await refreshLogs();
        }
      } catch (error) {
        setStatus(backupStatus, error.message, true);
      } finally {
        button.disabled = false;
      }
    };

    document.getElementById('run-backup').addEventListener('click', runBackup);

    const runManualLoad = async (service, button) => {
      const input = document.querySelector(`[name="${service}_manual_days"]`);
      const fallback = Number(input?.dataset.default || '30');
      const parsed = Number(input?.value || fallback);
      if (!Number.isFinite(parsed) || parsed < 1) {
        setStatus(loaderStatus, 'Please provide a valid number of days.', true);
        return;
      }
      const days = Math.floor(parsed);
      setStatus(loaderStatus, `Running ${service} backfill...`);
      button.disabled = true;
      try {
        const response = await fetch(`/api/manual-load/${service}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ days }),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || 'Backfill failed');
        }
        const data = await response.json();
        setStatus(loaderStatus, `${service} backfill completed: ${data.processed} episodes.`);
        await refreshLogs();
      } catch (error) {
        setStatus(loaderStatus, error.message, true);
      } finally {
        button.disabled = false;
      }
    };

    document.querySelectorAll('[data-service]').forEach((button) => {
      button.addEventListener('click', () => runManualLoad(button.dataset.service, button));
    });

    const loadLogCategories = async () => {
      try {
        const response = await fetch('/api/logs/categories');
        if (!response.ok) {
          throw new Error('Unable to load log categories');
        }
        const data = await response.json();
        const categories = data.categories || ['system', 'episodes'];
        logCategorySelect.innerHTML = '';
        categories.forEach((category) => {
          const option = document.createElement('option');
          option.value = category;
          option.textContent = category;
          logCategorySelect.appendChild(option);
        });
      } catch (error) {
        logCategorySelect.innerHTML = '<option value="system">system</option>';
        throw error;
      }
    };

    const refreshLogs = async () => {
      setStatus(logsStatus, 'Loading logs...');
      try {
        const params = new URLSearchParams();
        const category = logCategorySelect.value || 'system';
        params.set('category', category);
        const limit = Number(logLimitInput.value || '200');
        params.set('limit', String(Math.max(1, Math.floor(limit))));
        const since = Number(logSinceInput.value || '0');
        if (Number.isFinite(since) && since > 0) {
          params.set('since_days', String(Math.floor(since)));
        }
        const response = await fetch(`/api/logs?${params.toString()}`);
        if (!response.ok) {
          throw new Error('Unable to fetch logs');
        }
        const data = await response.json();
        const entries = data.records || [];
        logsOutput.textContent = entries.length
          ? entries.map((record) => JSON.stringify(record)).join('\n')
          : 'No log entries available.';
        setStatus(logsStatus, `Loaded ${entries.length} log entries.`);
      } catch (error) {
        logsOutput.textContent = '';
        setStatus(logsStatus, error.message, true);
      }
    };

    document.getElementById('refresh-logs').addEventListener('click', refreshLogs);
    logCategorySelect.addEventListener('change', refreshLogs);

    loadLogCategories()
      .then(refreshLogs)
      .catch((error) => setStatus(logsStatus, error.message, true));
  </script>
</body>
</html>""")
    return template.substitute(
        neo4j_uri=escape(payload.neo4j_uri),
        neo4j_user=escape(payload.neo4j_user),
        neo4j_password=escape(payload.neo4j_password),
        group_id=escape(payload.group_id),
        poll_gmail_drive_calendar_seconds=payload.poll_gmail_drive_calendar_seconds,
        poll_slack_active_seconds=payload.poll_slack_active_seconds,
        poll_slack_idle_seconds=payload.poll_slack_idle_seconds,
        gmail_fallback_days=payload.gmail_fallback_days,
        gmail_backfill_days=payload.gmail_backfill_days,
        drive_backfill_days=payload.drive_backfill_days,
        calendar_backfill_days=payload.calendar_backfill_days,
        slack_backfill_days=payload.slack_backfill_days,
        allowlist=escape(allowlist),
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


def _logs_directory(config: GraphitiConfig, state_store: GraphitiStateStore) -> Path:
    if config.logs_directory:
        return Path(config.logs_directory).expanduser()
    return state_store.base_dir / "logs"


__all__ = ["create_app"]
