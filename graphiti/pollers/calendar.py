from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Protocol

from ..config import GraphitiConfig, load_config
from ..episodes import Episode, Neo4jEpisodeStore
from ..hooks import EpisodeProcessor
from ..state import GraphitiStateStore


class CalendarSyncTokenExpired(Exception):
    """Raised when Google Calendar indicates the sync token is invalid."""


@dataclass(slots=True)
class CalendarEventsPage:
    events: Iterable[Mapping[str, object]]
    next_sync_token: str


class CalendarClient(Protocol):  # pragma: no cover - protocol definition
    def list_events(self, calendar_id: str, sync_token: str | None) -> CalendarEventsPage: ...

    def full_sync(self, calendar_id: str) -> CalendarEventsPage: ...


class CalendarPoller:
    """Google Calendar incremental poller."""

    def __init__(
        self,
        calendar_client: CalendarClient,
        episode_store: Neo4jEpisodeStore,
        state_store: GraphitiStateStore,
        calendar_ids: Iterable[str],
        config: GraphitiConfig | None = None,
    ) -> None:
        self._client = calendar_client
        self._episodes = episode_store
        self._state = state_store
        self._config = config or load_config()
        if self._episodes.group_id != self._config.group_id:
            raise ValueError("Episode store group_id does not match configuration group_id")
        self._group_id = self._config.group_id
        self._calendar_ids = list(calendar_ids)
        self._processor = EpisodeProcessor(self._config)

    def run_once(self) -> int:
        state = self._state.load_state()
        calendar_state = state.get("calendar", {}) if isinstance(state, Mapping) else {}
        sync_tokens = calendar_state.get("sync_tokens", {}) if isinstance(calendar_state, Mapping) else {}

        processed = 0
        new_tokens: dict[str, str] = dict(sync_tokens) if isinstance(sync_tokens, Mapping) else {}

        for calendar_id in self._calendar_ids:
            token = sync_tokens.get(calendar_id) if isinstance(sync_tokens, Mapping) else None
            try:
                page = self._client.list_events(calendar_id, token)
            except CalendarSyncTokenExpired:
                page = self._client.full_sync(calendar_id)

            for event in page.events:
                episode = self._processor.process(
                    self._normalize_event(calendar_id, event)
                )
                self._episodes.upsert_episode(episode)
                processed += 1
            new_tokens[calendar_id] = page.next_sync_token

        self._state.update_state(
            {
                "calendar": {
                    "sync_tokens": new_tokens,
                    "last_run_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        )
        return processed

    def _normalize_event(self, calendar_id: str, event: Mapping[str, object]) -> Episode:
        event_id = event.get("id")
        updated = event.get("updated")
        status = event.get("status")
        if not isinstance(event_id, str):
            raise ValueError("Calendar event missing id")
        if not isinstance(updated, str):
            raise ValueError(f"Event {event_id} missing updated timestamp")

        valid_at = self._parse_time(updated) or datetime.now(timezone.utc)
        version = updated
        metadata = {
            "calendar_id": calendar_id,
            "event_id": event_id,
            "recurringEventId": event.get("recurringEventId"),
            "tombstone": status == "cancelled",
        }
        location = event.get("location")
        if location:
            metadata["location"] = location
        attendees = event.get("attendees")
        if isinstance(attendees, list) and attendees:
            metadata["attendees"] = attendees
        json_payload = dict(event)
        if status == "cancelled":
            json_payload = {"cancelled": True, "event": dict(event)}

        return Episode(
            group_id=self._group_id,
            source="calendar",
            native_id=event_id,
            version=version,
            valid_at=valid_at,
            json=json_payload,
            metadata=metadata,
        )

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value).astimezone(timezone.utc)
        except ValueError:  # pragma: no cover - defensive
            return None


__all__ = [
    "CalendarPoller",
    "CalendarClient",
    "CalendarEventsPage",
    "CalendarSyncTokenExpired",
]
