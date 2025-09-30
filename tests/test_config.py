import os
from pathlib import Path

import pytest

from graphiti.config import GraphitiConfig, load_config


def test_load_config_prefers_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("NEO4J_URI=bolt://dotenv:7687\nPOLL_GMAIL_DRIVE_CAL=100\n")

    monkeypatch.setenv("NEO4J_URI", "bolt://env:7687")
    monkeypatch.setenv("POLL_GMAIL_DRIVE_CAL", "200")

    config = load_config(dotenv_path=dotenv, environ=os.environ)
    assert config.neo4j_uri == "bolt://env:7687"
    assert config.poll_gmail_drive_calendar_seconds == 200


def test_invalid_integer_raises(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("POLL_SLACK_ACTIVE=not-an-int\n")
    with pytest.raises(ValueError):
        load_config(dotenv_path=dotenv, environ={})


def test_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(dotenv_path=tmp_path / ".env", environ={})
    assert isinstance(config, GraphitiConfig)
    assert config.group_id == "mike_assistant"
