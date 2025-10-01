from __future__ import annotations

import json
import pathlib
from pathlib import Path
from unittest import mock

from graphiti import GraphitiStateStore, cli


def test_cli_status_outputs_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    state_store = GraphitiStateStore()
    monkeypatch.setattr(cli, "GraphitiStateStore", lambda: state_store)
    monkeypatch.setattr(
        cli,
        "load_config",
        mock.Mock(
            return_value=mock.Mock(
                neo4j_uri="bolt://localhost:7687",
                neo4j_user="neo4j",
                group_id="group",
                poll_gmail_drive_calendar_seconds=3600,
                poll_slack_active_seconds=30,
                poll_slack_idle_seconds=3600,
                gmail_fallback_days=7,
                gmail_backfill_days=365,
                drive_backfill_days=365,
                calendar_backfill_days=365,
                slack_backfill_days=365,
                calendar_ids=("primary",),
                slack_search_query="in:general",
                backup_directory="/tmp/backups",
                backup_retention_days=14,
                log_retention_days=30,
                logs_directory=None,
            )
        ),
    )

    exit_code = cli.main(["status"])
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["state_directory"] == str(state_store.base_dir)


def _stub_episode_store():
    class _Store:
        group_id = "group"

    return _Store()


def test_cli_sync_gmail_once(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "create_episode_store", lambda config: _stub_episode_store())
    monkeypatch.setattr(cli, "close_episode_store", lambda store: None)

    poller_mock = mock.Mock()
    poller_mock.run_once.return_value = 5

    def factory(config, state, store):
        return poller_mock

    monkeypatch.setitem(cli.POLLER_FACTORIES, "gmail", factory)

    exit_code = cli.main(["sync", "gmail", "--once"])
    assert exit_code == 0
    poller_mock.run_once.assert_called_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload["processed"] == 5


def test_cli_sync_slack_list_channels(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    state_store = GraphitiStateStore()
    monkeypatch.setattr(cli, "GraphitiStateStore", lambda: state_store)
    monkeypatch.setattr(cli, "create_episode_store", lambda config: _stub_episode_store())
    monkeypatch.setattr(cli, "close_episode_store", lambda store: None)

    slack_client = mock.Mock()
    slack_client.list_channels.return_value = [
        {"id": "C1", "name": "general"},
        {"id": "C2", "name": "random"},
    ]
    monkeypatch.setattr(cli, "create_slack_client", lambda config, state: slack_client)

    exit_code = cli.main(["sync", "slack", "--list-channels"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output) == 2
    state = state_store.load_state()["slack"]
    assert "C1" in state["channels"]


def test_cli_sync_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    store = GraphitiStateStore()
    store.update_state({
        "gmail": {"last_run_at": "2024-01-01T00:00:00Z", "last_history_id": "123"}
    })
    monkeypatch.setattr(cli, "GraphitiStateStore", lambda: store)

    exit_code = cli.main(["sync", "status"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Personal Assistant Sync Status" in output
    assert "gmail" in output
    assert "123" not in output  # checkpoint details suppressed in dashboard


def test_cli_sync_status_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    store = GraphitiStateStore()
    store.update_state({
        "gmail": {"last_run_at": "2024-01-01T00:00:00Z", "last_history_id": "123"}
    })
    monkeypatch.setattr(cli, "GraphitiStateStore", lambda: store)

    exit_code = cli.main(["sync", "status", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sources"]["gmail"]["last_run_at"].startswith("2024-01-01")


def test_cli_sync_scheduler_once(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "create_episode_store", lambda config: _stub_episode_store())
    monkeypatch.setattr(cli, "close_episode_store", lambda store: None)

    def factory(name):
        poller = mock.Mock()
        poller.run_once.return_value = 1
        return poller

    def gmail_factory(config, state, store):
        return factory("gmail")

    def drive_factory(config, state, store):
        return factory("drive")

    def calendar_factory(config, state, store):
        return factory("calendar")

    monkeypatch.setitem(cli.POLLER_FACTORIES, "gmail", gmail_factory)
    monkeypatch.setitem(cli.POLLER_FACTORIES, "drive", drive_factory)
    monkeypatch.setitem(cli.POLLER_FACTORIES, "calendar", calendar_factory)

    slack_poller = mock.Mock()
    slack_poller.run_once.return_value = 2
    monkeypatch.setattr(cli, "create_slack_client", lambda config, state: mock.Mock())

    monkeypatch.setattr(cli, "SlackPoller", lambda client, store, state: slack_poller)

    exit_code = cli.main(["sync", "scheduler", "--once"])
    assert exit_code == 0
    metrics = json.loads(capsys.readouterr().out)["metrics"]
    assert {entry["source"] for entry in metrics} == {"gmail", "drive", "calendar", "slack"}


def test_cli_backup_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    store = GraphitiStateStore()
    store.save_state({"gmail": {"last_history_id": "123"}})
    monkeypatch.setattr(cli, "GraphitiStateStore", lambda: store)

    exit_code = cli.main(["backup", "state", "--output", str(tmp_path)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    archive = Path(payload["backup_path"])
    assert archive.exists()


def test_cli_restore_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    store = GraphitiStateStore()
    store.save_state({"gmail": {"last_history_id": "old"}})
    monkeypatch.setattr(cli, "GraphitiStateStore", lambda: store)

    archive = cli.create_state_backup(store, destination=tmp_path)  # type: ignore[attr-defined]
    store.save_state({"gmail": {"last_history_id": "mutated"}})

    exit_code = cli.main(["restore", "state", str(archive)])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["restored_from"]) == archive
    assert store.load_state()["gmail"]["last_history_id"] == "old"
