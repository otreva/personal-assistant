from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphiti.config import GraphitiConfig
from graphiti.mcp.logger import McpEpisodeLogger, McpTurn


class InMemoryEpisodeStore:
    def __init__(self, group_id: str) -> None:
        self.group_id = group_id
        self.saved = []
        self.raise_error = False

    def upsert_episode(self, episode):
        if self.raise_error:
            raise RuntimeError("boom")
        self.saved.append(episode)


@pytest.fixture()
def turn() -> McpTurn:
    return McpTurn(
        message_id="msg1",
        conversation_id="conv",
        role="user",
        content="Hello",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        metadata={"foo": "bar"},
    )


def test_logger_flushes_turns(turn):
    store = InMemoryEpisodeStore("group")
    logger = McpEpisodeLogger(store, GraphitiConfig(group_id="group"))
    logger.log_turn(turn)
    assert logger.pending() == 1
    processed = logger.flush()
    assert processed == 1
    assert store.saved[0].metadata["conversation_id"] == "conv"
    assert logger.pending() == 0


def test_logger_trims_queue(turn):
    store = InMemoryEpisodeStore("group")
    logger = McpEpisodeLogger(store, GraphitiConfig(group_id="group"), queue_limit=1)
    logger.log_turn(turn)
    logger.log_turn(turn)
    assert logger.pending() == 1


def test_logger_requeues_on_failure(turn):
    store = InMemoryEpisodeStore("group")
    store.raise_error = True
    logger = McpEpisodeLogger(store, GraphitiConfig(group_id="group"))
    logger.log_turn(turn)
    with pytest.raises(RuntimeError):
        logger.flush()
    assert logger.pending() == 1
