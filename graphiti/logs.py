"""Lightweight structured logging utilities for the admin and pollers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Mapping
import json


@dataclass(frozen=True)
class LogRecord:
    """Structured representation of a persisted log entry."""

    timestamp: datetime
    level: str
    message: str
    data: Mapping[str, Any]

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "LogRecord":
        ts_raw = payload.get("timestamp")
        if not isinstance(ts_raw, str):
            raise ValueError("Log record missing timestamp")
        try:
            timestamp = datetime.fromisoformat(
                ts_raw.replace("Z", "+00:00") if ts_raw.endswith("Z") else ts_raw
            ).astimezone(timezone.utc)
        except ValueError as exc:  # pragma: no cover - defensive parsing
            raise ValueError(f"Invalid timestamp in log record: {ts_raw!r}") from exc
        level = str(payload.get("level", "INFO"))
        message = str(payload.get("message", ""))
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        return cls(timestamp=timestamp, level=level.upper(), message=message, data=data)

    def to_json(self) -> Mapping[str, Any]:
        return {
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
            "level": self.level,
            "message": self.message,
            "data": dict(self.data),
        }


class GraphitiLogStore:
    """Persist and retrieve structured log entries with retention controls."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def append(
        self,
        category: str,
        message: str,
        *,
        level: str = "INFO",
        data: Mapping[str, Any] | None = None,
        retention_days: int | None = None,
    ) -> LogRecord:
        record = LogRecord(
            timestamp=datetime.now(timezone.utc),
            level=level.upper(),
            message=message,
            data=dict(data or {}),
        )
        path = self._path_for_category(category)
        payload = record.to_json()
        line = json.dumps(payload, sort_keys=True)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if retention_days is not None:
                self._prune_file(path, retention_days)
        return record

    def tail(
        self,
        category: str,
        *,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[LogRecord]:
        path = self._path_for_category(category)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-max(limit, 0) :]
        records: list[LogRecord] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = LogRecord.from_json(payload)
            except Exception:  # pragma: no cover - defensive parsing
                continue
            if since and record.timestamp < since:
                continue
            records.append(record)
        return records

    def categories(self) -> list[str]:
        return sorted({path.stem for path in self.base_dir.glob("*.log")})

    def prune(self, retention_days: int) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(retention_days, 0))
        with self._lock:
            for path in self.base_dir.glob("*.log"):
                self._prune_file(path, retention_days, cutoff=cutoff)

    def _path_for_category(self, category: str) -> Path:
        safe = category.strip().lower() or "default"
        return self.base_dir / f"{safe}.log"

    def _prune_file(
        self,
        path: Path,
        retention_days: int,
        *,
        cutoff: datetime | None = None,
    ) -> None:
        if retention_days < 0:
            return
        cutoff_dt = cutoff or (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        )
        if retention_days == 0:
            path.unlink(missing_ok=True)
            return
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        kept: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = LogRecord.from_json(payload)
            except Exception:  # pragma: no cover - defensive
                continue
            if record.timestamp >= cutoff_dt:
                kept.append(json.dumps(record.to_json(), sort_keys=True))
        with path.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(kept))
            if kept:
                handle.write("\n")


__all__ = ["GraphitiLogStore", "LogRecord"]
