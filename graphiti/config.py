"""Configuration utilities for Graphiti."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional
import os


DEFAULT_DOTENV_PATH = Path(".env")


@dataclass(frozen=True)
class GraphitiConfig:
    """Application configuration loaded from environment variables or .env."""

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    group_id: str = "mike_assistant"
    poll_gmail_drive_calendar_seconds: int = 3600
    poll_slack_active_seconds: int = 30
    poll_slack_idle_seconds: int = 3600
    gmail_fallback_days: int = 7
    slack_channel_allowlist: tuple[str, ...] = ()
    calendar_ids: tuple[str, ...] = ("primary",)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
        *,
        defaults: Optional["GraphitiConfig"] = None,
    ) -> "GraphitiConfig":
        """Create a configuration instance from a key/value mapping."""

        defaults = defaults or cls()
        def get_int(key: str, default: int) -> int:
            raw = values.get(key)
            if raw is None or raw.strip() == "":
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"Invalid integer for {key}: {raw!r}") from exc

        return cls(
            neo4j_uri=values.get("NEO4J_URI", defaults.neo4j_uri),
            neo4j_user=values.get("NEO4J_USER", defaults.neo4j_user),
            neo4j_password=values.get("NEO4J_PASS", defaults.neo4j_password),
            group_id=values.get("GROUP_ID", defaults.group_id),
            poll_gmail_drive_calendar_seconds=get_int(
                "POLL_GMAIL_DRIVE_CAL", defaults.poll_gmail_drive_calendar_seconds
            ),
            poll_slack_active_seconds=get_int(
                "POLL_SLACK_ACTIVE", defaults.poll_slack_active_seconds
            ),
            poll_slack_idle_seconds=get_int(
                "POLL_SLACK_IDLE", defaults.poll_slack_idle_seconds
            ),
            gmail_fallback_days=get_int(
                "GMAIL_FALLBACK_DAYS", defaults.gmail_fallback_days
            ),
            slack_channel_allowlist=_parse_csv(
                values.get("SLACK_CHANNEL_ALLOWLIST"), defaults.slack_channel_allowlist
            ),
            calendar_ids=_parse_csv(
                values.get("CALENDAR_IDS"), defaults.calendar_ids
            ),
        )


def _parse_csv(raw: Optional[str], default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return default
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if not items:
        return ()
    return tuple(dict.fromkeys(items))


def _parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse a minimal .env file into a dictionary."""

    data: Dict[str, str] = {}
    if not path.exists():
        return data

    for line in path.read_text().splitlines():
        striped = line.strip()
        if not striped or striped.startswith("#"):
            continue
        if "=" not in striped:
            continue
        key, value = striped.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def load_config(*, dotenv_path: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> GraphitiConfig:
    """Load configuration merging `.env` values with environment variables."""

    dotenv_path = dotenv_path or DEFAULT_DOTENV_PATH
    environ = dict(os.environ if environ is None else environ)

    values: Dict[str, str] = {}
    values.update(_parse_dotenv(dotenv_path))
    # Environment variables take precedence over .env
    values.update(environ)
    return GraphitiConfig.from_mapping(values)


__all__ = ["GraphitiConfig", "load_config"]
