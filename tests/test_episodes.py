from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pytest

from graphiti.episodes import Episode, Neo4jEpisodeStore


def test_episode_properties_include_optional_fields() -> None:
    episode = Episode(
        group_id="mike_assistant",
        source="gmail",
        native_id="mid",
        version="123",
        valid_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        invalid_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        text="hello",
        json={"key": "value"},
        metadata={"message_id": "mid"},
    )
    props = episode.to_properties()
    assert props["invalid_at"] == "2024-01-02T00:00:00+00:00"
    assert props["text"] == "hello"
    assert props["json"] == {"key": "value"}


def test_upsert_episode_executes_queries_in_order() -> None:
    driver = mock.MagicMock()
    session = driver.session.return_value.__enter__.return_value

    episode = Episode(
        group_id="mike_assistant",
        source="gmail",
        native_id="mid",
        version="123",
        valid_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    store = Neo4jEpisodeStore(driver, group_id="mike_assistant")
    store.upsert_episode(episode)

    session.execute_write.assert_any_call(store._invalidate_previous_version, episode)
    session.execute_write.assert_any_call(store._write_episode, episode)


def test_upsert_episode_rejects_mismatched_group() -> None:
    driver = mock.MagicMock()
    store = Neo4jEpisodeStore(driver, group_id="expected")
    episode = Episode(
        group_id="other",
        source="gmail",
        native_id="mid",
        version="123",
        valid_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError):
        store.upsert_episode(episode)


def test_fetch_latest_episode_calls_driver() -> None:
    driver = mock.MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.execute_read.return_value = {"episode_id": "gmail:mid:123"}

    store = Neo4jEpisodeStore(driver, group_id="mike_assistant")
    result = store.fetch_latest_episode_by_native_id("gmail", "mid")

    assert result == {"episode_id": "gmail:mid:123"}
    session.execute_read.assert_called_once()
