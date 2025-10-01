"""Local state directory manager."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping
import json
import os
from datetime import datetime, timezone

STATE_DIR_NAME = ".graphiti_sync"
TOKENS_FILE = "tokens.json"
STATE_FILE = "state.json"


def _ensure_mode(path: Path, mode: int) -> None:
    """Ensure the file at *path* has the provided permission bits."""

    if path.exists():
        os.chmod(path, mode)


@dataclass
class GraphitiStateStore:
    """Manage the on-disk state required for pollers and auth tokens."""

    base_dir: Path = field(default_factory=lambda: Path.home() / STATE_DIR_NAME)

    def __post_init__(self) -> None:
        self.ensure_directory()

    def ensure_directory(self) -> Path:
        """Ensure the state directory exists with the proper permissions."""

        self.base_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.base_dir, 0o700)
        return self.base_dir

    # ---- tokens management ----
    @property
    def tokens_path(self) -> Path:
        return self.base_dir / TOKENS_FILE

    @property
    def state_path(self) -> Path:
        return self.base_dir / STATE_FILE

    def load_tokens(self) -> Dict[str, Any]:
        if not self.tokens_path.exists():
            return {}
        with self.tokens_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_tokens(self, tokens: Mapping[str, Any]) -> None:
        self._write_json(self.tokens_path, tokens)

    def load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {}
        with self.state_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_state(self, state: Mapping[str, Any]) -> None:
        self._write_json(self.state_path, state)

    def update_state(self, update: Mapping[str, Any]) -> Dict[str, Any]:
        current = self.load_state()
        merged = _deep_merge(current, update)
        self.save_state(merged)
        return merged

    def record_error(self, source: str, message: str | None = None) -> Dict[str, Any]:
        if not source:
            raise ValueError("source must be provided")
        state = self.load_state()
        source_state = state.get(source) if isinstance(state.get(source), Mapping) else {}
        try:
            current_count = int(source_state.get("error_count", 0))
        except (TypeError, ValueError):
            current_count = 0
        payload: Dict[str, Any] = {
            source: {
                "error_count": current_count + 1,
                "last_error_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        if message:
            payload[source]["last_error_message"] = message
        return self.update_state(payload)

    def clear_errors(self, source: str) -> Dict[str, Any]:
        if not source:
            raise ValueError("source must be provided")
        state = self.load_state()
        current = state.get(source)
        if not isinstance(current, Mapping):
            return state
        cleaned = dict(current)
        cleaned.pop("error_count", None)
        cleaned.pop("last_error_at", None)
        cleaned.pop("last_error_message", None)
        new_state = dict(state)
        new_state[source] = cleaned
        self.save_state(new_state)
        return new_state

    def _write_json(self, path: Path, data: Mapping[str, Any]) -> None:
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
        _ensure_mode(path, 0o600)


def _deep_merge(base: MutableMapping[str, Any], update: Mapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), Mapping):
            base[key] = _deep_merge(dict(base[key]), value)  # type: ignore[index]
        else:
            base[key] = value  # type: ignore[index]
    return base


__all__ = ["GraphitiStateStore"]
