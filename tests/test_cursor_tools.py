from __future__ import annotations

from datetime import datetime, timezone

import pytest

from graphiti.cursor import CursorTool, GraphitiCursorToolset, GraphitiQueryService


class FakeNode:
    def __init__(self, properties):
        self._properties = properties


class FakeResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        if not self._records:
            return None
        return self._records[0]


class FakeTx:
    def __init__(self, records):
        self._records = records
        self.last_query = None
        self.last_params = None

    def run(self, statement, **params):
        self.last_query = statement
        self.last_params = params
        return FakeResult(self._records)


class FakeSession:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute_read(self, func, params):
        tx = FakeTx(self.records)
        return func(tx, params)


class FakeDriver:
    def __init__(self, records):
        self.records = records

    def session(self):
        return FakeSession(self.records)


@pytest.fixture()
def driver():
    return FakeDriver([[FakeNode({"episode_id": "1", "text": "hello"})]])


def test_hybrid_search(driver):
    service = GraphitiQueryService(driver, group_id="group")
    results = service.hybrid_search("hello", limit=5)
    assert results[0]["episode_id"] == "1"


def test_as_of_returns_none(driver):
    driver.records = []
    service = GraphitiQueryService(driver, group_id="group")
    result = service.as_of("gmail", "native", datetime.now(timezone.utc))
    assert result is None


def test_shortest_path(driver):
    driver.records = [[FakeNode({"episode_id": "1"}), FakeNode({"episode_id": "2"})]]
    service = GraphitiQueryService(driver, group_id="group")
    result = service.shortest_path("A", "B", source="gmail", max_depth=5)
    assert [node["episode_id"] for node in result] == ["1", "2"]


def test_cursor_tool_validation():
    tool = CursorTool(
        name="demo",
        description="",
        schema={"properties": {"name": {"type": "string"}}, "required": ["name"]},
        _handler=lambda **params: params,
    )
    with pytest.raises(ValueError):
        tool.run()
    with pytest.raises(ValueError):
        tool.run(name=123)
    assert tool.run(name="valid")["name"] == "valid"


def test_toolset_dispatch(driver):
    driver.records = [[FakeNode({"episode_id": "1", "text": "hello"})]]
    service = GraphitiQueryService(driver, group_id="group")
    toolset = GraphitiCursorToolset(service)
    tools = {tool.name: tool for tool in toolset.tools()}
    hybrid = tools["graphiti_hybrid_search"]
    assert hybrid.run(query="hello")
    with pytest.raises(ValueError):
        tools["graphiti_as_of"].run(source="gmail", native_id="id")
    result = tools["graphiti_as_of"].run(
        source="gmail", native_id="id", as_of=datetime.now(timezone.utc).isoformat()
    )
    assert result == driver.records[0][0]._properties
