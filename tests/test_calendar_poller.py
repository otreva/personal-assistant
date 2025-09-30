from __future__ import annotations

from unittest import mock

import pytest

from graphiti.config import GraphitiConfig
from graphiti.episodes import Neo4jEpisodeStore
from graphiti.pollers.calendar import (
    CalendarEventsPage,
    CalendarPoller,
    CalendarSyncTokenExpired,
)
from graphiti.state import GraphitiStateStore


def _event(event_id: str, *, status: str = "confirmed") -> dict[str, object]:
    return {
        "id": event_id,
        "updated": "2024-01-01T00:00:00Z",
        "status": status,
        "summary": "Meeting",
    }


def test_calendar_poller_updates_tokens(tmp_path):
    config = GraphitiConfig(group_id="group")
    client = mock.MagicMock()
    client.list_events.return_value = CalendarEventsPage([_event("e1"), _event("e2")], "sync-2")

    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value=config.group_id)
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")
    state_store.save_state({"calendar": {"sync_tokens": {"primary": "sync-1"}}})

    poller = CalendarPoller(client, episode_store, state_store, ["primary"], config)
    processed = poller.run_once()

    assert processed == 2
    client.list_events.assert_called_once_with("primary", "sync-1")
    state = state_store.load_state()["calendar"]
    assert state["sync_tokens"]["primary"] == "sync-2"


def test_calendar_poller_handles_cancelled_event(tmp_path):
    config = GraphitiConfig(group_id="group")
    client = mock.MagicMock()
    client.list_events.return_value = CalendarEventsPage([_event("e1", status="cancelled")], "sync-2")

    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value=config.group_id)
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")

    poller = CalendarPoller(client, episode_store, state_store, ["primary"], config)
    poller.run_once()

    episode = episode_store.upsert_episode.call_args.args[0]
    assert episode.metadata["tombstone"] is True
    assert episode.json["cancelled"] is True


def test_calendar_poller_performs_full_sync_on_token_expiry(tmp_path):
    config = GraphitiConfig(group_id="group")
    client = mock.MagicMock()
    client.list_events.side_effect = CalendarSyncTokenExpired()
    client.full_sync.return_value = CalendarEventsPage([_event("e1")], "sync-3")

    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value=config.group_id)
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")

    poller = CalendarPoller(client, episode_store, state_store, ["primary"], config)
    processed = poller.run_once()

    assert processed == 1
    client.full_sync.assert_called_once_with("primary")


def test_calendar_poller_validates_group(tmp_path):
    config = GraphitiConfig(group_id="expected")
    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value="other")
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")

    with pytest.raises(ValueError):
        CalendarPoller(mock.MagicMock(), episode_store, state_store, ["primary"], config)
