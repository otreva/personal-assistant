"""Pydantic models for the web admin API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, validator


class RedactionRule(BaseModel):
    """A pattern-based redaction rule."""

    pattern: str = Field(..., min_length=1)
    replacement: str = Field("[REDACTED]", min_length=1)

    class Config:
        frozen = True


class ConfigPayload(BaseModel):
    """Configuration payload for API requests."""

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
    slack_search_queries: list[str] = Field(default_factory=list)
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

    @validator("calendar_ids", "slack_search_queries", pre=True)
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

    def to_config(self):
        """Convert to GraphitiConfig."""
        from ..config import GraphitiConfig

        return GraphitiConfig.from_json(self.model_dump())

    @classmethod
    def from_config(cls, config) -> "ConfigPayload":
        """Create from GraphitiConfig."""
        return cls(**config.to_json())


class ManualLoadPayload(BaseModel):
    """Payload for manual historical load requests."""

    days: int = Field(..., ge=1)


class SlackAuthPayload(BaseModel):
    """Payload for Slack authentication."""

    workspace: str = Field("", min_length=0)
    slack_token: str = Field("", min_length=0)
    slack_cookie: str = Field("", min_length=0)


class DirectoryRequest(BaseModel):
    """Request for directory picker dialog."""

    title: str | None = None
    initial: str | None = None


__all__ = [
    "RedactionRule",
    "ConfigPayload",
    "ManualLoadPayload",
    "SlackAuthPayload",
    "DirectoryRequest",
]


