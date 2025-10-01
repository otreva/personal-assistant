"""Gmail poller implementation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Protocol

from ..config import GraphitiConfig, load_config
from ..episodes import Episode, Neo4jEpisodeStore
from ..hooks import EpisodeProcessor
from ..state import GraphitiStateStore
from ..utils import sleep_with_jitter


class GmailHistoryNotFound(Exception):
    """Raised when the Gmail history API indicates the history ID is invalid."""


@dataclass(slots=True)
class GmailHistoryResult:
    message_ids: list[str]
    latest_history_id: str


class GmailClient(Protocol):  # pragma: no cover - protocol definition
    def list_history(self, start_history_id: str | None) -> GmailHistoryResult: ...

    def fallback_fetch(self, newer_than_days: int) -> GmailHistoryResult: ...

    def fetch_message(self, message_id: str) -> Mapping[str, object]: ...


class GmailPoller:
    """Incremental Gmail poller with fallback behavior."""

    def __init__(
        self,
        gmail_client: GmailClient,
        episode_store: Neo4jEpisodeStore,
        state_store: GraphitiStateStore,
        config: GraphitiConfig | None = None,
    ) -> None:
        self._gmail = gmail_client
        self._episodes = episode_store
        self._state = state_store
        self._config = config or load_config()
        if self._episodes.group_id != self._config.group_id:
            raise ValueError(
                "Episode store group_id does not match configuration group_id"
            )
        self._group_id = self._config.group_id
        self._processor = EpisodeProcessor(self._config)

    def run_once(self) -> int:
        state = self._state.load_state()
        gmail_state = state.get("gmail", {}) if isinstance(state, Mapping) else {}
        last_history_id = gmail_state.get("last_history_id") if isinstance(gmail_state, Mapping) else None

        try:
            history = self._gmail.list_history(last_history_id)
            fallback_used = False
        except GmailHistoryNotFound:
            history = self._gmail.fallback_fetch(self._config.gmail_fallback_days)
            fallback_used = True

        processed = 0
        seen: set[str] = set()
        for message_id in history.message_ids:
            if message_id in seen:
                continue
            seen.add(message_id)
            message = self._gmail.fetch_message(message_id)
            episode = self._processor.process(self._normalize_message(message))
            self._episodes.upsert_episode(episode)
            processed += 1

        update_payload = {
            "gmail": {
                "last_history_id": history.latest_history_id,
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "fallback_used": fallback_used,
            }
        }
        self._state.update_state(update_payload)
        return processed

    def backfill(self, newer_than_days: int | None = None) -> int:
        """Fetch messages from the fallback window and ingest recent episodes."""

        days = max(int(newer_than_days or self._config.gmail_backfill_days), 1)
        history = self._gmail.fallback_fetch(days)
        processed = 0
        seen: set[str] = set()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        for index, message_id in enumerate(history.message_ids, start=1):
            if message_id in seen:
                continue
            seen.add(message_id)
            message = self._gmail.fetch_message(message_id)
            episode = self._normalize_message(message)
            if episode.valid_at and episode.valid_at < cutoff:
                continue
            processed_episode = self._processor.process(episode)
            self._episodes.upsert_episode(processed_episode)
            processed += 1
            if index % 25 == 0:
                sleep_with_jitter(0.4, 0.2)

        payload = {
            "gmail": {
                "last_history_id": history.latest_history_id,
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "fallback_used": True,
                "backfilled_days": days,
                "backfill_ran_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        self._state.update_state(payload)
        return processed

    def _normalize_message(self, message: Mapping[str, object]) -> Episode:
        message_id = str(message.get("id"))
        if not message_id:
            raise ValueError("Gmail message missing id")
        native_id = message_id
        thread_id = message.get("threadId")
        internal_date_raw = message.get("internalDate")
        if internal_date_raw is None:
            raise ValueError(f"Message {message_id} missing internalDate")
        try:
            internal_ms = int(internal_date_raw)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            raise ValueError(
                f"Message {message_id} has invalid internalDate {internal_date_raw!r}"
            ) from exc
        internal_date = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
        history_id = str(message.get("historyId") or internal_ms)
        snippet = message.get("snippet")

        headers = {}
        payload = message.get("payload")
        if isinstance(payload, Mapping):
            headers_list = payload.get("headers")
            if isinstance(headers_list, Iterable):
                for header in headers_list:
                    if isinstance(header, Mapping):
                        name = header.get("name")
                        value = header.get("value")
                        if isinstance(name, str) and isinstance(value, str):
                            headers[name.lower()] = value

        from_addr = headers.get("from")
        to_addr = headers.get("to")
        message_id_header = headers.get("message-id")

        metadata = {
            "message_id": message_id,
            "thread_id": thread_id,
            "headers_json": json.dumps(headers) if headers else "{}",
        }
        if from_addr:
            metadata["from"] = from_addr
        if to_addr:
            metadata["to"] = to_addr
        if message_id_header:
            metadata["message_id_hdr"] = message_id_header

        return Episode(
            group_id=self._group_id,
            source="gmail",
            native_id=native_id,
            version=history_id,
            valid_at=internal_date,
            text=str(snippet) if snippet is not None else None,
            metadata=metadata,
        )


__all__ = ["GmailPoller", "GmailHistoryNotFound", "GmailHistoryResult"]
