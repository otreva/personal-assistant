from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pytest

from graphiti.config import GraphitiConfig
from graphiti.episodes import Neo4jEpisodeStore
from graphiti.pollers.gmail import (
    GmailHistoryNotFound,
    GmailHistoryResult,
    GmailPoller,
)
from graphiti.state import GraphitiStateStore


def _message(message_id: str, *, history_id: str = "2", snippet: str = "hello") -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": "thread-1",
        "historyId": history_id,
        "internalDate": "1700000000000",
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "bob@example.com"},
            ]
        },
    }


def test_gmail_poller_incremental_updates_state(tmp_path):
    config = GraphitiConfig(group_id="test_group")
    gmail_client = mock.MagicMock()
    gmail_client.list_history.return_value = GmailHistoryResult(["m1", "m1"], "456")
    gmail_client.fetch_message.return_value = _message("m1")

    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value=config.group_id)
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")
    state_store.save_state({"gmail": {"last_history_id": "123"}})

    poller = GmailPoller(gmail_client, episode_store, state_store, config)
    processed = poller.run_once()

    assert processed == 1
    gmail_client.list_history.assert_called_once_with("123")
    episode_store.upsert_episode.assert_called_once()
    saved = state_store.load_state()["gmail"]
    assert saved["last_history_id"] == "456"
    assert saved["fallback_used"] is False


def test_gmail_poller_uses_fallback_on_missing_history(tmp_path):
    config = GraphitiConfig(group_id="test_group", gmail_fallback_days=9)
    gmail_client = mock.MagicMock()
    gmail_client.list_history.side_effect = GmailHistoryNotFound()
    gmail_client.fallback_fetch.return_value = GmailHistoryResult(["m1"], "900")
    gmail_client.fetch_message.return_value = _message("m1")

    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value=config.group_id)
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")

    poller = GmailPoller(gmail_client, episode_store, state_store, config)
    processed = poller.run_once()

    assert processed == 1
    gmail_client.fallback_fetch.assert_called_once_with(9)
    saved = state_store.load_state()["gmail"]
    assert saved["fallback_used"] is True


def test_gmail_poller_validates_group_id(tmp_path):
    config = GraphitiConfig(group_id="expected")
    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value="other")
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")

    with pytest.raises(ValueError):
        GmailPoller(mock.MagicMock(), episode_store, state_store, config)
