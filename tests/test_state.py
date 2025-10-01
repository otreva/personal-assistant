from __future__ import annotations

from pathlib import Path

from graphiti.state import GraphitiStateStore


def test_state_directory_created_with_permissions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    store = GraphitiStateStore()
    path = store.ensure_directory()
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o700


def test_update_state_merges_nested(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    store = GraphitiStateStore()
    store.save_state({"gmail": {"last_history_id": "1"}, "drive": {"page_token": "abc"}})

    updated = store.update_state({"gmail": {"last_history_id": "2"}, "calendar": {"sync_tokens": {"id": "tok"}}})
    assert updated["gmail"]["last_history_id"] == "2"
    assert updated["drive"]["page_token"] == "abc"
    assert updated["calendar"]["sync_tokens"]["id"] == "tok"


def test_record_and_clear_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    store = GraphitiStateStore()
    store.save_state({"gmail": {"last_history_id": "1"}})

    store.record_error("gmail", "Timeout")
    state = store.load_state()
    assert state["gmail"]["error_count"] == 1
    assert state["gmail"]["last_error_message"] == "Timeout"

    store.record_error("gmail")
    state = store.load_state()
    assert state["gmail"]["error_count"] == 2

    store.clear_errors("gmail")
    state = store.load_state()
    assert "error_count" not in state["gmail"]
