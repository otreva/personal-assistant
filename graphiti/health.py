"""Health endpoint and status dashboard helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping

from .config import GraphitiConfig, load_config
from .state import GraphitiStateStore

SOURCE_ORDER = ("gmail", "drive", "calendar", "slack", "mcp")


def collect_health_metrics(
    state_store: GraphitiStateStore,
    config: GraphitiConfig,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a structured view of source health suitable for JSON output."""

    current = now or datetime.now(timezone.utc)
    state = state_store.load_state()
    sources: dict[str, Any] = {}
    statuses: list[str] = []

    for source in SOURCE_ORDER:
        source_state = state.get(source)
        if not isinstance(source_state, Mapping):
            source_state = {}
        last_run_at_raw = source_state.get("last_run_at")
        last_run_at = _parse_time(last_run_at_raw)
        error_count = _coerce_int(source_state.get("error_count", 0))
        interval = _poll_interval_for_source(source, config)
        next_due = last_run_at + interval if last_run_at and interval else None
        lag_seconds = (
            (current - last_run_at).total_seconds()
            if last_run_at
            else None
        )
        status = "ok"
        if error_count:
            status = "error"
        elif interval and lag_seconds is not None:
            if lag_seconds > interval.total_seconds() * 2:
                status = "stale"
        elif last_run_at is None:
            status = "pending"
        statuses.append(status)
        sources[source] = {
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
            "next_run_due": next_due.isoformat() if next_due else None,
            "lag_seconds": lag_seconds,
            "error_count": error_count,
            "status": status,
            "poll_interval_seconds": interval.total_seconds() if interval else None,
        }

    overall = "ok"
    if any(status == "error" for status in statuses):
        overall = "error"
    elif any(status == "stale" for status in statuses):
        overall = "stale"
    elif all(status == "pending" for status in statuses):
        overall = "pending"

    return {
        "generated_at": current.isoformat(),
        "status": overall,
        "sources": sources,
    }


def format_dashboard(metrics: Mapping[str, Any]) -> str:
    """Render a textual dashboard summarising sync health."""

    generated = metrics.get("generated_at")
    header = "Personal Assistant Sync Status"
    if isinstance(generated, str):
        header += f" — {generated}"
    lines = [header, ""]
    lines.append(
        f"{'Source':<10} {'Last Run (UTC)':<22} {'Status':<8} {'Errors':<8} {'Next Due':<22}"
    )
    lines.append("-" * 76)
    sources = metrics.get("sources")
    if not isinstance(sources, Mapping):
        sources = {}
    for source in SOURCE_ORDER:
        info = sources.get(source, {}) if isinstance(sources, Mapping) else {}
        if not isinstance(info, Mapping):
            info = {}
        last_run = _format_timestamp(info.get("last_run_at"))
        next_due = _format_timestamp(info.get("next_run_due"))
        status = str(info.get("status", "unknown")).upper()
        errors = str(info.get("error_count", 0))
        lines.append(
            f"{source:<10} {last_run:<22} {status:<8} {errors:<8} {next_due:<22}"
        )
    lines.append("")
    lines.append(f"Overall status: {metrics.get('status', 'unknown').upper()}")
    return "\n".join(lines)


class HealthApp:
    """Minimal WSGI-compatible health endpoint."""

    def __init__(
        self,
        *,
        config: GraphitiConfig | None = None,
        state_store: GraphitiStateStore | None = None,
    ) -> None:
        self._config = config or load_config()
        self._state = state_store or GraphitiStateStore()

    def __call__(self, environ, start_response):  # pragma: no cover - exercised in tests
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "")
        if path.rstrip("/") == "/health" and method in {"GET", "HEAD"}:
            payload = collect_health_metrics(self._state, self._config)
            body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            length = len(body) if method == "GET" else 0
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(length)),
                ],
            )
            if method == "HEAD":
                return [b""]
            return [body]

        start_response(
            "404 Not Found",
            [("Content-Type", "application/json"), ("Content-Length", "0")],
        )
        return [b""]


def create_health_app(
    *,
    config: GraphitiConfig | None = None,
    state_store: GraphitiStateStore | None = None,
):
    """Return a FastAPI app when available, otherwise fall back to :class:`HealthApp`."""

    try:  # pragma: no cover - optional dependency
        from fastapi import FastAPI

        app = FastAPI()

        resolved_config = config or load_config()
        resolved_state = state_store or GraphitiStateStore()

        @app.get("/health")
        def _health_endpoint():
            return collect_health_metrics(resolved_state, resolved_config)

        return app
    except Exception:
        return HealthApp(config=config, state_store=state_store)


def _poll_interval_for_source(
    source: str, config: GraphitiConfig
) -> timedelta | None:
    if source in {"gmail", "drive", "calendar"}:
        return timedelta(seconds=max(config.poll_gmail_drive_calendar_seconds, 1))
    if source == "slack":
        return timedelta(seconds=max(config.poll_slack_active_seconds, 1))
    return None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value:
        try:
            cleaned = value
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            return datetime.fromisoformat(cleaned).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _format_timestamp(value: Any) -> str:
    dt = _parse_time(value)
    if dt is None:
        return "never"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "collect_health_metrics",
    "create_health_app",
    "format_dashboard",
    "HealthApp",
]

