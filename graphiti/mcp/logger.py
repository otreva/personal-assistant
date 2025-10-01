"""MCP episode logging utilities."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, Mapping, MutableMapping

from ..config import GraphitiConfig, load_config
from ..episodes import Episode, Neo4jEpisodeStore


@dataclass(slots=True)
class McpTurn:
    """Representation of a single MCP conversation turn."""

    message_id: str
    conversation_id: str
    role: str
    content: str | None
    timestamp: datetime
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_episode(self, group_id: str) -> Episode:
        metadata = dict(self.metadata)
        metadata.update({
            "conversation_id": self.conversation_id,
            "role": self.role,
        })
        json_payload: MutableMapping[str, object] = {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.content is not None:
            json_payload["content"] = self.content
        if self.metadata:
            json_payload["metadata"] = dict(self.metadata)
        return Episode(
            group_id=group_id,
            source="mcp",
            native_id=self.message_id,
            version=self.timestamp.isoformat(),
            valid_at=self.timestamp.astimezone(timezone.utc),
            text=self.content,
            json=json_payload,
            metadata=metadata,
        )


@dataclass
class McpEpisodeLogger:
    """Asynchronous-friendly logger that batches MCP turns."""

    episode_store: Neo4jEpisodeStore
    config: GraphitiConfig | None = None
    queue_limit: int = 1000

    def __post_init__(self) -> None:
        self._config = self.config or load_config()
        if self.episode_store.group_id != self._config.group_id:
            raise ValueError("Episode store group_id does not match configuration group_id")
        self._queue: Deque[McpTurn] = deque()
        self._lock = Lock()

    def log_turn(self, turn: McpTurn) -> None:
        """Queue a turn for persistence."""

        with self._lock:
            if len(self._queue) >= self.queue_limit:
                self._queue.popleft()
            self._queue.append(turn)

    def drain(self) -> list[McpTurn]:
        """Drain the queue and return the collected turns."""

        with self._lock:
            items = list(self._queue)
            self._queue.clear()
        return items

    def flush(self) -> int:
        """Persist queued turns to the episode store."""

        turns = self.drain()
        if not turns:
            return 0
        processed = 0
        failures: list[tuple[McpTurn, Exception]] = []
        for turn in turns:
            episode = turn.to_episode(self._config.group_id)
            try:
                self.episode_store.upsert_episode(episode)
                processed += 1
            except Exception as exc:  # pragma: no cover - defensive
                failures.append((turn, exc))
        if failures:
            with self._lock:
                for turn, _ in reversed(failures):
                    if len(self._queue) < self.queue_limit:
                        self._queue.appendleft(turn)
            first_error = failures[0][1]
            raise RuntimeError("Failed to persist MCP turns") from first_error
        return processed

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)


__all__ = ["McpTurn", "McpEpisodeLogger"]

