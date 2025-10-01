"""Slack poller implementation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping, MutableMapping, Protocol

from ..config import GraphitiConfig, load_config
from ..episodes import Episode, Neo4jEpisodeStore
from ..state import GraphitiStateStore


class SlackRateLimited(Exception):
    """Raised when Slack responds with a rate limit error."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Slack API rate limited")
        self.retry_after = max(retry_after or 1.0, 0.1)


class SlackClient(Protocol):  # pragma: no cover - protocol definition
    def list_channels(self) -> Iterable[Mapping[str, object]]: ...

    def fetch_channel_history(
        self, channel_id: str, oldest_ts: str | None
    ) -> Iterable[Mapping[str, object]]: ...

    def fetch_thread_replies(
        self, channel_id: str, thread_ts: str, oldest_ts: str | None
    ) -> Iterable[Mapping[str, object]]: ...


@dataclass(slots=True)
class SlackPoller:
    """Poll Slack conversations, capturing threads and messages."""

    client: SlackClient
    episode_store: Neo4jEpisodeStore
    state_store: GraphitiStateStore
    allowlist: Iterable[str] | None = None
    config: GraphitiConfig | None = None
    max_retries: int = 3
    _config: GraphitiConfig = field(init=False)
    _group_id: str = field(init=False)
    _allowlist: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self._config = self.config or load_config()
        if self.episode_store.group_id != self._config.group_id:
            raise ValueError("Episode store group_id does not match configuration group_id")
        self._group_id = self._config.group_id
        allow = [item.lower() for item in self.allowlist or self._config.slack_channel_allowlist]
        self._allowlist = set(allow)

    def run_once(self) -> int:
        state = self.state_store.load_state()
        slack_state = state.get("slack") if isinstance(state, Mapping) else {}
        if not isinstance(slack_state, Mapping):
            slack_state = {}

        channels_state = slack_state.get("channels") if isinstance(slack_state, Mapping) else {}
        if not isinstance(channels_state, Mapping):
            channels_state = {}

        channels = self._resolve_channels(channels_state)
        if not channels:
            channels = self._inventory_channels()

        channel_checkpoints = slack_state.get("checkpoints") if isinstance(slack_state, Mapping) else {}
        if not isinstance(channel_checkpoints, Mapping):
            channel_checkpoints = {}
        thread_checkpoints = slack_state.get("threads") if isinstance(slack_state, Mapping) else {}
        if not isinstance(thread_checkpoints, Mapping):
            thread_checkpoints = {}

        processed = 0
        updated_channels: dict[str, Mapping[str, object]] = {}
        updated_checkpoints: dict[str, str] = {}
        updated_threads: dict[str, MutableMapping[str, str]] = {
            channel_id: dict(checks) if isinstance(checks, Mapping) else {}
            for channel_id, checks in thread_checkpoints.items()
            if isinstance(channel_id, str)
        }

        for channel_id, metadata in channels.items():
            updated_channels[channel_id] = metadata
            oldest = str(channel_checkpoints.get(channel_id)) if channel_checkpoints.get(channel_id) else None
            messages = self._call_with_backoff(
                self.client.fetch_channel_history, channel_id, oldest
            )
            channel_max_ts = oldest
            for message in messages:
                episode = self._normalize_message(channel_id, metadata, message)
                if episode is None:
                    continue
                self.episode_store.upsert_episode(episode)
                processed += 1
                channel_max_ts = self._max_ts(channel_max_ts, episode.version)
                thread_ts = self._thread_ts(message)
                if thread_ts and thread_ts != episode.version:
                    processed += self._process_thread(
                        channel_id,
                        metadata,
                        thread_ts,
                        updated_threads,
                    )
            if channel_max_ts:
                updated_checkpoints[channel_id] = channel_max_ts

        payload = {
            "slack": {
                "channels": updated_channels,
                "checkpoints": updated_checkpoints,
                "threads": updated_threads,
                "last_run_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        self.state_store.update_state(payload)
        return processed

    def _inventory_channels(self) -> dict[str, Mapping[str, object]]:
        channels = self.client.list_channels()
        filtered = {}
        for channel in channels:
            if not isinstance(channel, Mapping):
                continue
            channel_id = str(channel.get("id"))
            name = str(channel.get("name", ""))
            if not channel_id:
                continue
            if self._allowlist and not self._channel_allowed(channel_id, name):
                continue
            filtered[channel_id] = dict(channel)
        return filtered

    def _resolve_channels(self, stored: Mapping[str, Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
        channels: dict[str, Mapping[str, object]] = {}
        for channel_id, metadata in stored.items():
            if not isinstance(metadata, Mapping):
                continue
            name = str(metadata.get("name", ""))
            if self._allowlist and not self._channel_allowed(channel_id, name):
                continue
            channels[str(channel_id)] = dict(metadata)
        return channels

    def _channel_allowed(self, channel_id: str, name: str) -> bool:
        if not self._allowlist:
            return True
        return channel_id.lower() in self._allowlist or name.lower() in self._allowlist

    def _normalize_message(
        self,
        channel_id: str,
        metadata: Mapping[str, object],
        message: Mapping[str, object],
    ) -> Episode | None:
        if not isinstance(message, Mapping):
            return None
        if message.get("type") not in {None, "message"}:
            return None
        subtype = message.get("subtype")
        if isinstance(subtype, str) and subtype:
            return None
        ts = message.get("ts")
        if not isinstance(ts, str) or not ts:
            return None
        user = message.get("user")
        if not isinstance(user, str):
            user = None
        text = message.get("text")
        if text is not None and not isinstance(text, str):
            text = str(text)
        valid_at = self._parse_ts(ts)
        native_id = f"{channel_id}:{ts}"
        json_payload = dict(message)
        metadata_payload = {
            "channel_id": channel_id,
            "channel_name": metadata.get("name"),
            "user": user,
            "thread_ts": message.get("thread_ts"),
            "tombstone": False,
        }
        return Episode(
            group_id=self._group_id,
            source="slack",
            native_id=native_id,
            version=ts,
            valid_at=valid_at,
            text=text,
            json=json_payload,
            metadata=metadata_payload,
        )

    def _process_thread(
        self,
        channel_id: str,
        metadata: Mapping[str, object],
        thread_ts: str,
        thread_checkpoints: MutableMapping[str, MutableMapping[str, str]],
    ) -> int:
        channel_threads = thread_checkpoints.setdefault(channel_id, {})
        oldest = channel_threads.get(thread_ts)
        replies = self._call_with_backoff(
            self.client.fetch_thread_replies, channel_id, thread_ts, oldest
        )
        processed = 0
        thread_max_ts = oldest
        for reply in replies:
            if not isinstance(reply, Mapping):
                continue
            ts = reply.get("ts")
            if not isinstance(ts, str) or ts == thread_ts:
                continue
            episode = self._normalize_message(channel_id, metadata, reply)
            if episode is None:
                continue
            self.episode_store.upsert_episode(episode)
            processed += 1
            thread_max_ts = self._max_ts(thread_max_ts, episode.version)
        if thread_max_ts:
            channel_threads[thread_ts] = thread_max_ts
        return processed

    def _call_with_backoff(self, func, *args):
        delay = 1.0
        for attempt in range(self.max_retries):
            try:
                result = func(*args)
                return list(result)
            except SlackRateLimited as exc:
                sleep_for = max(delay, exc.retry_after)
                time.sleep(sleep_for)
                delay = min(sleep_for * 2, 60.0)
        result = func(*args)
        return list(result)

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


@dataclass(slots=True)
class NullSlackClient:
    """Default Slack client that performs no operations."""

    channels: tuple[Mapping[str, object], ...] = field(default_factory=tuple)

    def list_channels(self) -> Iterable[Mapping[str, object]]:
        return list(self.channels)

    def fetch_channel_history(
        self, channel_id: str, oldest_ts: str | None
    ) -> Iterable[Mapping[str, object]]:
        return []

    def fetch_thread_replies(
        self, channel_id: str, thread_ts: str, oldest_ts: str | None
    ) -> Iterable[Mapping[str, object]]:
        return []


__all__ = [
    "SlackPoller",
    "SlackClient",
    "SlackRateLimited",
    "NullSlackClient",
]

