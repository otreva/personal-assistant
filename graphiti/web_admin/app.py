"""FastAPI application powering the Graphiti admin UI."""
from __future__ import annotations

from html import escape
from pathlib import Path
from string import Template
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator

from ..config import ConfigStore, GraphitiConfig


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
    slack_channel_allowlist: list[str] = Field(default_factory=list)
    calendar_ids: list[str] = Field(default_factory=lambda: ["primary"])
    redaction_rules_path: str | None = None
    redaction_rules: list[RedactionRule] = Field(default_factory=list)
    summarization_strategy: str = Field("heuristic", min_length=1)
    summarization_threshold: int = Field(..., ge=1)
    summarization_max_chars: int = Field(..., ge=1)
    summarization_sentence_count: int = Field(..., ge=1)

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

    def to_config(self) -> GraphitiConfig:
        return GraphitiConfig.from_json(self.model_dump())

    @classmethod
    def from_config(cls, config: GraphitiConfig) -> "ConfigPayload":
        return cls(**config.to_json())


def create_app(config_path: Path | None = None) -> FastAPI:
    """Create a FastAPI application exposing the admin UI."""

    store = ConfigStore(path=config_path)
    app = FastAPI(title="Graphiti Admin", version="1.0.0")

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
        store.save(config)
        return ConfigPayload.from_config(config)

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
      max-width: 960px;
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
    input, textarea {
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
    #status {
      margin-top: 1rem;
      min-height: 1.25rem;
      font-weight: 600;
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
      <p>Configure data sources, polling intervals, and summarisation defaults in one place.</p>
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
        <h2>Summarisation & Redaction</h2>
        <label>Strategy<input type=\"text\" name=\"summarization_strategy\" value=\"$summarization_strategy\" required /></label>
        <label>Threshold (characters)<input type=\"number\" min=\"1\" name=\"summarization_threshold\" value=\"$summarization_threshold\" required /></label>
        <label>Max Summary Length<input type=\"number\" min=\"1\" name=\"summarization_max_chars\" value=\"$summarization_max_chars\" required /></label>
        <label>Sentence Count<input type=\"number\" min=\"1\" name=\"summarization_sentence_count\" value=\"$summarization_sentence_count\" required /></label>
        <label>Redaction Rules Path<input type=\"text\" name=\"redaction_rules_path\" value=\"$redaction_rules_path\" /></label>
        <label>Inline Redaction Rules<textarea name=\"redaction_rules\" placeholder=\"sensitive@example.com =&gt; [REDACTED]\">$redaction_lines</textarea></label>
      </section>
      <div>
        <button type=\"submit\">Save configuration</button>
        <div id=\"status\" role=\"status\"></div>
      </div>
    </form>
  </div>
  <script>
    const form = document.getElementById('config-form');
    const statusEl = document.getElementById('status');

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

    const submit = async (event) => {
      event.preventDefault();
      statusEl.textContent = '';
      const data = new FormData(form);
      const payload = Object.fromEntries(data.entries());
      payload.poll_gmail_drive_calendar_seconds = Number(payload.poll_gmail_drive_calendar_seconds);
      payload.poll_slack_active_seconds = Number(payload.poll_slack_active_seconds);
      payload.poll_slack_idle_seconds = Number(payload.poll_slack_idle_seconds);
      payload.gmail_fallback_days = Number(payload.gmail_fallback_days);
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
        statusEl.textContent = 'Configuration saved successfully.';
        statusEl.style.color = 'var(--accent)';
      } catch (error) {
        statusEl.textContent = error.message;
        statusEl.style.color = '#ef4444';
      } finally {
        form.querySelector('button[type=\"submit\"]').disabled = false;
      }
    };

    form.addEventListener('submit', submit);
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
        allowlist=escape(allowlist),
        calendars=escape(calendars),
        summarization_strategy=escape(payload.summarization_strategy),
        summarization_threshold=payload.summarization_threshold,
        summarization_max_chars=payload.summarization_max_chars,
        summarization_sentence_count=payload.summarization_sentence_count,
        redaction_rules_path=escape(payload.redaction_rules_path or ""),
        redaction_lines=escape(redaction_lines),
    )


__all__ = ["create_app"]
