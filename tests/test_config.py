from pathlib import Path

import pytest

from graphiti.config import ConfigStore, GraphitiConfig, load_config


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setenv("GRAPHITI_CONFIG_PATH", str(path))
    return path


def test_load_config_creates_default_store(config_path: Path) -> None:
    config = load_config()
    assert config_path.exists()
    assert config.group_id == "mike_assistant"
    data = config_path.read_text()
    assert "neo4j_uri" in data


def test_environment_overrides_store(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfigStore(config_path)
    store.save(GraphitiConfig(neo4j_uri="bolt://store:7687"))

    monkeypatch.setenv("NEO4J_URI", "bolt://env:7687")
    monkeypatch.setenv("POLL_GMAIL_DRIVE_CAL", "120")

    config = load_config()
    assert config.neo4j_uri == "bolt://env:7687"
    assert config.poll_gmail_drive_calendar_seconds == 120


def test_invalid_numeric_input_raises(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("POLL_SLACK_ACTIVE", "not-an-int")
    with pytest.raises(ValueError):
        load_config()


def test_config_store_round_trip(config_path: Path) -> None:
    store = ConfigStore(config_path)
    original = GraphitiConfig(
        slack_channel_allowlist=("C1", "C2"),
        calendar_ids=("primary", "team"),
        summarization_threshold=2000,
        redaction_rules=(
            ("secret@example.com", "[MASKED]"),
            ("(?i)password", "***"),
        ),
    )
    store.save(original)

    loaded = store.load()
    assert loaded.slack_channel_allowlist == ("C1", "C2")
    assert loaded.calendar_ids == ("primary", "team")
    assert loaded.summarization_threshold == 2000
    assert loaded.redaction_rules == (
        ("secret@example.com", "[MASKED]"),
        ("(?i)password", "***"),
    )
