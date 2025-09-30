from __future__ import annotations

from unittest import mock

import pytest

from graphiti.config import GraphitiConfig
from graphiti.episodes import Neo4jEpisodeStore
from graphiti.pollers.drive import (
    DriveChangesResult,
    DriveFileContent,
    DrivePoller,
)
from graphiti.state import GraphitiStateStore


def _change(file_id: str, *, removed: bool = False) -> dict[str, object]:
    return {
        "fileId": file_id,
        "removed": removed,
        "time": "2024-01-01T00:00:00Z",
        "file": None if removed else {
            "modifiedTime": "2024-01-01T00:00:00Z",
            "headRevisionId": "rev-1",
            "name": "Doc",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "http://example.com",
        },
    }


def test_drive_poller_processes_changes(tmp_path):
    config = GraphitiConfig(group_id="test_group")
    drive_client = mock.MagicMock()
    drive_client.list_changes.return_value = DriveChangesResult([_change("f1")], "token-2")
    drive_client.fetch_file_content.return_value = DriveFileContent("text", {"owners": ["alice"]})

    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value=config.group_id)
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")
    state_store.save_state({"drive": {"page_token": "token-1"}})

    poller = DrivePoller(drive_client, episode_store, state_store, config)
    processed = poller.run_once()

    assert processed == 1
    drive_client.list_changes.assert_called_once_with("token-1")
    episode_store.upsert_episode.assert_called_once()
    saved = state_store.load_state()["drive"]
    assert saved["page_token"] == "token-2"


def test_drive_poller_creates_tombstone_for_removals(tmp_path):
    config = GraphitiConfig(group_id="test_group")
    drive_client = mock.MagicMock()
    drive_client.list_changes.return_value = DriveChangesResult([_change("f1", removed=True)], "token-3")

    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value=config.group_id)
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")

    poller = DrivePoller(drive_client, episode_store, state_store, config)
    processed = poller.run_once()

    assert processed == 1
    drive_client.fetch_file_content.assert_not_called()
    episode = episode_store.upsert_episode.call_args.args[0]
    assert episode.json == {"deleted": True}
    assert episode.metadata["tombstone"] is True


def test_drive_poller_validates_group(tmp_path):
    config = GraphitiConfig(group_id="expected")
    episode_store = mock.MagicMock(spec=Neo4jEpisodeStore)
    type(episode_store).group_id = mock.PropertyMock(return_value="other")
    state_store = GraphitiStateStore(base_dir=tmp_path / "state")

    with pytest.raises(ValueError):
        DrivePoller(mock.MagicMock(), episode_store, state_store, config)
