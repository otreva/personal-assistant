from pathlib import Path

from graphiti.logs import GraphitiLogStore


def test_log_store_append_and_tail(tmp_path):
    store = GraphitiLogStore(tmp_path)
    record = store.append(
        "system",
        "hello world",
        data={"foo": "bar"},
        retention_days=7,
    )
    assert record.message == "hello world"
    assert record.level == "INFO"

    # additional entry to validate ordering
    store.append("system", "second", retention_days=7)

    records = store.tail("system", limit=1)
    assert len(records) == 1
    assert records[0].message == "second"

    # pruning with zero days clears the file
    store.prune(0)
    assert store.tail("system") == []
    assert Path(tmp_path / "system.log").exists() is False


def test_log_store_categories(tmp_path):
    store = GraphitiLogStore(tmp_path)
    store.append("system", "entry", retention_days=7)
    store.append("episodes", "episode", retention_days=7)

    categories = store.categories()
    assert set(categories) == {"episodes", "system"}
