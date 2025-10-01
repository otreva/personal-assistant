from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

import pytest

from graphiti.config import GraphitiConfig
from graphiti.pollers.slack import NullSlackClient, SlackPoller, SlackRateLimited
from graphiti.state import GraphitiStateStore


class InMemoryEpisodeStore:
    def __init__(self, group_id: str) -> None:
        self.group_id = group_id
        self.episodes = []

    def upsert_episode(self, episode) -> None:  # pragma: no cover - simple store
        self.episodes.append(episode)


class FakeSlackClient(NullSlackClient):
    def __init__(self) -> None:
        super().__init__(())
        self._messages: list[Mapping[str, object]] = []
        self._returned_once = False
        self.fetch_payloads: dict[tuple[str, str], Mapping[str, object]] = {}
        self.users: dict[str, Mapping[str, object]] = {}
        self.channels: dict[str, Mapping[str, object]] = {}
        self.search_calls: list[tuple[str, str | None, str | None]] = []
        self.fetch_calls: list[tuple[str, str]] = []
        self.user_calls: list[str] = []
        self.channel_calls: list[str] = []
        self._rate_limit_next = False

    def list_channels(self) -> Iterable[Mapping[str, object]]:
        return list(self.channels.values())

    def queue_messages(self, messages: Iterable[Mapping[str, object]]) -> None:
        self._messages = [dict(message) for message in messages]
        self._returned_once = False

    def trigger_rate_limit(self) -> None:
        self._rate_limit_next = True

    def search_messages(
        self,
        query: str,
        *,
        oldest: str | None = None,
        cursor: str | None = None,
    ) -> Mapping[str, object]:
        self.search_calls.append((query, oldest, cursor))
        if self._rate_limit_next:
            self._rate_limit_next = False
            raise SlackRateLimited(0)
        if self._returned_once:
            return {"messages": [], "next_cursor": None}
        filtered: list[Mapping[str, object]] = []
        for message in self._messages:
            ts = str(message.get("ts", ""))
            if oldest:
                try:
                    if float(ts) <= float(oldest):
                        continue
                except ValueError:
                    pass
            filtered.append(dict(message))
        self._returned_once = True
        return {"messages": filtered, "next_cursor": None}

    def fetch_message(self, channel_id: str, ts: str) -> Mapping[str, object]:
        self.fetch_calls.append((channel_id, ts))
        return self.fetch_payloads.get((channel_id, ts), {})

    def resolve_user(self, user_id: str) -> Mapping[str, object] | None:
        if not user_id:
            return None
        self.user_calls.append(user_id)
        return self.users.get(user_id)

    def resolve_channel(self, channel_id: str) -> Mapping[str, object] | None:
        if not channel_id:
            return None
        self.channel_calls.append(channel_id)
        return self.channels.get(channel_id)


@pytest.fixture()
def state_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GraphitiStateStore:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return GraphitiStateStore()


def test_slack_poller_processes_search_results(state_store: GraphitiStateStore) -> None:
    config = GraphitiConfig(group_id="group", slack_search_query="in:general")
    episode_store = InMemoryEpisodeStore(group_id="group")

    client = FakeSlackClient()
    client.channels["C1"] = {"id": "C1", "name": "general"}
    client.users["U1"] = {"id": "U1", "name": "Alice", "email": "alice@example.com"}
    client.users["U2"] = {"id": "U2", "real_name": "Bob", "email": "bob@example.com"}
    client.queue_messages(
        [
            {
                "ts": "1.0",
                "text": "Hello",
                "user": "U1",
                "channel": {"id": "C1", "name": "general"},
                "permalink": "https://example.com/1",
            },
            {
                "ts": "2.0",
                "text": "Snippet",
                "user": "U2",
                "channel": {"id": "C1"},
                "is_truncated": True,
                "permalink": "https://example.com/2",
            },
        ]
    )
    client.fetch_payloads[("C1", "2.0")] = {
        "ts": "2.0",
        "text": "Full message",
        "user": "U2",
        "permalink": "https://example.com/full",
        "channel": {"id": "C1", "name": "general"},
    }

    poller = SlackPoller(client, episode_store, state_store, config=config)
    processed = poller.run_once()

    assert processed == 2
    assert len(episode_store.episodes) == 2
    second = episode_store.episodes[-1]
    assert second.text == "Full message"
    assert second.metadata["user_email"] == "bob@example.com"
    assert second.metadata["channel_name"] == "general"

    slack_state = state_store.load_state()["slack"]
    assert slack_state["search"]["last_seen_ts"] == "2.0"
    assert slack_state["users"]["U1"]["email"] == "alice@example.com"
    assert slack_state["channels"]["C1"]["name"] == "general"


def test_slack_poller_skips_previous_messages(state_store: GraphitiStateStore) -> None:
    config = GraphitiConfig(group_id="group", slack_search_query="in:general")
    episode_store = InMemoryEpisodeStore(group_id="group")
    client = FakeSlackClient()
    client.channels["C1"] = {"id": "C1", "name": "general"}
    client.queue_messages([{"ts": "5.0", "text": "Initial", "user": "U1", "channel": {"id": "C1"}}])

    poller = SlackPoller(client, episode_store, state_store, config=config)
    assert poller.run_once() == 1
    client.queue_messages([{"ts": "5.0", "text": "Initial", "user": "U1", "channel": {"id": "C1"}}])
    assert poller.run_once() == 0


def test_slack_poller_handles_rate_limit(state_store: GraphitiStateStore, monkeypatch: pytest.MonkeyPatch) -> None:
    config = GraphitiConfig(group_id="group", slack_search_query="in:general")
    episode_store = InMemoryEpisodeStore(group_id="group")
    client = FakeSlackClient()
    client.channels["C1"] = {"id": "C1", "name": "general"}
    client.queue_messages([{"ts": "1.0", "text": "Msg", "user": "U1", "channel": {"id": "C1"}}])
    client.trigger_rate_limit()

    slept: list[float] = []

    def fake_sleep(value: float) -> None:
        slept.append(value)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    poller = SlackPoller(client, episode_store, state_store, config=config)
    assert poller.run_once() == 1
    assert slept and slept[0] >= 1.0


def test_slack_poller_backfill_respects_cutoff(state_store: GraphitiStateStore) -> None:
    config = GraphitiConfig(group_id="group", slack_search_query="in:general")
    episode_store = InMemoryEpisodeStore(group_id="group")
    client = FakeSlackClient()
    client.channels["C1"] = {"id": "C1", "name": "general"}

    now = datetime.now(timezone.utc)
    recent_ts = f"{(now - timedelta(days=1)).timestamp():.6f}"
    old_ts = f"{(now - timedelta(days=10)).timestamp():.6f}"
    client.queue_messages(
        [
            {"ts": recent_ts, "text": "Recent", "user": "U1", "channel": {"id": "C1"}},
            {"ts": old_ts, "text": "Old", "user": "U1", "channel": {"id": "C1"}},
        ]
    )

    poller = SlackPoller(client, episode_store, state_store, config=config)
    processed = poller.backfill(newer_than_days=2)

    assert processed == 1
    assert len(episode_store.episodes) == 1
    slack_state = state_store.load_state()["slack"]
    assert slack_state["search"]["last_seen_ts"] == recent_ts
    assert slack_state["backfilled_days"] == 2
