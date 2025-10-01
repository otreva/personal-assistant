"""Operational helpers such as backup and restore of local state."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import tarfile
import tempfile
from typing import Iterable

from .state import GraphitiStateStore


def create_state_backup(
    state_store: GraphitiStateStore,
    *,
    destination: Path | str | None = None,
    timestamp: datetime | None = None,
) -> Path:
    """Create a compressed archive of the state directory and return the path."""

    state_dir = state_store.ensure_directory()
    base_destination = Path(destination) if destination else Path.cwd()
    if base_destination.is_dir():
        ts = (timestamp or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
        archive_path = base_destination / f"graphiti-state-{ts}.tar.gz"
    else:
        archive_path = base_destination
        archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(state_dir, arcname=state_dir.name)
    return archive_path


def restore_state_backup(
    state_store: GraphitiStateStore,
    archive_path: Path | str,
) -> Path:
    """Restore state from a previously created archive."""

    archive = Path(archive_path)
    if not archive.exists():
        raise FileNotFoundError(f"Backup archive not found: {archive}")

    target_dir = state_store.base_dir
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:gz") as tar:
        members = _validated_members(tar.getmembers())
        with tempfile.TemporaryDirectory(dir=target_dir.parent) as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            tar.extractall(path=temp_dir, members=members, filter="data")
            extracted = temp_dir / target_dir.name
            if not extracted.exists():
                raise ValueError("Archive does not contain expected state directory")
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.move(str(extracted), target_dir)

    state_store.ensure_directory()
    _normalise_permissions(target_dir)
    return target_dir


def _validated_members(members: Iterable[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    validated: list[tarfile.TarInfo] = []
    for member in members:
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError("Unsafe path detected in archive")
        validated.append(member)
    return validated


def _normalise_permissions(path: Path) -> None:
    path.chmod(0o700)
    for child in path.rglob("*"):
        try:
            if child.is_dir():
                child.chmod(0o700)
            elif child.is_file():
                child.chmod(0o600)
        except PermissionError:  # pragma: no cover - defensive
            continue


__all__ = ["create_state_backup", "restore_state_backup"]

