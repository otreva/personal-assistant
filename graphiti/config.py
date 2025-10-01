"""Configuration utilities for Graphiti."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
import os


DEFAULT_DOTENV_PATH = Path(".env")
CONFIG_FILE_NAME = "config.json"
CONFIG_PATH_ENV = "GRAPHITI_CONFIG_PATH"


def _normalise_sequence(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    normalised: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = value.strip()
        if not candidate:
            continue
        if candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        normalised.append(candidate)
    return tuple(normalised)


@dataclass(frozen=True)
class GraphitiConfig:
    """Application configuration persisted to `config.json`."""

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
    redaction_rules_path: str | None = None
    redaction_rules: tuple[tuple[str, str], ...] = ()
    summarization_strategy: str = "heuristic"
    summarization_threshold: int = 4000
    summarization_max_chars: int = 1200
    summarization_sentence_count: int = 5

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
            redaction_rules_path=values.get(
                "REDACTION_RULES_PATH", defaults.redaction_rules_path
            ),
            redaction_rules=_parse_redaction_rules(
                values.get("REDACTION_RULES"), defaults.redaction_rules
            ),
            summarization_strategy=values.get(
                "SUMMARY_STRATEGY", defaults.summarization_strategy
            ),
            summarization_threshold=get_int(
                "SUMMARY_THRESHOLD", defaults.summarization_threshold
            ),
            summarization_max_chars=get_int(
                "SUMMARY_MAX_CHARS", defaults.summarization_max_chars
            ),
            summarization_sentence_count=get_int(
                "SUMMARY_SENTENCE_COUNT", defaults.summarization_sentence_count
            ),
        )

    @classmethod
    def from_json(
        cls, values: Mapping[str, Any], *, defaults: Optional["GraphitiConfig"] = None
    ) -> "GraphitiConfig":
        """Create a configuration instance from structured data."""

        defaults = defaults or cls()

        def get_str(key: str, default: str) -> str:
            value = values.get(key, default)
            if value is None:
                return default
            return str(value)

        def get_int(key: str, default: int) -> int:
            value = values.get(key, default)
            if value is None or value == "":
                return default
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(value)
                except ValueError as exc:  # pragma: no cover - defensive
                    raise ValueError(f"Invalid integer for {key}: {value!r}") from exc
            raise ValueError(f"Invalid integer for {key}: {value!r}")

        def get_seq(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
            value = values.get(key, default)
            if value is None:
                return default
            if isinstance(value, str):
                return _parse_csv(value, default)
            if isinstance(value, Iterable):
                return _normalise_sequence(str(item) for item in value)
            return default

        def get_rules(
            key: str, default: tuple[tuple[str, str], ...]
        ) -> tuple[tuple[str, str], ...]:
            value = values.get(key, default)
            if not value:
                return default
            candidates: list[tuple[str, str]] = []
            if isinstance(value, str):
                return _parse_redaction_rules(value, default)
            if isinstance(value, Iterable):
                for entry in value:
                    if isinstance(entry, Mapping):
                        pattern = entry.get("pattern")
                        replacement = entry.get("replacement", "[REDACTED]")
                        if isinstance(pattern, str) and pattern.strip():
                            candidates.append((pattern.strip(), str(replacement)))
            if candidates:
                return tuple(candidates)
            return default

        return cls(
            neo4j_uri=get_str("neo4j_uri", defaults.neo4j_uri),
            neo4j_user=get_str("neo4j_user", defaults.neo4j_user),
            neo4j_password=get_str("neo4j_password", defaults.neo4j_password),
            group_id=get_str("group_id", defaults.group_id),
            poll_gmail_drive_calendar_seconds=get_int(
                "poll_gmail_drive_calendar_seconds",
                defaults.poll_gmail_drive_calendar_seconds,
            ),
            poll_slack_active_seconds=get_int(
                "poll_slack_active_seconds", defaults.poll_slack_active_seconds
            ),
            poll_slack_idle_seconds=get_int(
                "poll_slack_idle_seconds", defaults.poll_slack_idle_seconds
            ),
            gmail_fallback_days=get_int(
                "gmail_fallback_days", defaults.gmail_fallback_days
            ),
            slack_channel_allowlist=get_seq(
                "slack_channel_allowlist", defaults.slack_channel_allowlist
            ),
            calendar_ids=get_seq("calendar_ids", defaults.calendar_ids),
            redaction_rules_path=values.get(
                "redaction_rules_path", defaults.redaction_rules_path
            ),
            redaction_rules=get_rules("redaction_rules", defaults.redaction_rules),
            summarization_strategy=get_str(
                "summarization_strategy", defaults.summarization_strategy
            ),
            summarization_threshold=get_int(
                "summarization_threshold", defaults.summarization_threshold
            ),
            summarization_max_chars=get_int(
                "summarization_max_chars", defaults.summarization_max_chars
            ),
            summarization_sentence_count=get_int(
                "summarization_sentence_count",
                defaults.summarization_sentence_count,
            ),
        )

    def to_json(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["slack_channel_allowlist"] = list(self.slack_channel_allowlist)
        payload["calendar_ids"] = list(self.calendar_ids)
        payload["redaction_rules"] = [
            {"pattern": pattern, "replacement": replacement}
            for pattern, replacement in self.redaction_rules
        ]
        return payload


def _parse_csv(raw: Optional[str], default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return default
    items = [item.strip() for item in str(raw).split(",") if str(item).strip()]
    if not items:
        return ()
    return tuple(dict.fromkeys(items))


def _parse_redaction_rules(
    raw: Optional[str], default: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...]:
    if raw is None or str(raw).strip() == "":
        return default
    raw_str = str(raw).strip()
    candidates: list[tuple[str, str]] = []
    try:
        import json

        data = json.loads(raw_str)
    except Exception:  # pragma: no cover - defensive JSON parsing fallback
        parts = [segment.strip() for segment in raw_str.split(";;") if segment.strip()]
        for part in parts:
            if "=>" not in part:
                continue
            pattern, replacement = [chunk.strip() for chunk in part.split("=>", 1)]
            if pattern:
                candidates.append((pattern, replacement))
        if not candidates:
            return default
        return tuple(candidates)

    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, Mapping):
                continue
            pattern = entry.get("pattern")
            replacement = entry.get("replacement", "[REDACTED]")
            if isinstance(pattern, str) and pattern:
                candidates.append((pattern, str(replacement)))
    if candidates:
        return tuple(candidates)
    return default


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


class ConfigStore:
    """Persist Graphiti configuration to disk."""

    def __init__(self, path: Optional[Path] = None) -> None:
        env_path = os.environ.get(CONFIG_PATH_ENV)
        if path is None and env_path:
            path = Path(env_path)
        if path is None:
            from .state import STATE_DIR_NAME  # late import to avoid cycle

            base_dir = Path.home() / STATE_DIR_NAME
            path = base_dir / CONFIG_FILE_NAME
        self.path = path

    def load(self) -> GraphitiConfig:
        if not self.path.exists():
            config = GraphitiConfig()
            self.save(config)
            return config

        with self.path.open("r", encoding="utf-8") as fh:
            data = json_load(fh)
        if not isinstance(data, Mapping):
            raise ValueError("Invalid configuration file contents")
        return GraphitiConfig.from_json(data)

    def save(self, config: GraphitiConfig | Mapping[str, Any]) -> GraphitiConfig:
        if isinstance(config, Mapping):
            instance = GraphitiConfig.from_json(config)
        else:
            instance = config
        payload = instance.to_json()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json_dump(payload, fh)
        os.replace(tmp_path, self.path)
        os.chmod(self.path, 0o600)
        return instance


def json_load(handle) -> Any:
    import json

    return json.load(handle)


def json_dump(payload: Mapping[str, Any], handle) -> None:
    import json

    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")


def load_config(
    *,
    dotenv_path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    store: Optional[ConfigStore] = None,
) -> GraphitiConfig:
    """Load configuration from the on-disk store with optional overrides."""

    config_store = store or ConfigStore()
    config = config_store.load()

    overrides: Dict[str, str] = {}
    if dotenv_path is not None:
        overrides.update(_parse_dotenv(dotenv_path))
    elif DEFAULT_DOTENV_PATH.exists():
        overrides.update(_parse_dotenv(DEFAULT_DOTENV_PATH))

    env_mapping = os.environ if environ is None else environ
    overrides.update(
        {k: v for k, v in env_mapping.items() if k.upper() in ENV_KEYS}
    )

    if overrides:
        config = GraphitiConfig.from_mapping(overrides, defaults=config)
    return config


ENV_KEYS = {
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASS",
    "GROUP_ID",
    "POLL_GMAIL_DRIVE_CAL",
    "POLL_SLACK_ACTIVE",
    "POLL_SLACK_IDLE",
    "GMAIL_FALLBACK_DAYS",
    "SLACK_CHANNEL_ALLOWLIST",
    "CALENDAR_IDS",
    "REDACTION_RULES_PATH",
    "REDACTION_RULES",
    "SUMMARY_STRATEGY",
    "SUMMARY_THRESHOLD",
    "SUMMARY_MAX_CHARS",
    "SUMMARY_SENTENCE_COUNT",
}


__all__ = ["GraphitiConfig", "ConfigStore", "load_config"]
