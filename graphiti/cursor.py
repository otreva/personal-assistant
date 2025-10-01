"""Cursor tool integration for Graphiti."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping


class GraphitiQueryService:
    """High-level query helpers backed by Neo4j."""

    def __init__(self, driver: Any, *, group_id: str) -> None:
        self._driver = driver
        self._group_id = group_id

    def hybrid_search(self, query: str, *, limit: int = 10) -> list[Mapping[str, Any]]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string")
        if limit <= 0:
            raise ValueError("Limit must be positive")
        params = {"group_id": self._group_id, "query": query, "limit": limit}
        with self._driver.session() as session:
            return session.execute_read(self._query_hybrid, params)

    def as_of(
        self,
        source: str,
        native_id: str,
        as_of: datetime,
    ) -> Mapping[str, Any] | None:
        if not source or not native_id:
            raise ValueError("source and native_id are required")
        params = {
            "group_id": self._group_id,
            "source": source,
            "native_id": native_id,
            "as_of": as_of.isoformat(),
        }
        with self._driver.session() as session:
            return session.execute_read(self._query_as_of, params)

    def shortest_path(
        self,
        source_native_id: str,
        target_native_id: str,
        *,
        source: str,
        max_depth: int = 10,
    ) -> list[Mapping[str, Any]]:
        if not source_native_id or not target_native_id:
            raise ValueError("source_native_id and target_native_id are required")
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        params = {
            "group_id": self._group_id,
            "source": source,
            "source_native_id": source_native_id,
            "target_native_id": target_native_id,
            "max_depth": max_depth,
        }
        with self._driver.session() as session:
            return session.execute_read(self._query_shortest_path, params)

    @staticmethod
    def _query_hybrid(tx, params):  # pragma: no cover - exercised via driver mocks
        result = tx.run(
            """
            MATCH (e:Episode {group_id: $group_id})
            WHERE (exists(e.text) AND toLower(e.text) CONTAINS toLower($query))
            RETURN e ORDER BY e.valid_at DESC LIMIT $limit
            """,
            **params,
        )
        records: list[Mapping[str, Any]] = []
        for record in result:
            node = GraphitiQueryService._first_column(record)
            if node is not None:
                records.append(GraphitiQueryService._node_to_dict(node))
        return records

    @staticmethod
    def _query_as_of(tx, params):  # pragma: no cover - exercised via driver mocks
        record = tx.run(
            """
            MATCH (e:Episode {group_id: $group_id, source: $source, native_id: $native_id})
            WHERE datetime(e.valid_at) <= datetime($as_of)
            RETURN e ORDER BY e.valid_at DESC LIMIT 1
            """,
            **params,
        ).single()
        if not record:
            return None
        node = GraphitiQueryService._first_column(record)
        return GraphitiQueryService._node_to_dict(node) if node is not None else None

    @staticmethod
    def _query_shortest_path(tx, params):  # pragma: no cover - exercised via driver mocks
        result = tx.run(
            """
            MATCH (start:Episode {group_id: $group_id, source: $source, native_id: $source_native_id})
            MATCH (target:Episode {group_id: $group_id, source: $source, native_id: $target_native_id})
            MATCH p = shortestPath((start)-[*..$max_depth]-(target))
            RETURN [node IN nodes(p) | node] AS nodes
            """,
            **params,
        )
        record = result.single()
        if not record:
            return []
        nodes = GraphitiQueryService._first_column(record)
        if isinstance(nodes, (list, tuple)):
            return [GraphitiQueryService._node_to_dict(node) for node in nodes]
        if nodes is None:
            return []
        return [GraphitiQueryService._node_to_dict(nodes)]

    @staticmethod
    def _node_to_dict(node: Any) -> Mapping[str, Any]:
        if hasattr(node, "_properties"):
            return dict(node._properties)
        if isinstance(node, Mapping):
            return dict(node)
        return {"value": node}

    @staticmethod
    def _first_column(record: Any) -> Any:
        if isinstance(record, Mapping):
            for value in record.values():
                return value
            return None
        if isinstance(record, (list, tuple)):
            if not record:
                return None
            if len(record) == 1:
                return record[0]
            return record
        return record


@dataclass
class CursorTool:
    name: str
    description: str
    schema: Mapping[str, Any]
    _handler: Callable[..., Mapping[str, Any] | list[Mapping[str, Any]] | None]

    def run(self, **kwargs):
        params = self._validate(kwargs)
        return self._handler(**params)

    def _validate(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        properties = self.schema.get("properties", {})
        required = self.schema.get("required", [])
        validated: dict[str, Any] = {}
        for field in required:
            if field not in params:
                raise ValueError(f"Missing required parameter: {field}")
        for field, spec in properties.items():
            if field not in params:
                continue
            value = params[field]
            expected_type = spec.get("type")
            if expected_type == "string" and not isinstance(value, str):
                raise ValueError(f"{field} must be a string")
            if expected_type == "integer" and not isinstance(value, int):
                raise ValueError(f"{field} must be an integer")
            validated[field] = value
        for field in required:
            validated.setdefault(field, params[field])
        return validated


class GraphitiCursorToolset:
    """Collection of Cursor tools backed by the query service."""

    def __init__(self, query_service: GraphitiQueryService) -> None:
        self._service = query_service

    def tools(self) -> list[CursorTool]:
        return [
            CursorTool(
                name="graphiti_hybrid_search",
                description="Hybrid search across episodes",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
                _handler=self._run_hybrid,
            ),
            CursorTool(
                name="graphiti_as_of",
                description="Fetch episode as of a timestamp",
                schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "native_id": {"type": "string"},
                        "as_of": {"type": "string"},
                    },
                    "required": ["source", "native_id", "as_of"],
                },
                _handler=self._run_as_of,
            ),
            CursorTool(
                name="graphiti_shortest_path",
                description="Compute shortest path between two native ids",
                schema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "source_native_id": {"type": "string"},
                        "target_native_id": {"type": "string"},
                        "max_depth": {"type": "integer"},
                    },
                    "required": ["source", "source_native_id", "target_native_id"],
                },
                _handler=self._run_shortest_path,
            ),
        ]

    def _run_hybrid(self, query: str, limit: int = 10, **_: Any):
        return self._service.hybrid_search(query, limit=limit)

    def _run_as_of(self, source: str, native_id: str, as_of: str, **_: Any):
        parsed = datetime.fromisoformat(as_of)
        return self._service.as_of(source, native_id, parsed)

    def _run_shortest_path(
        self,
        source: str,
        source_native_id: str,
        target_native_id: str,
        max_depth: int = 10,
        **_: Any,
    ):
        return self._service.shortest_path(
            source_native_id=source_native_id,
            target_native_id=target_native_id,
            source=source,
            max_depth=max_depth,
        )


__all__ = [
    "GraphitiQueryService",
    "CursorTool",
    "GraphitiCursorToolset",
]

