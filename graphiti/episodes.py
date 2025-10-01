"""Episode data model and persistence helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional


@dataclass(slots=True)
class Episode:
    """Canonical episode representation for Graphiti."""

    group_id: str
    source: str
    native_id: str
    version: str
    valid_at: datetime
    invalid_at: Optional[datetime] = None
    text: Optional[str] = None
    json: Optional[Mapping[str, Any]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def episode_id(self) -> str:
        return f"{self.source}:{self.native_id}:{self.version}"

    def to_properties(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "group_id": self.group_id,
            "source": self.source,
            "native_id": self.native_id,
            "version": self.version,
            "episode_id": self.episode_id(),
            "valid_at": self.valid_at.isoformat(),
            "invalid_at": self.invalid_at.isoformat() if self.invalid_at else None,
            "metadata_json": json.dumps(dict(self.metadata)),
        }
        if self.text is not None:
            payload["text"] = self.text
        if self.json is not None:
            payload["json_data"] = json.dumps(dict(self.json))
        return payload


class Neo4jEpisodeStore:
    """Persistence layer backed by a Neo4j driver."""

    def __init__(self, driver: Any, *, group_id: str):
        self._driver = driver
        self._group_id = group_id

    def upsert_episode(self, episode: Episode) -> None:
        """Insert or update an episode (overwrites existing with same source:native_id)."""

        if episode.group_id != self._group_id:
            raise ValueError(
                f"Episode group_id {episode.group_id!r} does not match store group {self._group_id!r}"
            )

        with self._driver.session() as session:
            session.execute_write(self._write_episode, episode)

    @property
    def group_id(self) -> str:
        return self._group_id

    def fetch_latest_episode_by_native_id(self, source: str, native_id: str) -> Optional[Dict[str, Any]]:
        with self._driver.session() as session:
            result = session.execute_read(
                self._fetch_latest, {
                    "group_id": self._group_id,
                    "source": source,
                    "native_id": native_id,
                }
            )
        return result

    @staticmethod
    def _write_episode(tx, episode: Episode) -> None:  # pragma: no cover - executed via driver mocks
        properties = episode.to_properties()
        # Use source + native_id as the unique key (ignore version for deduplication)
        unique_key = f"{episode.source}:{episode.native_id}"
        tx.run(
            """
            MERGE (g:Group {group_id: $group_id})
            MERGE (g)-[:HAS_EPISODE]->(e:Episode {group_id: $group_id, source: $source, native_id: $native_id})
            SET e = $properties
            """,
            group_id=episode.group_id,
            source=episode.source,
            native_id=episode.native_id,
            properties=properties,
        )

    def _invalidate_previous_version(self, tx, episode: Episode) -> None:  # pragma: no cover - executed via driver mocks
        tx.run(
            """
            MATCH (e:Episode {group_id: $group_id, source: $source, native_id: $native_id})
            WHERE e.episode_id <> $episode_id AND (e.invalid_at IS NULL OR e.invalid_at = "")
            SET e.invalid_at = $valid_at
            """,
            group_id=episode.group_id,
            source=episode.source,
            native_id=episode.native_id,
            episode_id=episode.episode_id(),
            valid_at=episode.valid_at.isoformat(),
        )

    @staticmethod
    def _fetch_latest(tx, params: Mapping[str, Any]) -> Optional[Dict[str, Any]]:  # pragma: no cover - executed via driver mocks
        record = tx.run(
            """
            MATCH (e:Episode {group_id: $group_id, source: $source, native_id: $native_id})
            RETURN e ORDER BY e.valid_at DESC LIMIT 1
            """,
            **params,
        ).single()
        if not record:
            return None
        node = record[0]
        if hasattr(node, "_properties"):
            return dict(node._properties)
        return dict(node)


__all__ = ["Episode", "Neo4jEpisodeStore"]
