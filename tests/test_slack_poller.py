from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping
import time

import pytest

from graphiti.config import GraphitiConfig
from graphiti.pollers.slack import NullSlackClient, SlackPoller, SlackRateLimited
from graphiti.state import GraphitiStateStore


class InMemoryEpisodeStore:
    def __init__(self, group_id: str) -> None:
        self.group_id = group_id
        self.episodes = []

    def upsert_episode(self, episode):
        self.episodes.append(episode)


class FakeSlackClient(NullSlackClient):
    def __init__(self, channels: Iterable[Mapping[str, object]]):
        super().__init__(tuple(dict(channel) for channel in channels))
        self.histories: dict[str, list[Mapping[str, object]]] = {}
        self.threads: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        self.history_calls: list[tuple[str, str | None]] = []
        self.thread_calls: list[tuple[str, str, str | None]] = []
        self._rate_limit_next = False

    def queue_history(self, channel_id: str, messages: Iterable[Mapping[str, object]]):
        self.histories[channel_id] = list(messages)

    def queue_thread(self, channel_id: str, thread_ts: str, messages: Iterable[Mapping[str, object]]):
        self.threads[(channel_id, thread_ts)] = list(messages)

    def fetch_channel_history(self, channel_id: str, oldest_ts: str | None):
        self.history_calls.append((channel_id, oldest_ts))
        if self._rate_limit_next:
            self._rate_limit_next = False
            raise SlackRateLimited(0)
        return self.histories.get(channel_id, [])

    def fetch_thread_replies(self, channel_id: str, thread_ts: str, oldest_ts: str | None):
        self.thread_calls.append((channel_id, thread_ts, oldest_ts))
        return self.threads.get((channel_id, thread_ts), [])

    def trigger_rate_limit(self) -> None:
        self._rate_limit_next = True


@pytest.fixture()
def state_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GraphitiStateStore:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return GraphitiStateStore()


def test_slack_poller_processes_messages_and_threads(state_store, monkeypatch):
    config = GraphitiConfig(group_id="group")
    episode_store = InMemoryEpisodeStore(group_id="group")

    channels = ({"id": "C1", "name": "general"},)
    client = FakeSlackClient(channels)
    ts = str(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    client.queue_history(
        "C1",
        [
            {
                "ts": ts,
                "text": "Hello",
                "user": "U1",
                "thread_ts": ts,
            },
            {
                "ts": "1704067201.0",
                "text": "Follow up",
                "user": "U2",
                "thread_ts": ts,
            },
        ],
    )
    client.queue_thread(
        "C1",
        ts,
        [
            {"ts": ts, "text": "Hello", "user": "U1", "thread_ts": ts},
            {"ts": "1704067300.0", "text": "Thread reply", "user": "U3", "thread_ts": ts},
        ],
    )

    poller = SlackPoller(client, episode_store, state_store, config=config)
    processed = poller.run_once()

    assert processed == 3
    assert len(episode_store.episodes) == 3
    state = state_store.load_state()["slack"]
    assert state["channels"]["C1"]["last_seen_ts"] == "1704067201.0"
    assert state["channels"]["C1"]["metadata"]["name"] == "general"
    assert state["threads"]["C1"][ts]["last_seen_ts"] == "1704067300.0"
    assert state.get("checkpoints") is None


def test_slack_poller_honors_allowlist(state_store):
    config = GraphitiConfig(group_id="group", slack_channel_allowlist=("restricted",))
    episode_store = InMemoryEpisodeStore(group_id="group")
    client = FakeSlackClient(({"id": "C1", "name": "general"}, {"id": "C2", "name": "restricted"}))
    poller = SlackPoller(client, episode_store, state_store, config=config)
    state_store.update_state(
        {
            "slack": {
                "channels": {
                    "C1": {"metadata": {"name": "general"}},
                    "C2": {"metadata": {"name": "restricted"}},
                }
            }
        }
    )
    processed = poller.run_once()
    assert processed == 0
    assert client.history_calls[0][0] == "C2"


def test_slack_poller_handles_rate_limit(state_store, monkeypatch):
    config = GraphitiConfig(group_id="group")
    episode_store = InMemoryEpisodeStore(group_id="group")
    client = FakeSlackClient(({"id": "C1", "name": "general"},))
    client.queue_history("C1", [{"ts": "1.0", "text": "Msg", "user": "U1"}])
    client.trigger_rate_limit()

    slept = []

    def fake_sleep(value):
        slept.append(value)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    poller = SlackPoller(client, episode_store, state_store, config=config)
    processed = poller.run_once()
    assert processed == 1
    assert slept and slept[0] >= 1.0


def test_slack_poller_skips_bots(state_store):
    config = GraphitiConfig(group_id="group")
    episode_store = InMemoryEpisodeStore(group_id="group")
    client = FakeSlackClient(({"id": "C1", "name": "general"},))
    client.queue_history(
        "C1",
        [
            {"ts": "1.0", "text": "bot", "subtype": "bot_message"},
            {"ts": "2.0", "text": "user", "user": "U1"},
        ],
    )
    poller = SlackPoller(client, episode_store, state_store, config=config)
    processed = poller.run_once()
    assert processed == 1
    assert len(episode_store.episodes) == 1
