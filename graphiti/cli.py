"""Command line interface for Graphiti."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .config import GraphitiConfig, load_config
from .episodes import Neo4jEpisodeStore
from .health import collect_health_metrics, format_dashboard
from .ops import create_state_backup, restore_state_backup
from .pollers.calendar import CalendarPoller
from .pollers.drive import DrivePoller
from .pollers.gmail import GmailPoller
from .pollers.slack import SlackPoller
from .state import GraphitiStateStore

DEFAULT_INDENT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphiti")
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser(
        "status", help="Display configuration and state directory information."
    )
    status_parser.set_defaults(func=cmd_status)

    sync = sub.add_parser("sync", help="Synchronisation utilities")
    sync_sub = sync.add_subparsers(dest="sync_command", required=True)

    for source in ("gmail", "drive", "calendar"):
        poller_parser = sync_sub.add_parser(source, help=f"Run the {source} poller once")
        poller_parser.add_argument(
            "--once",
            action="store_true",
            required=True,
            help="Execute a single polling iteration",
        )
        poller_parser.set_defaults(func=cmd_sync_poller, poller_name=source)

    slack_parser = sync_sub.add_parser("slack", help="Slack poller utilities")
    group = slack_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="Run the Slack poller once")
    group.add_argument(
        "--list-channels",
        action="store_true",
        help="List discoverable Slack channels and persist metadata",
    )
    slack_parser.set_defaults(func=cmd_sync_slack)

    sync_status = sync_sub.add_parser(
        "status", help="Summarise the last recorded sync checkpoints"
    )
    sync_status.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON instead of the textual dashboard",
    )
    sync_status.set_defaults(func=cmd_sync_status)

    scheduler = sync_sub.add_parser("scheduler", help="Run the scheduler stub")
    scheduler.add_argument(
        "--once",
        action="store_true",
        help="Execute each poller sequentially once",
    )
    scheduler.set_defaults(func=cmd_sync_scheduler)

    backup = sub.add_parser("backup", help="Backup utilities")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    backup_state = backup_sub.add_parser("state", help="Create a state backup archive")
    backup_state.add_argument(
        "--output",
        type=str,
        help="Directory or file path for the backup archive",
    )
    backup_state.set_defaults(func=cmd_backup_state)

    restore = sub.add_parser("restore", help="Restore utilities")
    restore_sub = restore.add_subparsers(dest="restore_command", required=True)
    restore_state = restore_sub.add_parser("state", help="Restore state from an archive")
    restore_state.add_argument(
        "archive",
        type=str,
        help="Path to a backup archive created via `backup state`",
    )
    restore_state.set_defaults(func=cmd_restore_state)

    return parser


def _bootstrap() -> tuple[GraphitiConfig, GraphitiStateStore]:
    config = load_config()
    state = GraphitiStateStore()
    state.ensure_directory()
    return config, state


def cmd_status(_: argparse.Namespace) -> int:
    config, state = _bootstrap()
    payload: dict[str, Any] = {
        "config": {
            "neo4j_uri": config.neo4j_uri,
            "neo4j_user": config.neo4j_user,
            "group_id": config.group_id,
            "poll_gmail_drive_calendar_seconds": config.poll_gmail_drive_calendar_seconds,
            "poll_slack_active_seconds": config.poll_slack_active_seconds,
            "poll_slack_idle_seconds": config.poll_slack_idle_seconds,
            "gmail_fallback_days": config.gmail_fallback_days,
            "gmail_backfill_days": config.gmail_backfill_days,
            "drive_backfill_days": config.drive_backfill_days,
            "calendar_backfill_days": config.calendar_backfill_days,
            "slack_backfill_days": config.slack_backfill_days,
            "calendar_ids": list(config.calendar_ids),
            "slack_channel_allowlist": list(config.slack_channel_allowlist),
            "backup_directory": config.backup_directory,
            "backup_retention_days": config.backup_retention_days,
            "log_retention_days": config.log_retention_days,
            "logs_directory": config.logs_directory,
        },
        "state_directory": str(state.base_dir),
        "tokens_path_exists": state.tokens_path.exists(),
        "state_path_exists": state.state_path.exists(),
    }
    print(json.dumps(payload, indent=DEFAULT_INDENT, sort_keys=True))
    return 0


def cmd_sync_status(args: argparse.Namespace) -> int:
    config, state = _bootstrap()
    metrics = collect_health_metrics(state, config)
    if getattr(args, "json", False):
        print(json.dumps(metrics, indent=DEFAULT_INDENT, sort_keys=True))
    else:
        print(format_dashboard(metrics))
    return 0


def cmd_sync_poller(args: argparse.Namespace) -> int:
    config, state = _bootstrap()
    episode_store = create_episode_store(config)
    poller_factory = POLLER_FACTORIES[args.poller_name]
    try:
        poller = poller_factory(config, state, episode_store)
        processed = poller.run_once()
    finally:
        close_episode_store(episode_store)

    payload = {
        "source": args.poller_name,
        "processed": processed,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(payload, indent=DEFAULT_INDENT, sort_keys=True))
    return 0


def cmd_sync_slack(args: argparse.Namespace) -> int:
    config, state = _bootstrap()
    episode_store = create_episode_store(config)
    client = create_slack_client(config, state)

    try:
        if getattr(args, "list_channels", False):
            channels = list(client.list_channels())
            filtered = _filter_channels(channels, config.slack_channel_allowlist)
            state.update_state(
                {
                    "slack": {
                        "channels": {
                            str(c.get("id")): {"metadata": dict(c)}
                            for c in filtered
                            if isinstance(c, Mapping) and c.get("id")
                        },
                        "last_inventory_at": datetime.now(timezone.utc).isoformat(),
                    }
                }
            )
            print(json.dumps(filtered, indent=DEFAULT_INDENT, sort_keys=True))
            return 0

        poller = SlackPoller(
            client,
            episode_store,
            state,
            allowlist=config.slack_channel_allowlist,
        )
        processed = poller.run_once()
        payload = {
            "source": "slack",
            "processed": processed,
            "ran_at": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(payload, indent=DEFAULT_INDENT, sort_keys=True))
        return 0
    finally:
        close_episode_store(episode_store)


def cmd_sync_scheduler(args: argparse.Namespace) -> int:
    config, state = _bootstrap()
    if not getattr(args, "once", False):
        payload = {
            "poll_gmail_drive_calendar_seconds": config.poll_gmail_drive_calendar_seconds,
            "poll_slack_active_seconds": config.poll_slack_active_seconds,
            "poll_slack_idle_seconds": config.poll_slack_idle_seconds,
        }
        print(json.dumps(payload, indent=DEFAULT_INDENT, sort_keys=True))
        return 0

    metrics: list[dict[str, Any]] = []
    episode_store = create_episode_store(config)
    try:
        for name in ("gmail", "drive", "calendar"):
            poller = POLLER_FACTORIES[name](config, state, episode_store)
            metrics.append(
                {
                    "source": name,
                    "processed": poller.run_once(),
                }
            )
        slack_client = create_slack_client(config, state)
        slack_poller = SlackPoller(
            slack_client,
            episode_store,
            state,
            allowlist=config.slack_channel_allowlist,
        )
        metrics.append({"source": "slack", "processed": slack_poller.run_once()})
    finally:
        close_episode_store(episode_store)

    payload = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }
    print(json.dumps(payload, indent=DEFAULT_INDENT, sort_keys=True))
    return 0


def cmd_backup_state(args: argparse.Namespace) -> int:
    _, state = _bootstrap()
    destination = Path(args.output) if getattr(args, "output", None) else None
    archive = create_state_backup(state, destination=destination)
    payload = {"backup_path": str(archive)}
    print(json.dumps(payload, indent=DEFAULT_INDENT, sort_keys=True))
    return 0


def cmd_restore_state(args: argparse.Namespace) -> int:
    _, state = _bootstrap()
    archive = Path(args.archive)
    restored = restore_state_backup(state, archive)
    payload = {
        "restored_from": str(archive),
        "state_path": str(restored),
    }
    print(json.dumps(payload, indent=DEFAULT_INDENT, sort_keys=True))
    return 0


def _filter_channels(
    channels: Iterable[Mapping[str, Any]],
    allowlist: Iterable[str] | None,
) -> list[Mapping[str, Any]]:
    allow = {channel.lower() for channel in allowlist or ()}
    if not allow:
        return [dict(channel) for channel in channels]
    filtered: list[Mapping[str, Any]] = []
    for channel in channels:
        channel_id = str(channel.get("id", "")).lower()
        name = str(channel.get("name", "")).lower()
        if channel_id in allow or name in allow:
            filtered.append(dict(channel))
    return filtered


def create_episode_store(config: GraphitiConfig) -> Neo4jEpisodeStore:
    driver = create_neo4j_driver(config)
    return Neo4jEpisodeStore(driver, group_id=config.group_id)


def create_neo4j_driver(config: GraphitiConfig):  # pragma: no cover - requires neo4j driver
    try:
        from neo4j import GraphDatabase  # type: ignore
    except ImportError as exc:  # pragma: no cover - executed when driver missing
        raise RuntimeError(
            "The neo4j Python driver is required to use the CLI sync commands"
        ) from exc
    return GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, config.neo4j_password),
    )


def close_episode_store(store: Neo4jEpisodeStore) -> None:
    driver = getattr(store, "_driver", None)
    if driver and hasattr(driver, "close"):
        driver.close()


def create_gmail_poller(
    config: GraphitiConfig,
    state: GraphitiStateStore,
    episode_store: Neo4jEpisodeStore,
) -> GmailPoller:
    client = create_gmail_client(config, state)
    return GmailPoller(client, episode_store, state, config)


def create_drive_poller(
    config: GraphitiConfig,
    state: GraphitiStateStore,
    episode_store: Neo4jEpisodeStore,
) -> DrivePoller:
    client = create_drive_client(config, state)
    return DrivePoller(client, episode_store, state, config)


def create_calendar_poller(
    config: GraphitiConfig,
    state: GraphitiStateStore,
    episode_store: Neo4jEpisodeStore,
) -> CalendarPoller:
    client = create_calendar_client(config, state)
    return CalendarPoller(client, episode_store, state, config.calendar_ids, config)


POLLER_FACTORIES: dict[str, Callable[[GraphitiConfig, GraphitiStateStore, Neo4jEpisodeStore], Any]] = {
    "gmail": create_gmail_poller,
    "drive": create_drive_poller,
    "calendar": create_calendar_poller,
}


# ---- default clients ----


@dataclass(slots=True)
class _NoopGmailClient:
    def list_history(self, start_history_id: str | None) -> Any:
        from .pollers.gmail import GmailHistoryResult

        return GmailHistoryResult(message_ids=[], latest_history_id=start_history_id or "noop")

    def fallback_fetch(self, newer_than_days: int) -> Any:
        from .pollers.gmail import GmailHistoryResult

        return GmailHistoryResult(message_ids=[], latest_history_id=f"noop:{newer_than_days}")

    def fetch_message(self, message_id: str) -> Mapping[str, Any]:  # pragma: no cover - defensive
        return {"id": message_id}


@dataclass(slots=True)
class _NoopDriveClient:
    def list_changes(self, page_token: str | None) -> Any:
        from .pollers.drive import DriveChangesResult

        return DriveChangesResult(changes=[], new_page_token=page_token or "noop")

    def backfill_changes(
        self, newer_than_days: int, page_token: str | None = None
    ) -> Any:
        from .pollers.drive import DriveChangesResult

        return DriveChangesResult(changes=[], new_page_token=page_token or "noop")

    def fetch_file_content(self, file_id: str, file_metadata: Mapping[str, Any]) -> Any:
        from .pollers.drive import DriveFileContent

        return DriveFileContent(text=None, metadata={})


@dataclass(slots=True)
class _NoopCalendarClient:
    def list_events(self, calendar_id: str, sync_token: str | None) -> Any:
        from .pollers.calendar import CalendarEventsPage

        token = sync_token or f"noop:{calendar_id}"
        return CalendarEventsPage(events=[], next_sync_token=token)

    def full_sync(self, calendar_id: str) -> Any:
        from .pollers.calendar import CalendarEventsPage

        return CalendarEventsPage(events=[], next_sync_token=f"noop:{calendar_id}")


def create_gmail_client(
    config: GraphitiConfig, state: GraphitiStateStore
) -> Any:  # pragma: no cover - default stub
    return _NoopGmailClient()


def create_drive_client(
    config: GraphitiConfig, state: GraphitiStateStore
) -> Any:  # pragma: no cover - default stub
    return _NoopDriveClient()


def create_calendar_client(
    config: GraphitiConfig, state: GraphitiStateStore
) -> Any:  # pragma: no cover - default stub
    return _NoopCalendarClient()


def create_slack_client(
    config: GraphitiConfig, state: GraphitiStateStore
) -> Any:  # pragma: no cover - default stub
    from .pollers.slack import NullSlackClient

    return NullSlackClient()


COMMAND_HANDLERS = {
    "status": cmd_status,
    "sync": lambda args: args.func(args),
    "backup": lambda args: args.func(args),
    "restore": lambda args: args.func(args),
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMAND_HANDLERS.get(args.command)
    if handler is None:
        parser.error(f"Unknown command: {args.command}")
    return handler(args)


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
