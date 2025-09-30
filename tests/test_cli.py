from __future__ import annotations

import json
import pathlib
from unittest import mock

from graphiti import GraphitiStateStore, cli


def test_cli_status_outputs_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    state_store = GraphitiStateStore()
    monkeypatch.setattr(cli, "GraphitiStateStore", lambda: state_store)
    monkeypatch.setattr(cli, "load_config", mock.Mock(return_value=mock.Mock(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        group_id="group",
        poll_gmail_drive_calendar_seconds=3600,
        poll_slack_active_seconds=30,
        poll_slack_idle_seconds=3600,
        gmail_fallback_days=7,
    )))

    exit_code = cli.main(["status"])
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["state_directory"] == str(state_store.base_dir)
