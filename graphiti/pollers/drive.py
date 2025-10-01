from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Protocol

from ..config import GraphitiConfig, load_config
from ..episodes import Episode, Neo4jEpisodeStore
from ..hooks import EpisodeProcessor
from ..state import GraphitiStateStore
from ..utils import sleep_with_jitter


@dataclass(slots=True)
class DriveChangesResult:
    changes: Iterable[Mapping[str, object]]
    new_page_token: str


@dataclass(slots=True)
class DriveFileContent:
    text: str | None
    metadata: Mapping[str, object]


class DriveClient(Protocol):  # pragma: no cover - protocol definition
    def list_changes(self, page_token: str | None) -> DriveChangesResult: ...

    def fetch_file_content(self, file_id: str, file_metadata: Mapping[str, object]) -> DriveFileContent: ...


class DrivePoller:
    """Poll Google Drive for changes and emit episodes."""

    def __init__(
        self,
        drive_client: DriveClient,
        episode_store: Neo4jEpisodeStore,
        state_store: GraphitiStateStore,
        config: GraphitiConfig | None = None,
    ) -> None:
        self._drive = drive_client
        self._episodes = episode_store
        self._state = state_store
        self._config = config or load_config()
        if self._episodes.group_id != self._config.group_id:
            raise ValueError("Episode store group_id does not match configuration group_id")
        self._group_id = self._config.group_id
        self._processor = EpisodeProcessor(self._config)

    def run_once(self) -> int:
        state = self._state.load_state()
        drive_state = state.get("drive", {}) if isinstance(state, Mapping) else {}
        page_token = drive_state.get("page_token") if isinstance(drive_state, Mapping) else None

        result = self._drive.list_changes(page_token)
        processed = 0
        for change in result.changes:
            episode = self._normalize_change(change)
            if episode is None:
                continue
            self._episodes.upsert_episode(self._processor.process(episode))
            processed += 1

        self._state.update_state(
            {
                "drive": {
                    "page_token": result.new_page_token,
                    "last_run_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        )
        return processed

    def backfill(self, newer_than_days: int | None = None) -> int:
        """Fetch Drive changes in a historical window and ingest episodes."""

        days = max(int(newer_than_days or self._config.drive_backfill_days), 1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        processed = 0
        page_token: str | None = None
        guard = 0

        while guard < 200:
            guard += 1
            result = self._backfill_page(page_token, days)
            if not result.changes:
                break
            for change in result.changes:
                episode = self._normalize_change(change)
                if episode is None:
                    continue
                if episode.valid_at and episode.valid_at < cutoff:
                    continue
                self._episodes.upsert_episode(self._processor.process(episode))
                processed += 1
            next_token = result.new_page_token
            if not next_token or next_token == page_token:
                page_token = next_token
                break
            page_token = next_token
            sleep_with_jitter(0.5, 0.3)

        payload = {
            "drive": {
                "page_token": page_token,
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "backfilled_days": days,
                "backfill_ran_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        self._state.update_state(payload)
        return processed

    def _backfill_page(self, page_token: str | None, days: int) -> DriveChangesResult:
        fetcher = getattr(self._drive, "backfill_changes", None)
        if callable(fetcher):
            try:
                return fetcher(days, page_token)
            except TypeError:
                return fetcher(days=days, page_token=page_token)
        return self._drive.list_changes(page_token)

    def _normalize_change(self, change: Mapping[str, object]) -> Episode | None:
        file_id = change.get("fileId")
        if not isinstance(file_id, str):  # pragma: no cover - defensive
            return None

        removed = bool(change.get("removed"))
        file_metadata = change.get("file") if isinstance(change.get("file"), Mapping) else None
        change_time = change.get("time")

        if removed or (isinstance(file_metadata, Mapping) and file_metadata.get("trashed")):
            timestamp = self._parse_time(change_time) or datetime.now(timezone.utc)
            version = f"deleted:{timestamp.isoformat()}"
            metadata = {
                "file_id": file_id,
                "tombstone": True,
            }
            return Episode(
                group_id=self._group_id,
                source="gdrive",
                native_id=file_id,
                version=version,
                valid_at=timestamp,
                json={"deleted": True},
                metadata=metadata,
            )

        if not isinstance(file_metadata, Mapping):
            return None

        modified_time = file_metadata.get("modifiedTime")
        valid_at = self._parse_time(modified_time) or self._parse_time(change_time)
        if valid_at is None:
            valid_at = datetime.now(timezone.utc)
        version = str(file_metadata.get("headRevisionId") or file_metadata.get("modifiedTime") or valid_at.isoformat())

        content = self._drive.fetch_file_content(file_id, file_metadata)
        text = content.text
        revision_id = file_metadata.get("headRevisionId") or file_metadata.get("revisionId")
        metadata = {
            "file_id": file_id,
            "name": file_metadata.get("name"),
            "mimeType": file_metadata.get("mimeType"),
            "webViewLink": file_metadata.get("webViewLink"),
            "url": file_metadata.get("webViewLink") or file_metadata.get("webContentLink"),
        }
        metadata.update({k: v for k, v in content.metadata.items()})
        if revision_id and "revisionId" not in metadata:
            metadata["revisionId"] = revision_id
        owners = metadata.get("owners") or file_metadata.get("owners")
        if owners is not None:
            metadata["owners"] = owners

        return Episode(
            group_id=self._group_id,
            source="gdrive",
            native_id=file_id,
            version=version,
            valid_at=valid_at,
            text=text,
            metadata=metadata,
        )

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value).astimezone(timezone.utc)
        except ValueError:  # pragma: no cover - defensive
            return None


__all__ = ["DrivePoller", "DriveClient", "DriveChangesResult", "DriveFileContent"]
