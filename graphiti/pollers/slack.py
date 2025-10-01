"""Slack poller implementation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, MutableMapping, Protocol

from ..config import GraphitiConfig, load_config
from ..episodes import Episode, Neo4jEpisodeStore
from ..hooks import EpisodeProcessor
from ..state import GraphitiStateStore


class SlackRateLimited(Exception):
    """Raised when Slack responds with a rate limit error."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Slack API rate limited")
        self.retry_after = max(retry_after or 1.0, 0.1)


class SlackClient(Protocol):  # pragma: no cover - protocol definition
    def list_channels(self) -> Iterable[Mapping[str, object]]: ...

    def search_messages(
        self,
        query: str,
        *,
        oldest: str | None = None,
        cursor: str | None = None,
    ) -> Mapping[str, object]: ...

    def fetch_message(self, channel_id: str, ts: str) -> Mapping[str, object]: ...

    def resolve_user(self, user_id: str) -> Mapping[str, object] | None: ...

    def resolve_channel(self, channel_id: str) -> Mapping[str, object] | None: ...


@dataclass(slots=True)
class SlackPoller:
    """Poll Slack conversations using the search API."""

    client: SlackClient
    episode_store: Neo4jEpisodeStore
    state_store: GraphitiStateStore
    config: GraphitiConfig | None = None
    max_retries: int = 3
    _config: GraphitiConfig = field(init=False)
    _group_id: str = field(init=False)
    _processor: EpisodeProcessor = field(init=False)
    _query: str = field(init=False)

    def __post_init__(self) -> None:
        self._config = self.config or load_config()
        if self.episode_store.group_id != self._config.group_id:
            raise ValueError("Episode store group_id does not match configuration group_id")
        self._group_id = self._config.group_id
        self._processor = EpisodeProcessor(self._config)
        query = (self._config.slack_search_query or "*").strip()
        self._query = query or "*"

    def run_once(self) -> int:
        state = self.state_store.load_state()
        slack_state = state.get("slack") if isinstance(state.get("slack"), Mapping) else {}
        if not isinstance(slack_state, Mapping):
            slack_state = {}
        search_state = (
            slack_state.get("search") if isinstance(slack_state.get("search"), Mapping) else {}
        )
        stored_query = (
            str(search_state.get("query", "")).strip() if isinstance(search_state, Mapping) else ""
        )
        last_seen = search_state.get("last_seen_ts") if isinstance(search_state, Mapping) else None
        if not isinstance(last_seen, str):
            last_seen = None
        if stored_query != self._query:
            last_seen = None

        user_cache = self._load_user_cache(slack_state.get("users"))
        channel_cache = self._load_channel_cache(slack_state.get("channels"))

        processed, newest_ts = self._process_search_results(
            oldest=last_seen,
            cutoff=None,
            user_cache=user_cache,
            channel_cache=channel_cache,
            skip_until=last_seen,
        )

        payload = self._build_state_payload(
            newest_ts,
            user_cache,
            channel_cache,
            extra={"last_run_at": datetime.now(timezone.utc).isoformat()},
        )
        self.state_store.update_state({"slack": payload})
        return processed

    def backfill(self, newer_than_days: int | None = None) -> int:
        """Load historical Slack messages within the requested window."""

        days = max(int(newer_than_days or self._config.slack_backfill_days), 1)
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        oldest_ts = f"{cutoff_dt.timestamp():.6f}"

        state = self.state_store.load_state()
        slack_state = state.get("slack") if isinstance(state.get("slack"), Mapping) else {}
        if not isinstance(slack_state, Mapping):
            slack_state = {}
        search_state = (
            slack_state.get("search") if isinstance(slack_state.get("search"), Mapping) else {}
        )
        last_seen = search_state.get("last_seen_ts") if isinstance(search_state, Mapping) else None
        if not isinstance(last_seen, str):
            last_seen = None

        user_cache = self._load_user_cache(slack_state.get("users"))
        channel_cache = self._load_channel_cache(slack_state.get("channels"))

        processed, newest_ts = self._process_search_results(
            oldest=oldest_ts,
            cutoff=cutoff_dt,
            user_cache=user_cache,
            channel_cache=channel_cache,
            skip_until=None,
        )

        combined_last_seen = self._max_ts(last_seen, newest_ts) if newest_ts else last_seen
        payload = self._build_state_payload(
            combined_last_seen,
            user_cache,
            channel_cache,
            extra={
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "backfilled_days": days,
                "backfill_ran_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.state_store.update_state({"slack": payload})
        return processed

    def _process_search_results(
        self,
        *,
        oldest: str | None,
        cutoff: datetime | None,
        user_cache: MutableMapping[str, dict[str, str]],
        channel_cache: MutableMapping[str, dict[str, str]],
        skip_until: str | None,
    ) -> tuple[int, str | None]:
        processed = 0
        newest_ts = skip_until
        for payload in self._search_messages(oldest):
            episode = self._normalise_message(payload, user_cache, channel_cache)
            if episode is None:
                continue
            if skip_until and not self._is_newer(episode.version, skip_until):
                continue
            if cutoff and episode.valid_at and episode.valid_at < cutoff:
                continue
            self.episode_store.upsert_episode(self._processor.process(episode))
            processed += 1
            newest_ts = self._max_ts(newest_ts, episode.version)
        return processed, newest_ts

    def _search_messages(self, oldest: str | None) -> Iterable[Mapping[str, object]]:
        cursor: str | None = None
        while True:
            page = self._call_with_backoff(
                self.client.search_messages,
                self._query,
                oldest=oldest,
                cursor=cursor,
            )
            if not isinstance(page, Mapping):
                break
            messages = page.get("messages")
            if not isinstance(messages, Iterable):
                messages = []
            for message in messages:
                if isinstance(message, Mapping):
                    yield message
            cursor_value = page.get("next_cursor")
            cursor = str(cursor_value) if isinstance(cursor_value, str) and cursor_value else None
            if not cursor:
                break

    def _normalise_message(
        self,
        payload: Mapping[str, object],
        user_cache: MutableMapping[str, dict[str, str]],
        channel_cache: MutableMapping[str, dict[str, str]],
    ) -> Episode | None:
        ts = payload.get("ts")
        if not isinstance(ts, str) or not ts.strip():
            return None
        ts = ts.strip()
        channel_id = self._channel_id(payload)
        if not channel_id:
            return None

        full_payload = dict(payload)
        if bool(payload.get("is_truncated")):
            extra = self._call_with_backoff(self.client.fetch_message, channel_id, ts)
            if isinstance(extra, Mapping):
                full_payload.update(extra)

        user_id = self._user_id(full_payload)
        user_info = self._resolve_user(user_id, user_cache) if user_id else None
        channel_info = self._resolve_channel(channel_id, channel_cache, full_payload.get("channel"))

        text = full_payload.get("text")
        if text is not None and not isinstance(text, str):
            text = str(text)
        if isinstance(text, str):
            stripped = text.strip()
            text = text if stripped else stripped

        metadata: dict[str, Any] = {
            "channel_id": channel_id,
            "channel_name": channel_info.get("name") if channel_info else None,
            "user_id": user_id,
            "user_name": user_info.get("name") if user_info else None,
            "user_email": user_info.get("email") if user_info else None,
            "thread_ts": self._thread_ts(full_payload),
            "permalink": full_payload.get("permalink"),
        }
        metadata = {key: value for key, value in metadata.items() if value}

        return Episode(
            group_id=self._group_id,
            source="slack",
            native_id=f"{channel_id}:{ts}",
            version=ts,
            valid_at=self._parse_ts(ts),
            text=text,
            json=full_payload,
            metadata=metadata,
        )

    def _resolve_user(
        self,
        user_id: str,
        cache: MutableMapping[str, dict[str, str]],
    ) -> dict[str, str]:
        if user_id in cache:
            return cache[user_id]
        response = self._call_with_backoff(self.client.resolve_user, user_id)
        record: dict[str, str] = {"id": user_id}
        if isinstance(response, Mapping):
            name = self._extract_name(response)
            if name:
                record["name"] = name
            email = response.get("email")
            if isinstance(email, str) and email.strip():
                record["email"] = email.strip()
        cache[user_id] = record
        return record

    def _resolve_channel(
        self,
        channel_id: str,
        cache: MutableMapping[str, dict[str, str]],
        initial: object,
    ) -> dict[str, str]:
        if channel_id in cache:
            return cache[channel_id]
        record: dict[str, str] = {"id": channel_id}
        if isinstance(initial, Mapping):
            name = initial.get("name")
            if isinstance(name, str) and name.strip():
                record["name"] = name.strip()
        response = self._call_with_backoff(self.client.resolve_channel, channel_id)
        if isinstance(response, Mapping):
            name = response.get("name")
            if isinstance(name, str) and name.strip():
                record["name"] = name.strip()
        cache[channel_id] = record
        return record

    def _call_with_backoff(self, func, *args, **kwargs):
        delay = 1.0
        for _ in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except SlackRateLimited as exc:
                sleep_for = max(delay, exc.retry_after)
                time.sleep(sleep_for)
                delay = min(sleep_for * 2, 60.0)
        return func(*args, **kwargs)

    @staticmethod
    def _parse_ts(ts: str) -> datetime:
        try:
            seconds = float(ts)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            seconds = 0.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    @staticmethod
    def _thread_ts(message: Mapping[str, object]) -> str | None:
        value = message.get("thread_ts")
        return str(value) if isinstance(value, str) and value else None

    @staticmethod
    def _max_ts(current: str | None, candidate: str) -> str:
        if current is None:
            return candidate
        try:
            current_f = float(current)
            candidate_f = float(candidate)
            return candidate if candidate_f > current_f else current
        except ValueError:  # pragma: no cover - defensive
            return candidate

    @staticmethod
    def _is_newer(candidate: str, reference: str) -> bool:
        try:
            return float(candidate) > float(reference)
        except ValueError:
            return candidate > reference

    @staticmethod
    def _channel_id(payload: Mapping[str, object]) -> str | None:
        channel = payload.get("channel")
        if isinstance(channel, Mapping):
            candidate = channel.get("id") or channel.get("channel")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        fallback = payload.get("channel_id") or payload.get("channel")
        if isinstance(fallback, str) and fallback.strip():
            return fallback.strip()
        return None

    @staticmethod
    def _user_id(payload: Mapping[str, object]) -> str | None:
        candidate = payload.get("user") or payload.get("user_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return None

    @staticmethod
    def _extract_name(payload: Mapping[str, object]) -> str | None:
        for key in ("name", "real_name", "display_name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _load_user_cache(value: object) -> dict[str, dict[str, str]]:
        cache: dict[str, dict[str, str]] = {}
        if not isinstance(value, Mapping):
            return cache
        for key, entry in value.items():
            if not isinstance(entry, Mapping):
                continue
            record: dict[str, str] = {"id": str(entry.get("id", key))}
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                record["name"] = name.strip()
            email = entry.get("email")
            if isinstance(email, str) and email.strip():
                record["email"] = email.strip()
            cache[str(key)] = record
        return cache

    @staticmethod
    def _load_channel_cache(value: object) -> dict[str, dict[str, str]]:
        cache: dict[str, dict[str, str]] = {}
        if not isinstance(value, Mapping):
            return cache
        for key, entry in value.items():
            metadata: Mapping[str, object] | None = None
            if isinstance(entry, Mapping):
                if isinstance(entry.get("metadata"), Mapping):
                    metadata = entry.get("metadata")  # type: ignore[assignment]
                else:
                    metadata = entry
            if metadata is None:
                continue
            record: dict[str, str] = {"id": str(metadata.get("id", key))}
            name = metadata.get("name")
            if isinstance(name, str) and name.strip():
                record["name"] = name.strip()
            cache[str(key)] = record
        return cache

    def _build_state_payload(
        self,
        newest_ts: str | None,
        user_cache: Mapping[str, dict[str, str]],
        channel_cache: Mapping[str, dict[str, str]],
        *,
        extra: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "search": {"query": self._query},
            "users": {key: dict(value) for key, value in user_cache.items()},
            "channels": {key: dict(value) for key, value in channel_cache.items()},
            "checkpoints": None,
            "threads": None,
        }
        if newest_ts:
            payload["search"]["last_seen_ts"] = newest_ts
        if extra:
            payload.update(extra)
        return payload


@dataclass(slots=True)
class NullSlackClient:
    """Default Slack client that performs no operations."""

    channels: tuple[Mapping[str, object], ...] = field(default_factory=tuple)

    def list_channels(self) -> Iterable[Mapping[str, object]]:
        return list(self.channels)

    def search_messages(
        self,
        query: str,
        *,
        oldest: str | None = None,
        cursor: str | None = None,
    ) -> Mapping[str, object]:
        return {"messages": [], "next_cursor": None}

    def fetch_message(self, channel_id: str, ts: str) -> Mapping[str, object]:
        return {}

    def resolve_user(self, user_id: str) -> Mapping[str, object] | None:
        return None

    def resolve_channel(self, channel_id: str) -> Mapping[str, object] | None:
        return None


__all__ = [
    "SlackPoller",
    "SlackClient",
    "SlackRateLimited",
    "NullSlackClient",
]
