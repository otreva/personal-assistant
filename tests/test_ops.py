from __future__ import annotations

import json

from graphiti.ops import create_state_backup, restore_state_backup
from graphiti.state import GraphitiStateStore


def test_create_and_restore_state_backup(tmp_path):
    state_dir = tmp_path / "state"
    store = GraphitiStateStore(base_dir=state_dir)
    store.save_state({"gmail": {"last_history_id": "123"}})
    store.save_tokens({"access_token": "token"})

    archive = create_state_backup(store, destination=tmp_path)
    assert archive.exists()

    # mutate state then restore
    (state_dir / "state.json").write_text(json.dumps({"gmail": {"last_history_id": "999"}}))
    restored_path = restore_state_backup(store, archive)

    assert restored_path == state_dir
    data = store.load_state()
    assert data["gmail"]["last_history_id"] == "123"

