"""FastAPI application powering the Personal Assistant admin UI.

This module has been refactored for better maintainability:
- models.py: Pydantic models and data validation
- oauth.py: OAuth session management and token handling  
- templates.py: HTML template rendering
- routes.py: API endpoint handlers (to be created)
- static/admin.js: Frontend JavaScript logic
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency
    import tkinter  # type: ignore
    from tkinter import filedialog  # type: ignore
except Exception:  # pragma: no cover - tkinter not available
    tkinter = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

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
from .models import ConfigPayload, DirectoryRequest, ManualLoadPayload, SlackAuthPayload
from .oauth import (
    GOOGLE_OAUTH_SCOPES,
    generate_pkce_pair,
    load_token_section,
    normalise_scope_list,
    persist_token_section,
    pop_oauth_session,
    register_oauth_session,
)
from .templates import oauth_result_page, render_index_page


def _logs_directory(config: GraphitiConfig, state_store: GraphitiStateStore) -> Path:
    """Determine the logs directory from config or state store default."""
    if config.logs_directory:
        return Path(config.logs_directory).expanduser()
    return state_store.base_dir / "logs"


def create_app(config_path: Path | None = None) -> FastAPI:
    """Create a FastAPI application exposing the admin UI."""
    # Initialize core stores
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
    
    # Mount static files
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def _refresh_log_store(config: GraphitiConfig) -> None:
        """Refresh log store with new configuration."""
        nonlocal log_store
        log_store = GraphitiLogStore(_logs_directory(config, state_store))
        log_store.prune(config.log_retention_days)
        scheduler.update_log_store(log_store)

    async def _run_manual_load(source: str, days: int) -> dict[str, Any]:
        """Execute a manual backfill for the specified source."""
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
                poller = create_calendar_poller(config, state_store, episode_store)
                processed = await asyncio.to_thread(poller.backfill, days)
            elif source == "slack":
                client = create_slack_client(config, state_store)
                poller = SlackPoller(client, episode_store, state_store, config=config)
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
        """Run a poller once for incremental sync."""
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
                poller = create_calendar_poller(config, state_store, episode_store)
                processed = await asyncio.to_thread(poller.run_once)
            elif source == "slack":
                client = create_slack_client(config, state_store)
                poller = SlackPoller(client, episode_store, state_store, config=config)
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

    # Lifecycle events
    @app.on_event("startup")
    async def _startup() -> None:  # pragma: no cover - exercised in integration
        await scheduler.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover - exercised in integration
        await scheduler.stop()

    # Routes
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """Serve the main admin UI page."""
        config = store.load()
        html = render_index_page(ConfigPayload.from_config(config))
        return HTMLResponse(content=html)

    @app.get("/api/config", response_model=ConfigPayload)
    async def get_config() -> ConfigPayload:
        """Get current configuration."""
        config = store.load()
        return ConfigPayload.from_config(config)

    @app.post("/api/config", response_model=ConfigPayload)
    async def update_config(payload: ConfigPayload) -> ConfigPayload:
        """Update configuration."""
        try:
            config = payload.to_config()
        except ValueError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        saved = store.save(config)
        _refresh_log_store(saved)
        return ConfigPayload.from_config(saved)

    @app.get("/api/auth/google/status")
    async def google_status() -> dict[str, Any]:
        """Get Google OAuth status."""
        config = store.load()
        tokens = load_token_section(state_store, "google")
        scopes = tokens.get("scopes")
        return {
            "client_id": config.google_client_id,
            "has_client": bool(config.google_client_id),
            "has_secret": bool(config.google_client_secret),
            "has_refresh_token": bool(tokens.get("refresh_token")),
            "scopes": normalise_scope_list(scopes) or list(GOOGLE_OAUTH_SCOPES),
            "updated_at": tokens.get("updated_at"),
        }

    @app.post("/api/auth/google/start")
    async def google_start(request: Request) -> dict[str, Any]:
        """Initiate Google OAuth flow."""
        config = store.load()
        if not config.google_client_id or not config.google_client_secret:
            raise HTTPException(
                status_code=400,
                detail="Google client ID and secret must be configured before signing in.",
            )

        verifier, challenge = generate_pkce_pair()
        import secrets
        from urllib.parse import urlencode

        state = secrets.token_urlsafe(32)
        redirect_uri = str(request.url_for("google_oauth_callback"))
        register_oauth_session(
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
        return {"auth_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)}

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
        """Handle Google OAuth callback."""
        if error:
            return HTMLResponse(
                content=oauth_result_page(False, f"Google sign-in failed: {error}"),
                status_code=400,
            )
        if not state or not code:
            return HTMLResponse(
                content=oauth_result_page(False, "Missing OAuth response parameters."),
                status_code=400,
            )
        session = pop_oauth_session(state)
        if not session:
            return HTMLResponse(
                content=oauth_result_page(False, "OAuth session has expired. Please try again."),
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
                content=oauth_result_page(False, f"Token exchange failed: {exc}"),
                status_code=502,
            )
        payload = response.json()
        google_tokens = load_token_section(state_store, "google")
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
        scopes = normalise_scope_list(payload.get("scope"))
        if scopes:
            google_tokens["scopes"] = scopes
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expiry = datetime.now(timezone.utc) + timedelta(seconds=float(expires_in))
            google_tokens["access_token_expires_at"] = expiry.isoformat()
        persist_token_section(state_store, "google", google_tokens)
        return HTMLResponse(
            content=oauth_result_page(True, "Google authorisation complete. You can close this window."),
        )

    @app.get("/api/auth/slack")
    async def slack_status() -> dict[str, Any]:
        """Get Slack authentication status."""
        tokens = load_token_section(state_store, "slack")
        return {
            "workspace": tokens.get("workspace", ""),
            "has_token": bool(tokens.get("user_token")),
            "updated_at": tokens.get("updated_at"),
        }

    @app.post("/api/auth/slack")
    async def slack_update(payload: SlackAuthPayload) -> dict[str, Any]:
        """Update Slack credentials."""
        tokens = {
            "workspace": payload.workspace.strip(),
            "user_token": payload.user_token.strip(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if not tokens["user_token"]:
            raise HTTPException(status_code=400, detail="A Slack user token is required.")
        saved = persist_token_section(state_store, "slack", tokens)
        log_store.append(
            "system",
            "Slack credentials updated",
            data={"workspace": saved.get("workspace")},
            retention_days=store.load().log_retention_days,
        )
        return {"workspace": saved.get("workspace", ""), "updated_at": saved.get("updated_at")}

    @app.post("/api/slack/inventory")
    async def slack_inventory() -> dict[str, Any]:
        """Inventory Slack channels."""
        config = store.load()
        client = create_slack_client(config, state_store)
        channels = list(client.list_channels())
        from typing import Mapping

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
        """Open a directory picker dialog."""
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
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"path": selection}

    @app.post("/api/manual-load/{source}")
    async def manual_load(source: str, payload: ManualLoadPayload) -> dict[str, Any]:
        """Run a manual backfill for the specified source."""
        return await _run_manual_load(source, payload.days)

    @app.post("/api/backup/run")
    async def trigger_backup() -> dict[str, Any]:
        """Trigger a manual backup."""
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
        """Get log entries."""
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
        """Get available log categories."""
        categories = set(log_store.categories()) | {"system", "episodes"}
        return {"categories": sorted(categories)}

    @app.post("/api/pollers/{source}/run")
    async def run_poller(source: str) -> dict[str, Any]:
        """Run a poller once."""
        result = await _run_poller_once(source)
        return result

    return app


__all__ = ["create_app"]


