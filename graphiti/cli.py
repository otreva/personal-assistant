"""Command line interface for Graphiti."""
from __future__ import annotations

import argparse
import json
from typing import Any

from .config import load_config
from .state import GraphitiStateStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="graphiti")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Display configuration and state directory information.")
    return parser


def cmd_status(_: argparse.Namespace) -> int:
    config = load_config()
    state = GraphitiStateStore()
    state.ensure_directory()

    payload: dict[str, Any] = {
        "config": {
            "neo4j_uri": config.neo4j_uri,
            "neo4j_user": config.neo4j_user,
            "group_id": config.group_id,
            "poll_gmail_drive_calendar_seconds": config.poll_gmail_drive_calendar_seconds,
            "poll_slack_active_seconds": config.poll_slack_active_seconds,
            "poll_slack_idle_seconds": config.poll_slack_idle_seconds,
            "gmail_fallback_days": config.gmail_fallback_days,
        },
        "state_directory": str(state.base_dir),
        "tokens_path_exists": state.tokens_path.exists(),
        "state_path_exists": state.state_path.exists(),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


COMMAND_HANDLERS = {
    "status": cmd_status,
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
