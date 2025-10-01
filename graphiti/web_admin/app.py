"""FastAPI application powering the Personal Assistant admin UI."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Mapping
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator

try:  # pragma: no cover - optional dependency
    import tkinter  # type: ignore
    from tkinter import filedialog  # type: ignore
except Exception:  # pragma: no cover - tkinter not available
    tkinter = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]

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


GOOGLE_OAUTH_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)

GOOGLE_OAUTH_SESSION_TTL = 600

_oauth_sessions: dict[str, dict[str, Any]] = {}


def _purge_oauth_sessions() -> None:
    now = time.time()
    expired = [
        state for state, payload in _oauth_sessions.items() if payload.get("expires_at", 0) <= now
    ]
    for state in expired:
        _oauth_sessions.pop(state, None)


def _register_oauth_session(state: str, payload: Mapping[str, Any]) -> None:
    _purge_oauth_sessions()
    data = dict(payload)
    data["expires_at"] = time.time() + GOOGLE_OAUTH_SESSION_TTL
    _oauth_sessions[state] = data


def _pop_oauth_session(state: str) -> dict[str, Any] | None:
    payload = _oauth_sessions.pop(state, None)
    if not payload:
        return None
    if payload.get("expires_at", 0) <= time.time():
        return None
    return payload


def _normalise_scope_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.replace(",", " ")
        return [scope for scope in cleaned.split() if scope]
    if isinstance(value, Mapping):
        return _normalise_scope_list(value.get("scopes"))
    try:
        iterator = iter(value)  # type: ignore[arg-type]
    except TypeError:
        return []
    result: list[str] = []
    for entry in iterator:
        text = str(entry).strip()
        if text:
            result.append(text)
    return result


def _load_token_section(
    state_store: GraphitiStateStore, section: str
) -> dict[str, Any]:
    tokens = state_store.load_tokens()
    payload = tokens.get(section)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def _persist_token_section(
    state_store: GraphitiStateStore, section: str, values: Mapping[str, Any]
) -> dict[str, Any]:
    tokens = state_store.load_tokens()
    merged = dict(tokens)
    merged[section] = dict(values)
    state_store.save_tokens(merged)
    return merged[section]


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _oauth_result_page(success: bool, message: str) -> str:
    status = "success" if success else "error"
    safe_message = escape(message)
    return f"""<!DOCTYPE html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>Google OAuth</title>
    <style>
      body {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        margin: 0;
        padding: 2rem;
        background: #0b0c10;
        color: #f5f5f5;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
      }}
      .panel {{
        max-width: 420px;
        background: rgba(255, 255, 255, 0.08);
        border-radius: 16px;
        padding: 2rem;
        box-shadow: 0 18px 45px rgba(0, 0, 0, 0.45);
        text-align: center;
      }}
      .panel.success {{
        border: 1px solid rgba(74, 222, 128, 0.6);
      }}
      .panel.error {{
        border: 1px solid rgba(248, 113, 113, 0.6);
      }}
      h1 {{
        font-size: 1.5rem;
        margin-bottom: 1rem;
      }}
      p {{
        margin: 0;
        line-height: 1.6;
      }}
    </style>
    <script>
      window.addEventListener('load', () => {{
        try {{
          if (window.opener && typeof window.opener.postMessage === 'function') {{
            window.opener.postMessage({{
              type: 'google-oauth',
              status: '{status}',
              message: '{safe_message}',
            }}, '*');
          }}
        }} catch (error) {{
          console.warn('Unable to notify parent window', error);
        }}
        setTimeout(() => window.close(), 500);
      }});
    </script>
  </head>
  <body>
    <div class=\"panel {status}\">
      <h1>Google OAuth</h1>
      <p>{safe_message}</p>
    </div>
  </body>
