"""End-to-end acceptance test harness utilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, MutableMapping, Sequence

from .config import GraphitiConfig
from .episodes import Neo4jEpisodeStore
from .mcp.logger import McpEpisodeLogger, McpTurn
from .pollers.calendar import CalendarEventsPage, CalendarPoller
from .pollers.drive import DriveChangesResult, DriveFileContent, DrivePoller
from .pollers.gmail import GmailHistoryResult, GmailPoller
from .pollers.slack import SlackPoller
from .state import GraphitiStateStore


@dataclass(slots=True)
class AcceptanceDataset:
    """Synthetic dataset used by the acceptance harness."""

    gmail_messages: Sequence[Mapping[str, object]] = ()
    drive_changes: Sequence[Mapping[str, object]] = ()
    calendar_events: Mapping[str, Sequence[Mapping[str, object]]] = field(
        default_factory=dict
    )
    slack_channels: Sequence[Mapping[str, object]] = ()
    slack_messages: Mapping[str, Sequence[Mapping[str, object]]] = field(
        default_factory=dict
    )
    slack_threads: Mapping[str, Mapping[str, Sequence[Mapping[str, object]]]] = field(
        default_factory=dict
    )
    mcp_turns: Sequence[McpTurn] = ()
    state_seed: Mapping[str, object] = field(default_factory=dict)


@dataclass
class AcceptanceTestHarness:
    """Coordinate pollers and loggers against a synthetic dataset."""

    episode_store: Neo4jEpisodeStore
    config: GraphitiConfig | None = None
    state_store: GraphitiStateStore | None = None

    def __post_init__(self) -> None:
        self._config = self.config or GraphitiConfig()
        self._state = self.state_store or GraphitiStateStore()
        if self.episode_store.group_id != self._config.group_id:
            raise ValueError("Episode store group_id does not match configuration group_id")

    def run(self, dataset: AcceptanceDataset) -> Mapping[str, int]:
        """Execute pollers against the provided dataset and return processed counts."""

        if dataset.state_seed:
            self._state.save_state(dataset.state_seed)

        gmail_client = _HarnessGmailClient(dataset.gmail_messages)
        drive_client = _HarnessDriveClient(dataset.drive_changes)
        calendar_client = _HarnessCalendarClient(dataset.calendar_events)
        slack_client = _HarnessSlackClient(dataset.slack_channels, dataset.slack_messages, dataset.slack_threads)

        metrics: MutableMapping[str, int] = {}

        gmail_poller = GmailPoller(gmail_client, self.episode_store, self._state, self._config)
        metrics["gmail"] = gmail_poller.run_once()

        drive_poller = DrivePoller(drive_client, self.episode_store, self._state, self._config)
        metrics["drive"] = drive_poller.run_once()

        calendar_poller = CalendarPoller(
            calendar_client,
            self.episode_store,
            self._state,
            self._config.calendar_ids,
            self._config,
        )
        metrics["calendar"] = calendar_poller.run_once()

        slack_poller = SlackPoller(
            slack_client,
            self.episode_store,
            self._state,
            config=self._config,
        )
        metrics["slack"] = slack_poller.run_once()

        logger = McpEpisodeLogger(self.episode_store, self._config)
        for turn in dataset.mcp_turns:
            logger.log_turn(turn)
        metrics["mcp"] = logger.flush()

        return metrics


@dataclass
class _HarnessGmailClient:
    messages: Sequence[Mapping[str, object]]

    def list_history(self, start_history_id: str | None) -> GmailHistoryResult:
        message_ids = [str(message.get("id")) for message in self.messages if message.get("id")]
        latest = message_ids[-1] if message_ids else (start_history_id or "0")
        return GmailHistoryResult(message_ids=message_ids, latest_history_id=latest)

    def fallback_fetch(self, newer_than_days: int) -> GmailHistoryResult:
        return self.list_history(None)

    def fetch_message(self, message_id: str) -> Mapping[str, object]:
        for message in self.messages:
            if str(message.get("id")) == message_id:
                return dict(message)
        raise KeyError(f"Message {message_id} not found")


@dataclass
class _HarnessDriveClient:
    changes: Sequence[Mapping[str, object]]

    def list_changes(self, page_token: str | None) -> DriveChangesResult:
        return DriveChangesResult(changes=list(self.changes), new_page_token="next")

    def fetch_file_content(
        self, file_id: str, file_metadata: Mapping[str, object]
    ) -> DriveFileContent:
        text = str(file_metadata.get("content", "")) or None
        metadata = {k: v for k, v in file_metadata.items() if k != "content"}
        return DriveFileContent(text=text, metadata=metadata)


@dataclass
class _HarnessCalendarClient:
    events: Mapping[str, Sequence[Mapping[str, object]]]

    def list_events(self, calendar_id: str, sync_token: str | None) -> CalendarEventsPage:
        entries = list(self.events.get(calendar_id, ()))
        return CalendarEventsPage(events=entries, next_sync_token=f"{calendar_id}:token")

    def full_sync(self, calendar_id: str) -> CalendarEventsPage:
        return self.list_events(calendar_id, None)


@dataclass
class _HarnessSlackClient:
    channels: Sequence[Mapping[str, object]]
    messages: Mapping[str, Sequence[Mapping[str, object]]]
    threads: Mapping[str, Mapping[str, Sequence[Mapping[str, object]]]]

    def list_channels(self) -> Iterable[Mapping[str, object]]:
        return list(self.channels)

    def search_messages(
        self,
        query: str,
        *,
        oldest: str | None = None,
        cursor: str | None = None,
    ) -> Mapping[str, object]:
        try:
            oldest_value = float(oldest) if oldest else None
        except ValueError:
            oldest_value = None
        results: list[Mapping[str, object]] = []
        for channel in self.channels:
            channel_id = str(channel.get("id"))
            for message in self.messages.get(channel_id, ()):  # type: ignore[arg-type]
                ts = str(message.get("ts", ""))
                if oldest_value is not None:
                    try:
                        if float(ts) <= oldest_value:
                            continue
                    except ValueError:
                        pass
                enriched = dict(message)
                enriched.setdefault("channel", {"id": channel_id, "name": channel.get("name")})
                results.append(enriched)
        results.sort(key=lambda item: float(str(item.get("ts", "0"))), reverse=False)
        return {"messages": results, "next_cursor": None}

    def fetch_message(self, channel_id: str, ts: str) -> Mapping[str, object]:
        for entry in self.messages.get(channel_id, ()):  # type: ignore[arg-type]
            if str(entry.get("ts")) == str(ts):
                return dict(entry)
        for entry in self.threads.get(channel_id, {}).values():
            for reply in entry:
                if str(reply.get("ts")) == str(ts):
                    payload = dict(reply)
                    payload.setdefault("channel", {"id": channel_id})
                    return payload
        return {}

    def resolve_user(self, user_id: str) -> Mapping[str, object] | None:
        if not user_id:
            return None
        return {
            "id": user_id,
            "name": f"User {user_id}",
            "email": f"{user_id.lower()}@example.com",
        }

    def resolve_channel(self, channel_id: str) -> Mapping[str, object] | None:
        for channel in self.channels:
            if str(channel.get("id")) == str(channel_id):
                return {"id": channel_id, "name": channel.get("name")}
        return {"id": channel_id}


def build_fixture_dataset(start: datetime | None = None) -> AcceptanceDataset:
    """Construct a default dataset covering 14 days of activity."""

    base = start or datetime.now(timezone.utc)
    gmail_messages = [
        {
            "id": f"email-{idx}",
            "threadId": f"thread-{idx}",
            "historyId": str(idx + 1),
            "internalDate": str(int((base - timedelta(days=idx)).timestamp() * 1000)),
            "snippet": f"Email body {idx}",
        }
        for idx in range(3)
    ]

    drive_changes = [
        {
            "fileId": f"file-{idx}",
            "file": {
                "name": f"Doc {idx}",
                "mimeType": "text/plain",
                "modifiedTime": (base - timedelta(days=idx)).isoformat(),
                "content": f"Document content {idx}",
            },
        }
        for idx in range(2)
    ]

    calendar_events = {
        "primary": [
            {
                "id": "event-1",
                "updated": base.isoformat(),
                "status": "confirmed",
            }
        ]
    }

    slack_channels = [
        {"id": "C1", "name": "general"},
    ]
    slack_messages = {
        "C1": [
            {
                "ts": "1",
                "user": "U1",
                "text": "Hello team",
            }
        ]
    }
    slack_threads: Mapping[str, Mapping[str, Sequence[Mapping[str, object]]]] = {}

    mcp_turns = [
        McpTurn(
            message_id="mcp-1",
            conversation_id="conv-1",
            role="user",
            content="Summarise latest updates",
            timestamp=base,
        )
    ]

    state_seed: Mapping[str, object] = {
        "calendar": {"sync_tokens": {}},
        "slack": {"channels": {}},
    }

    return AcceptanceDataset(
        gmail_messages=gmail_messages,
        drive_changes=drive_changes,
        calendar_events=calendar_events,
        slack_channels=slack_channels,
        slack_messages=slack_messages,
        slack_threads=slack_threads,
        mcp_turns=mcp_turns,
        state_seed=state_seed,
    )


__all__ = ["AcceptanceDataset", "AcceptanceTestHarness", "build_fixture_dataset"]

