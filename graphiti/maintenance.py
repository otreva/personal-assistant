"""Background maintenance helpers for backups and scheduled tasks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # pragma: no cover - available on Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback when zoneinfo unavailable
    ZoneInfo = None  # type: ignore[assignment]

from .config import ConfigStore, GraphitiConfig
from .logs import GraphitiLogStore
from .ops import create_state_backup, prune_backup_archives
from .state import GraphitiStateStore

BACKUP_TZ = ZoneInfo("America/New_York") if ZoneInfo else None
BACKUP_HOUR = 2


def next_backup_run(now: datetime) -> datetime:
    """Return the UTC timestamp for the next scheduled backup run."""

    if BACKUP_TZ is None:
        base = now if now.tzinfo else datetime.now(timezone.utc)
        target = base.replace(hour=BACKUP_HOUR, minute=0, second=0, microsecond=0)
        if base >= target:
            target += timedelta(days=1)
        return target

    local_now = now.astimezone(BACKUP_TZ)
    target_local = local_now.replace(hour=BACKUP_HOUR, minute=0, second=0, microsecond=0)
    if local_now >= target_local:
        target_local += timedelta(days=1)
    return target_local.astimezone(timezone.utc)


class BackupScheduler:
    """Co-ordinate daily backup creation and pruning with retention policies."""

    def __init__(
        self,
        *,
        state_store: GraphitiStateStore,
        config_store: ConfigStore,
        log_store: GraphitiLogStore,
    ) -> None:
        self._state_store = state_store
        self._config_store = config_store
        self._log_store = log_store
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())

    def update_log_store(self, log_store: GraphitiLogStore) -> None:
        """Swap the log store used for future backup runs."""

        self._log_store = log_store

    async def stop(self) -> None:
        if self._task is None or self._stop_event is None:
            return
        self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            self._stop_event = None

    async def trigger(self) -> Path | None:
        """Run a backup immediately, returning the archive path when successful."""

        config = self._config_store.load()
        return await self._run_backup(config)

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            config = self._config_store.load()
            now = datetime.now(timezone.utc)
            next_run = next_backup_run(now)
            wait_seconds = max((next_run - now).total_seconds(), 0)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                return
            except asyncio.TimeoutError:
                await self._run_backup(config)

    async def _run_backup(self, config: GraphitiConfig) -> Path | None:
        destination = Path(config.backup_directory).expanduser()
        retention = max(config.backup_retention_days, 0)
        log_retention = max(config.log_retention_days, 0)
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - filesystem errors
            self._log_store.append(
                "system",
                f"Failed to prepare backup directory: {exc}",
                level="ERROR",
                data={"destination": str(destination)},
                retention_days=log_retention,
            )
            return None

        try:
            archive = await asyncio.to_thread(
                create_state_backup, self._state_store, destination=destination
            )
            removed = await asyncio.to_thread(
                prune_backup_archives, destination, retention
            )
            timestamp = datetime.now(timezone.utc).isoformat()
            self._state_store.update_state(
                {
                    "backups": {
                        "last_run_at": timestamp,
                        "last_archive": str(archive),
                        "retention_days": retention,
                        "removed_archives": [str(path) for path in removed],
                    }
                }
            )
            self._log_store.append(
                "system",
                "State backup completed",
                data={
                    "archive": str(archive),
                    "removed": [path.name for path in removed],
                },
                retention_days=log_retention,
            )
            return archive
        except Exception as exc:  # pragma: no cover - defensive logging
            self._log_store.append(
                "system",
                f"Backup failed: {exc}",
                level="ERROR",
                data={"destination": str(destination)},
                retention_days=log_retention,
            )
            return None


__all__ = ["BackupScheduler", "next_backup_run"]