</html>"""


class ConfigPayload(BaseModel):
    neo4j_uri: str = Field(..., min_length=1)
    neo4j_user: str = Field(..., min_length=1)
    neo4j_password: str = Field(..., min_length=1)
    google_client_id: str = Field("", min_length=0)
    google_client_secret: str = Field("", min_length=0)
    group_id: str = Field(..., min_length=1)
    poll_gmail_drive_calendar_seconds: int = Field(..., ge=1)
    poll_slack_active_seconds: int = Field(..., ge=1)
    poll_slack_idle_seconds: int = Field(..., ge=1)
    gmail_fallback_days: int = Field(..., ge=1)
    gmail_backfill_days: int = Field(..., ge=1)
    drive_backfill_days: int = Field(..., ge=1)
    calendar_backfill_days: int = Field(..., ge=1)
    slack_backfill_days: int = Field(..., ge=1)
    slack_search_query: str = Field("", min_length=0)
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

    @validator("calendar_ids", pre=True)
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


class SlackAuthPayload(BaseModel):
    workspace: str = Field("", min_length=0)
    user_token: str = Field("", min_length=0)


class DirectoryRequest(BaseModel):
    title: str | None = None
    initial: str | None = None


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

    app = FastAPI(title="Personal Assistant Admin", version="1.0.0")

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

    async def _run_poller_once(source: str) -> dict[str, Any]:
        config = store.load()
        episode_store = create_episode_store(config)
        processed = 0
        try:
            if source == "gmail":
                poller = create_gmail_poller(config, state_store, episode_store)
                processed = await asyncio.to_thread(poller.run_once)
            elif source == "drive":
                poller = create_drive_poller(config, state_store, episode_store)
                processed = await asyncio.to_thread(poller.run_once)
            elif source == "calendar":
                poller = create_calendar_poller(
                    config, state_store, episode_store
                )
                processed = await asyncio.to_thread(poller.run_once)
            elif source == "slack":
                client = create_slack_client(config, state_store)
                poller = SlackPoller(
                    client,
                    episode_store,
                    state_store,
                    config=config,
                )
                processed = await asyncio.to_thread(poller.run_once)
            else:  # pragma: no cover - defensive
                raise HTTPException(status_code=404, detail="Unknown source")
        finally:
            close_episode_store(episode_store)

        config = store.load()
        log_store.append(
            "system",
            f"Manual {source} poller run completed",
            data={"processed": processed},
            retention_days=config.log_retention_days,
        )
        return {"source": source, "processed": processed}

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

    @app.get("/api/auth/google/status")
    async def google_status() -> dict[str, Any]:
        config = store.load()
        tokens = _load_token_section(state_store, "google")
        scopes = tokens.get("scopes")
        return {
            "client_id": config.google_client_id,
            "has_client": bool(config.google_client_id),
            "has_secret": bool(config.google_client_secret),
            "has_refresh_token": bool(tokens.get("refresh_token")),
            "scopes": _normalise_scope_list(scopes) or list(GOOGLE_OAUTH_SCOPES),
            "updated_at": tokens.get("updated_at"),
        }

    @app.post("/api/auth/google/start")
    async def google_start(request: Request) -> dict[str, Any]:
        config = store.load()
        if not config.google_client_id or not config.google_client_secret:
            raise HTTPException(
                status_code=400,
                detail="Google client ID and secret must be configured before signing in.",
            )

        verifier, challenge = _generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        redirect_uri = str(request.url_for("google_oauth_callback"))
        _register_oauth_session(
            state,
            {
                "code_verifier": verifier,
                "client_id": config.google_client_id,
                "client_secret": config.google_client_secret,
                "redirect_uri": redirect_uri,
            },
        )

        params = {
            "client_id": config.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_OAUTH_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return {
            "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        }

    @app.get(
        "/api/auth/google/callback",
        response_class=HTMLResponse,
        name="google_oauth_callback",
    )
    async def google_callback(
        request: Request,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        if error:
            return HTMLResponse(
                content=_oauth_result_page(False, f"Google sign-in failed: {error}"),
                status_code=400,
            )
        if not state or not code:
            return HTMLResponse(
                content=_oauth_result_page(False, "Missing OAuth response parameters."),
                status_code=400,
            )
        session = _pop_oauth_session(state)
        if not session:
            return HTMLResponse(
                content=_oauth_result_page(False, "OAuth session has expired. Please try again."),
                status_code=400,
            )
        data = {
            "client_id": session["client_id"],
            "client_secret": session["client_secret"],
            "code": code,
            "code_verifier": session["code_verifier"],
            "grant_type": "authorization_code",
            "redirect_uri": session["redirect_uri"],
        }
        try:
            response = requests.post(
                "https://oauth2.googleapis.com/token",
                data=data,
                timeout=20,
            )
            response.raise_for_status()
        except requests.RequestException as exc:  # pragma: no cover - network errors
            return HTMLResponse(
                content=_oauth_result_page(False, f"Token exchange failed: {exc}"),
                status_code=502,
            )
        payload = response.json()
        google_tokens = _load_token_section(state_store, "google")
        google_tokens.update(
            {
                "client_id": session["client_id"],
                "client_secret": session["client_secret"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        refresh_token = payload.get("refresh_token")
        if refresh_token:
            google_tokens["refresh_token"] = refresh_token
        access_token = payload.get("access_token")
        if access_token:
            google_tokens["access_token"] = access_token
        scopes = _normalise_scope_list(payload.get("scope"))
        if scopes:
            google_tokens["scopes"] = scopes
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expiry = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
            google_tokens["access_token_expires_at"] = expiry.isoformat()
        _persist_token_section(state_store, "google", google_tokens)
        return HTMLResponse(
            content=_oauth_result_page(True, "Google authorisation complete. You can close this window."),
        )

    @app.get("/api/auth/slack")
    async def slack_status() -> dict[str, Any]:
        tokens = _load_token_section(state_store, "slack")
        return {
            "workspace": tokens.get("workspace", ""),
            "has_token": bool(tokens.get("user_token")),
            "updated_at": tokens.get("updated_at"),
        }

    @app.post("/api/auth/slack")
    async def slack_update(payload: SlackAuthPayload) -> dict[str, Any]:
        tokens = {
            "workspace": payload.workspace.strip(),
            "user_token": payload.user_token.strip(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if not tokens["user_token"]:
            raise HTTPException(status_code=400, detail="A Slack user token is required.")
        saved = _persist_token_section(state_store, "slack", tokens)
        log_store.append(
            "system",
            "Slack credentials updated",
            data={"workspace": saved.get("workspace")},
            retention_days=store.load().log_retention_days,
        )
        return {"workspace": saved.get("workspace", ""), "updated_at": saved.get("updated_at")}

    @app.post("/api/slack/inventory")
    async def slack_inventory() -> dict[str, Any]:
        config = store.load()
        client = create_slack_client(config, state_store)
        channels = list(client.list_channels())
        state_store.update_state(
            {
                "slack": {
                    "channels": {
                        str(entry.get("id")): {"metadata": dict(entry)}
                        for entry in channels
                        if isinstance(entry, Mapping) and entry.get("id")
                    },
                    "last_inventory_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        )
        log_store.append(
            "system",
            "Slack channels inventoried",
            data={"count": len(channels)},
            retention_days=config.log_retention_days,
        )
        return {"channels": channels}

    @app.post("/api/dialog/directory")
    async def choose_directory(payload: DirectoryRequest) -> dict[str, Any]:
        if filedialog is None or tkinter is None:
            raise HTTPException(status_code=501, detail="Directory picker unavailable")

        def _select() -> str | None:
            root = None
            try:
                root = tkinter.Tk()  # type: ignore[call-arg]
                root.withdraw()
                options: dict[str, Any] = {}
                if payload.title:
                    options["title"] = payload.title
                initial = (payload.initial or "").strip()
                if initial:
                    options["initialdir"] = initial
                path = filedialog.askdirectory(**options)  # type: ignore[misc]
                return str(path) if path else None
            except Exception as exc:  # pragma: no cover - GUI errors
                raise RuntimeError(str(exc)) from exc
            finally:
                if root is not None:
                    root.destroy()

        try:
            selection = await asyncio.to_thread(_select)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"path": selection}

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

    @app.post("/api/pollers/{source}/run")
    async def run_poller(source: str) -> dict[str, Any]:
        result = await _run_poller_once(source)
        return result

    return app


def _render_index(payload: ConfigPayload) -> str:
    search_query = payload.slack_search_query
    calendars = ", ".join(payload.calendar_ids)
    redaction_lines = "\n".join(
        f"{rule.pattern} => {rule.replacement}" for rule in payload.redaction_rules
    )
    template = Template("""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
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
  <div class=\"layout\">
    <nav class=\"sidebar\" role=\"tablist\" aria-label=\"Personal Assistant configuration sections\">
      <button class=\"tab-button active\" data-tab=\"connections\" aria-pressed=\"true\">Connections</button>
      <button class=\"tab-button\" data-tab=\"ingestion\" aria-pressed=\"false\">Ingestion</button>
      <button class=\"tab-button\" data-tab=\"operations\" aria-pressed=\"false\">Backups &amp; Logs</button>
    </nav>
    <main class=\"content\">
      <header>
        <h1>Personal Assistant Admin</h1>
        <p>Configure data sources, schedule ingestion, and monitor state without leaving your browser.</p>
      </header>
      <form id=\"config-form\" autocomplete=\"off\">
        <section class=\"card\" data-tab-panel=\"connections\">
          <h2>Neo4j Graph</h2>
          <p class=\"hint\">Update the Neo4j connection credentials and the group identifier Personal Assistant uses for all episodes.</p>
          <div class=\"form-grid\">
            <label>URI<input type=\"text\" name=\"neo4j_uri\" required value=\"$neo4j_uri\" /></label>
            <label>User<input type=\"text\" name=\"neo4j_user\" required value=\"$neo4j_user\" /></label>
            <label>Password<input type=\"password\" name=\"neo4j_password\" required value=\"$neo4j_password\" autocomplete=\"current-password\" /></label>
            <label>Group ID<input type=\"text\" name=\"group_id\" required value=\"$group_id\" /></label>
          </div>
        </section>
        <section class=\"card\" data-tab-panel=\"connections\">
          <h2>Google Workspace OAuth</h2>
          <p class=\"hint\">Store your OAuth client ID and secret from the Google Cloud Console, then authorise Personal Assistant with the required Gmail, Drive, and Calendar scopes.</p>
          <div class=\"form-grid\">
            <label>Client ID<input type=\"text\" name=\"google_client_id\" value=\"$google_client_id\" placeholder=\"xxxxxxxx.apps.googleusercontent.com\" /></label>
            <label>Client Secret<input type=\"password\" name=\"google_client_secret\" value=\"$google_client_secret\" autocomplete=\"new-password\" placeholder=\"Your OAuth secret\" /></label>
          </div>
          <div class=\"button-row\">
            <button type=\"button\" id=\"google-signin\">Sign in with Google</button>
            <span class=\"badge\">Scopes: gmail, drive, calendar</span>
          </div>
          <div class=\"status-line\" id=\"google-auth-status\"></div>
        </section>
        <section class=\"card\" data-tab-panel=\"ingestion\">
          <h2>Polling Behaviour</h2>
          <div class=\"form-grid\">
            <label>Gmail/Drive/Calendar Interval (seconds)<input type=\"number\" min=\"1\" name=\"poll_gmail_drive_calendar_seconds\" value=\"$poll_gmail_drive_calendar_seconds\" required /></label>
            <label>Slack Active Interval (seconds)<input type=\"number\" min=\"1\" name=\"poll_slack_active_seconds\" value=\"$poll_slack_active_seconds\" required /></label>
            <label>Slack Idle Interval (seconds)<input type=\"number\" min=\"1\" name=\"poll_slack_idle_seconds\" value=\"$poll_slack_idle_seconds\" required /></label>
            <label>Gmail Fallback (days)<input type=\"number\" min=\"1\" name=\"gmail_fallback_days\" value=\"$gmail_fallback_days\" required /></label>
          </div>
          <div class=\"form-grid\">
            <label>Slack Search Query<input type=\"text\" name=\"slack_search_query\" placeholder=\"in:general OR from:@user\" value=\"$slack_search_query\" /></label>
            <label>Calendar IDs<input type=\"text\" name=\"calendar_ids\" placeholder=\"primary, team@domain.com\" value=\"$calendars\" /></label>
          </div>
        </section>
        <section class=\"card\" data-tab-panel=\"ingestion\">
          <h2>Historical Import Defaults</h2>
          <div class=\"form-grid\">
            <label>Gmail Backfill (days)<input type=\"number\" min=\"1\" name=\"gmail_backfill_days\" value=\"$gmail_backfill_days\" required /></label>
            <label>Drive Backfill (days)<input type=\"number\" min=\"1\" name=\"drive_backfill_days\" value=\"$drive_backfill_days\" required /></label>
            <label>Calendar Backfill (days)<input type=\"number\" min=\"1\" name=\"calendar_backfill_days\" value=\"$calendar_backfill_days\" required /></label>
            <label>Slack Backfill (days)<input type=\"number\" min=\"1\" name=\"slack_backfill_days\" value=\"$slack_backfill_days\" required /></label>
          </div>
        </section>
        <section class=\"card\" data-tab-panel=\"ingestion\">
          <h2>Summaries &amp; Redaction</h2>
          <div class=\"form-grid\">
            <label>Strategy<input type=\"text\" name=\"summarization_strategy\" value=\"$summarization_strategy\" required /></label>
            <label>Threshold (characters)<input type=\"number\" min=\"1\" name=\"summarization_threshold\" value=\"$summarization_threshold\" required /></label>
            <label>Max Summary Length<input type=\"number\" min=\"1\" name=\"summarization_max_chars\" value=\"$summarization_max_chars\" required /></label>
            <label>Sentence Count<input type=\"number\" min=\"1\" name=\"summarization_sentence_count\" value=\"$summarization_sentence_count\" required /></label>
            <label>Redaction Rules Path<input type=\"text\" name=\"redaction_rules_path\" value=\"$redaction_rules_path\" placeholder=\"Optional JSON file\" /></label>
          </div>
          <label>Inline Redaction Rules<textarea name=\"redaction_rules\" placeholder=\"sensitive@example.com =&gt; [REDACTED]\">$redaction_lines</textarea></label>
        </section>
        <section class=\"card\" data-tab-panel=\"operations\">
          <h2>Backups &amp; Logging</h2>
          <div class=\"form-grid\">
            <label>Backup Directory
              <div class=\"picker-row\">
                <input type=\"text\" name=\"backup_directory\" required value=\"$backup_directory\" placeholder=\"Select a directory\" data-directory-input=\"backup_directory\" />
                <button type=\"button\" class=\"path-button\" data-directory-target=\"backup_directory\">Choose…</button>
              </div>
            </label>
            <label>Backup Retention (days)<input type=\"number\" min=\"0\" name=\"backup_retention_days\" value=\"$backup_retention_days\" required /></label>
            <label>Log Retention (days)<input type=\"number\" min=\"0\" name=\"log_retention_days\" value=\"$log_retention_days\" required /></label>
            <label>Logs Directory
              <div class=\"picker-row\">
                <input type=\"text\" name=\"logs_directory\" value=\"$logs_directory\" placeholder=\"Defaults to ~/.graphiti_sync/logs\" data-directory-input=\"logs_directory\" />
                <button type=\"button\" class=\"path-button\" data-directory-target=\"logs_directory\">Choose…</button>
                <button type=\"button\" class=\"path-button secondary\" data-directory-clear=\"logs_directory\">Clear</button>
              </div>
            </label>
          </div>
        </section>
        <div class=\"form-actions\">
          <button type=\"submit\">Save configuration</button>
          <div class=\"status-line\" id=\"config-status\"></div>
        </div>
      </form>

      <section class=\"card\" data-tab-panel=\"connections\">
        <h2>Slack Workspace</h2>
        <p class=\"hint\">Paste a Slack user token with the required read scopes and optionally label the workspace for quick reference.</p>
        <form id=\"slack-form\" class=\"form-grid\">
          <label>Workspace Label<input type=\"text\" id=\"slack-workspace\" placeholder=\"acme-corp\" /></label>
          <label>User Token<input type=\"password\" id=\"slack-token\" placeholder=\"xoxp-...\" autocomplete=\"off\" /></label>
          <div class=\"button-row\">
            <button type=\"submit\">Save Slack Credentials</button>
            <button type=\"button\" id=\"slack-inventory\">Inventory Slack Channels</button>
          </div>
        </form>
        <div class=\"status-line\" id=\"slack-status\"></div>
        <div class=\"status-line\" id=\"slack-inventory-status\"></div>
        <div id=\"slack-channels\"></div>
      </section>

      <section class=\"card\" data-tab-panel=\"ingestion\">
        <h2>Manual Historical Load</h2>
        <p class=\"hint\">Run backfills for each service. Override the default number of days before launching.</p>
        <div class=\"manual-grid\">
          <label>Gmail Days<input type=\"number\" name=\"gmail_manual_days\" data-default=\"$gmail_backfill_days\" value=\"$gmail_backfill_days\" min=\"1\" /></label>
          <label>Drive Days<input type=\"number\" name=\"drive_manual_days\" data-default=\"$drive_backfill_days\" value=\"$drive_backfill_days\" min=\"1\" /></label>
          <label>Calendar Days<input type=\"number\" name=\"calendar_manual_days\" data-default=\"$calendar_backfill_days\" value=\"$calendar_backfill_days\" min=\"1\" /></label>
          <label>Slack Days<input type=\"number\" name=\"slack_manual_days\" data-default=\"$slack_backfill_days\" value=\"$slack_backfill_days\" min=\"1\" /></label>
        </div>
        <div class=\"button-row\">
          <button type=\"button\" data-service=\"gmail\">Run Gmail Backfill</button>
          <button type=\"button\" data-service=\"drive\">Run Drive Backfill</button>
          <button type=\"button\" data-service=\"calendar\">Run Calendar Backfill</button>
          <button type=\"button\" data-service=\"slack\">Run Slack Backfill</button>
        </div>
        <div class=\"status-line\" id=\"loader-status\"></div>
      </section>

      <section class=\"card\" data-tab-panel=\"ingestion\">
        <h2>Run Pollers Once</h2>
        <p class=\"hint\">Trigger an incremental sync for each connector to verify live ingestion.</p>
        <div class=\"button-row\">
          <button type=\"button\" data-poller=\"gmail\">Run Gmail Poller</button>
          <button type=\"button\" data-poller=\"drive\">Run Drive Poller</button>
          <button type=\"button\" data-poller=\"calendar\">Run Calendar Poller</button>
          <button type=\"button\" data-poller=\"slack\">Run Slack Poller</button>
        </div>
        <div class=\"status-line\" id=\"poller-status\"></div>
      </section>

      <section class=\"card\" data-tab-panel=\"operations\">
        <h2>Backups</h2>
        <p class=\"hint\">Create a timestamped archive of the state directory immediately.</p>
        <div class=\"button-row\">
          <button type=\"button\" id=\"run-backup\">Run Backup</button>
        </div>
        <div class=\"status-line\" id=\"backup-status\"></div>
      </section>

      <section class=\"card\" data-tab-panel=\"operations\">
        <h2>Logs</h2>
        <div class=\"log-controls\">
          <label>Category
            <select id=\"log-category\">
              <option value=\"system\">system</option>
              <option value=\"episodes\">episodes</option>
            </select>
          </label>
          <label>Limit<input type=\"number\" id=\"log-limit\" value=\"200\" min=\"1\" /></label>
          <label>Since (days)<input type=\"number\" id=\"log-since\" value=\"0\" min=\"0\" /></label>
          <button type=\"button\" id=\"refresh-logs\">Refresh</button>
        </div>
        <div class=\"status-line\" id=\"logs-status\"></div>
        <pre class=\"logs\" id=\"logs\"></pre>
      </section>
    </main>
  </div>
  <script>
    (function () {
      const tabButtons = document.querySelectorAll('.tab-button');
      const panels = document.querySelectorAll('[data-tab-panel]');
      const form = document.getElementById('config-form');
      const statusEl = document.getElementById('config-status');
      const backupStatus = document.getElementById('backup-status');
      const loaderStatus = document.getElementById('loader-status');
      const pollerStatus = document.getElementById('poller-status');
      const logsStatus = document.getElementById('logs-status');
      const logContainer = document.getElementById('logs');
      const logCategorySelect = document.getElementById('log-category');
      const logLimitInput = document.getElementById('log-limit');
      const logSinceInput = document.getElementById('log-since');
      const directoryButtons = form.querySelectorAll('[data-directory-target]');
      const clearDirectoryButtons = form.querySelectorAll('[data-directory-clear]');
      const googleStatus = document.getElementById('google-auth-status');
      const googleButton = document.getElementById('google-signin');
      const slackForm = document.getElementById('slack-form');
      const slackWorkspace = document.getElementById('slack-workspace');
      const slackToken = document.getElementById('slack-token');
      const slackStatus = document.getElementById('slack-status');
      const slackInventoryButton = document.getElementById('slack-inventory');
      const slackInventoryStatus = document.getElementById('slack-inventory-status');
      const slackChannels = document.getElementById('slack-channels');

      const setStatus = (element, message, isError = false) => {
        if (!element) return;
        element.textContent = message || '';
        if (!message) {
          element.classList.remove('error');
          return;
        }
        element.classList.toggle('error', Boolean(isError));
      };

      const activateTab = (name) => {
        tabButtons.forEach((button) => {
          const active = button.dataset.tab === name;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        panels.forEach((panel) => {
          panel.hidden = panel.dataset.tabPanel !== name;
        });
      };

      tabButtons.forEach((button) => {
        button.addEventListener('click', () => activateTab(button.dataset.tab));
      });

      const parseList = (value) => {
        if (!value) return [];
        return value
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean);
      };

      const parseRedaction = (value) => {
        if (!value) return [];
        return value
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => {
            const [pattern, replacement = '[REDACTED]'] = line.split('=>').map((part) => part.trim());
            return { pattern, replacement };
          })
          .filter((rule) => rule.pattern);
      };

      const openDirectoryPicker = async (field) => {
        const input = form.querySelector(`[data-directory-input="${field}"]`);
        if (!input) return;
        try {
          const response = await fetch('/api/dialog/directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              title: field === 'backup_directory' ? 'Select backup directory' : 'Select logs directory',
              initial: input.value || undefined,
            }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(data.detail || 'Directory picker unavailable.');
          }
          if (data.path) {
            input.value = data.path;
          }
        } catch (error) {
          if (statusEl && error instanceof Error) {
            setStatus(statusEl, error.message, true);
          }
        }
      };

      directoryButtons.forEach((button) => {
        button.addEventListener('click', async () => {
          const field = button.dataset.directoryTarget;
          if (!field) return;
          await openDirectoryPicker(field);
        });
      });

      clearDirectoryButtons.forEach((button) => {
        button.addEventListener('click', () => {
          const field = button.dataset.directoryClear;
          if (!field) return;
          const input = form.querySelector(`[data-directory-input="${field}"]`);
          if (input) {
            input.value = '';
          }
        });
      });

      const populate = (config) => {
        Object.entries(config).forEach(([key, value]) => {
          const field = form.querySelector(`[name="$${key}"]`);
          if (!field) return;
          if (Array.isArray(value)) {
            if (key === 'redaction_rules') {
              field.value = value.map((rule) => `$${rule.pattern} => $${rule.replacement}`).join('\n');
            } else {
              field.value = value.join(', ');
            }
          } else if (value === null || value === undefined) {
            field.value = '';
          } else {
            field.value = value;
          }
        });
      };

      const loadConfig = async () => {
        const response = await fetch('/api/config');
        if (!response.ok) {
          throw new Error('Unable to load configuration');
        }
        const data = await response.json();
        populate(data);
      };

      const loadGoogleStatus = async () => {
        if (!googleStatus) return;
        try {
          const response = await fetch('/api/auth/google/status');
          if (!response.ok) {
            throw new Error('Unable to load Google status');
          }
          const data = await response.json();
          if (!data.has_client || !data.has_secret) {
            setStatus(googleStatus, 'Add your Google OAuth client ID and secret, then click “Sign in with Google”.', true);
            return;
          }
          if (data.has_refresh_token) {
            const scopes = Array.isArray(data.scopes) ? data.scopes.join(', ') : 'gmail, drive, calendar';
            const updated = data.updated_at ? `Last updated $${new Date(data.updated_at).toLocaleString()}.` : '';
            setStatus(googleStatus, `Authorised with scopes: $${scopes}. $${updated}`.trim());
          } else {
            setStatus(googleStatus, 'Client saved. Click “Sign in with Google” to authorise access.');
          }
        } catch (error) {
          setStatus(googleStatus, error.message, true);
        }
      };

      const loadSlackStatus = async () => {
        if (!slackStatus || !slackForm) return;
        try {
          const response = await fetch('/api/auth/slack');
          if (!response.ok) {
            throw new Error('Unable to load Slack status');
          }
          const data = await response.json();
          slackWorkspace.value = data.workspace || '';
          slackToken.value = '';
          if (data.has_token) {
            const updated = data.updated_at ? `Saved $${new Date(data.updated_at).toLocaleString()}.` : '';
            const workspace = data.workspace ? `for $${data.workspace}` : '';
            setStatus(slackStatus, `Slack credentials $${workspace} stored. $${updated}`.trim());
          } else {
            setStatus(slackStatus, 'Add a Slack user token and click “Save Slack Credentials”.', true);
          }
        } catch (error) {
          setStatus(slackStatus, error.message, true);
        }
      };

      const submit = async (event) => {
        event.preventDefault();
        const formData = new FormData(form);
        const payload = Object.fromEntries(formData.entries());

        payload.poll_gmail_drive_calendar_seconds = Number(payload.poll_gmail_drive_calendar_seconds || '0');
        payload.poll_slack_active_seconds = Number(payload.poll_slack_active_seconds || '0');
        payload.poll_slack_idle_seconds = Number(payload.poll_slack_idle_seconds || '0');
        payload.gmail_fallback_days = Number(payload.gmail_fallback_days || '0');
        payload.gmail_backfill_days = Number(payload.gmail_backfill_days || '0');
        payload.drive_backfill_days = Number(payload.drive_backfill_days || '0');
        payload.calendar_backfill_days = Number(payload.calendar_backfill_days || '0');
        payload.slack_backfill_days = Number(payload.slack_backfill_days || '0');
        payload.summarization_threshold = Number(payload.summarization_threshold || '0');
        payload.summarization_max_chars = Number(payload.summarization_max_chars || '0');
        payload.summarization_sentence_count = Number(payload.summarization_sentence_count || '0');
        payload.backup_retention_days = Number(payload.backup_retention_days || '0');
        payload.log_retention_days = Number(payload.log_retention_days || '0');

        payload.slack_search_query = (payload.slack_search_query || '').trim();
        payload.calendar_ids = parseList(payload.calendar_ids || '');
        payload.redaction_rules = parseRedaction(payload.redaction_rules || '');

        const submitButton = form.querySelector('button[type="submit"]');
        try {
          if (submitButton) submitButton.disabled = true;
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
          await loadGoogleStatus().catch(() => {});
          await loadLogCategories().catch(() => {});
        } catch (error) {
          setStatus(statusEl, error.message, true);
        } finally {
          if (submitButton) submitButton.disabled = false;
        }
      };

      form.addEventListener('submit', submit);

      const googleAuthorize = async () => {
        if (!googleButton) return;
        googleButton.disabled = true;
        try {
          const response = await fetch('/api/auth/google/start', { method: 'POST' });
          if (!response.ok) {
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.detail || 'Unable to start Google sign-in');
          }
          const data = await response.json();
          if (!data.auth_url) {
            throw new Error('Google did not return an authorisation URL');
          }
          setStatus(googleStatus, 'Complete the Google consent screen in the new window.');
          window.open(data.auth_url, 'google-oauth', 'width=520,height=720');
        } catch (error) {
          setStatus(googleStatus, error.message, true);
        } finally {
          googleButton.disabled = false;
        }
      };

      if (googleButton) {
        googleButton.addEventListener('click', googleAuthorize);
      }

      window.addEventListener('message', (event) => {
        const detail = event.data || {};
        if (detail.type !== 'google-oauth') return;
        const isError = detail.status !== 'success';
        setStatus(googleStatus, detail.message || '', isError);
        loadGoogleStatus().catch(() => {});
      });

      if (slackForm) {
        slackForm.addEventListener('submit', async (event) => {
          event.preventDefault();
          const workspace = (slackWorkspace.value || '').trim();
          const token = (slackToken.value || '').trim();
          if (!token) {
            setStatus(slackStatus, 'Please provide a Slack user token.', true);
            return;
          }
          setStatus(slackStatus, 'Saving Slack credentials...');
          try {
            const response = await fetch('/api/auth/slack', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ workspace, user_token: token }),
            });
            if (!response.ok) {
              const detail = await response.json().catch(() => ({}));
              throw new Error(detail.detail || 'Unable to save Slack credentials');
            }
            slackToken.value = '';
            await loadSlackStatus();
            setStatus(slackStatus, 'Slack credentials saved successfully.');
          } catch (error) {
            setStatus(slackStatus, error.message, true);
          }
        });
      }

      if (slackInventoryButton) {
        slackInventoryButton.addEventListener('click', async () => {
          slackInventoryButton.disabled = true;
          setStatus(slackInventoryStatus, 'Fetching channels...');
          try {
            const response = await fetch('/api/slack/inventory', { method: 'POST' });
            if (!response.ok) {
              const detail = await response.json().catch(() => ({}));
              throw new Error(detail.detail || 'Unable to inventory Slack channels');
            }
            const data = await response.json();
            const channels = Array.isArray(data.channels) ? data.channels : [];
            if (!channels.length) {
              slackChannels.textContent = 'No channels returned by Slack.';
            } else {
              const list = document.createElement('ul');
              list.className = 'channel-list';
              channels.forEach((channel) => {
                const li = document.createElement('li');
                const name = channel.name || channel.id || 'unknown';
                li.textContent = `#$${name}`;
                list.appendChild(li);
              });
              slackChannels.innerHTML = '';
              slackChannels.appendChild(list);
            }
            setStatus(slackInventoryStatus, `Fetched $${channels.length} channels.`);
            await refreshLogs().catch(() => {});
          } catch (error) {
            slackChannels.textContent = '';
            setStatus(slackInventoryStatus, error.message, true);
          } finally {
            slackInventoryButton.disabled = false;
          }
        });
      }

      const runManualLoad = async (service, button) => {
        const input = document.querySelector(`[name="$${service}_manual_days"]`);
        const fallback = Number(input?.dataset.default || '30');
        const parsed = Number(input?.value || fallback);
        if (!Number.isFinite(parsed) || parsed < 1) {
          setStatus(loaderStatus, 'Please provide a valid number of days.', true);
          return;
        }
        const days = Math.floor(parsed);
        setStatus(loaderStatus, `Running $${service} backfill...`);
        if (button) button.disabled = true;
        try {
          const response = await fetch(`/api/manual-load/$${service}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ days }),
          });
          if (!response.ok) {
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.detail || 'Backfill failed');
          }
          const data = await response.json();
          setStatus(loaderStatus, `$${service} backfill completed: $${data.processed} episodes.`);
          await refreshLogs().catch(() => {});
        } catch (error) {
          setStatus(loaderStatus, error.message, true);
        } finally {
          if (button) button.disabled = false;
        }
      };

      document.querySelectorAll('[data-service]').forEach((button) => {
        button.addEventListener('click', () => runManualLoad(button.dataset.service, button));
      });

      const runPoller = async (source, button) => {
        if (!source) return;
        setStatus(pollerStatus, `Running $${source} poller...`);
        if (button) button.disabled = true;
        try {
          const response = await fetch(`/api/pollers/$${source}/run`, { method: 'POST' });
          if (!response.ok) {
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.detail || 'Poller run failed');
          }
          const data = await response.json();
          setStatus(pollerStatus, `$${source} poller processed $${data.processed} items.`);
          await refreshLogs().catch(() => {});
        } catch (error) {
          setStatus(pollerStatus, error.message, true);
        } finally {
          if (button) button.disabled = false;
        }
      };

      document.querySelectorAll('[data-poller]').forEach((button) => {
        button.addEventListener('click', () => runPoller(button.dataset.poller, button));
      });

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
            await refreshLogs().catch(() => {});
          }
        } catch (error) {
          setStatus(backupStatus, error.message, true);
        } finally {
          button.disabled = false;
        }
      };

      const backupButton = document.getElementById('run-backup');
      if (backupButton) {
        backupButton.addEventListener('click', runBackup);
      }

      const loadLogCategories = async () => {
        try {
          const response = await fetch('/api/logs/categories');
          if (!response.ok) {
            throw new Error('Unable to load log categories');
          }
          const data = await response.json();
          const categories = data.categories || ['system', 'episodes'];
          const current = logCategorySelect.value;
          logCategorySelect.innerHTML = '';
          categories.forEach((category) => {
            const option = document.createElement('option');
            option.value = category;
            option.textContent = category;
            if (category === current) option.selected = true;
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
          const response = await fetch(`/api/logs?$${params.toString()}`);
          if (!response.ok) {
            throw new Error('Unable to fetch logs');
          }
          const data = await response.json();
          const entries = Array.isArray(data.records) ? data.records : [];
          logContainer.textContent = entries.length
            ? entries.map((record) => JSON.stringify(record)).join('\n')
            : 'No log entries available.';
          setStatus(logsStatus, `Loaded $${entries.length} log entries.`);
        } catch (error) {
          logContainer.textContent = '';
          setStatus(logsStatus, error.message, true);
        }
      };

      const refreshButton = document.getElementById('refresh-logs');
      if (refreshButton) {
        refreshButton.addEventListener('click', refreshLogs);
      }
      logCategorySelect.addEventListener('change', refreshLogs);

      const bootstrap = async () => {
        activateTab('connections');
        await loadConfig();
        await Promise.allSettled([loadGoogleStatus(), loadSlackStatus()]);
        await loadLogCategories().catch((error) => setStatus(logsStatus, error.message, true));
        await refreshLogs().catch((error) => setStatus(logsStatus, error.message, true));
      };

      bootstrap().catch((error) => setStatus(statusEl, error.message, true));
    })();
  </script>
</body>
</html>""")
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
        slack_search_query=escape(search_query),
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
