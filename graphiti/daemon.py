"""Background daemon for continuous polling."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Any

from .cli import (
    close_episode_store,
    create_calendar_poller,
    create_drive_poller,
    create_episode_store,
    create_gmail_poller,
    create_slack_client,
)
from .config import load_config
from .pollers.slack import SlackPoller
from .state import GraphitiStateStore

logger = logging.getLogger(__name__)


class PollerDaemon:
    """Background daemon that runs pollers on configurable intervals."""

    def __init__(self) -> None:
        self.config = load_config()
        self.state_store = GraphitiStateStore()
        self.running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown on SIGTERM/SIGINT."""
        def shutdown_handler(signum: int, frame: Any) -> None:
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self.running = False

        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

    async def run_google_pollers(self) -> dict[str, int]:
        """Run Gmail, Drive, and Calendar pollers sequentially."""
        metrics = {}
        episode_store = create_episode_store(self.config)
        try:
            # Reload config in case it changed
            self.config = load_config()
            
            # Run Google pollers sequentially (share OAuth credentials)
            gmail_poller = create_gmail_poller(self.config, self.state_store, episode_store)
            metrics["gmail"] = await asyncio.to_thread(gmail_poller.run_once)
            
            drive_poller = create_drive_poller(self.config, self.state_store, episode_store)
            metrics["drive"] = await asyncio.to_thread(drive_poller.run_once)
            
            calendar_poller = create_calendar_poller(self.config, self.state_store, episode_store)
            metrics["calendar"] = await asyncio.to_thread(calendar_poller.run_once)
            
            logger.info(f"Google pollers completed: {metrics}")
        except Exception as e:
            logger.exception(f"Error in Google pollers: {e}")
        finally:
            close_episode_store(episode_store)
        
        return metrics

    async def run_slack_poller(self) -> dict[str, int]:
        """Run Slack poller."""
        metrics = {}
        episode_store = create_episode_store(self.config)
        try:
            # Reload config in case it changed
            self.config = load_config()
            
            slack_client = create_slack_client(self.config, self.state_store)
            slack_poller = SlackPoller(slack_client, episode_store, self.state_store, config=self.config)
            metrics["slack"] = await asyncio.to_thread(slack_poller.run_once)
            
            logger.info(f"Slack poller completed: {metrics}")
        except Exception as e:
            logger.exception(f"Error in Slack poller: {e}")
        finally:
            close_episode_store(episode_store)
        
        return metrics

    async def google_poller_loop(self) -> None:
        """Continuous loop for Google pollers."""
        logger.info(f"Starting Google poller loop (interval: {self.config.poll_gmail_drive_calendar_seconds}s)")
        
        while self.running:
            try:
                await self.run_google_pollers()
            except Exception as e:
                logger.exception(f"Unexpected error in Google poller loop: {e}")
            
            # Sleep for the configured interval
            for _ in range(self.config.poll_gmail_drive_calendar_seconds):
                if not self.running:
                    break
                await asyncio.sleep(1)

    async def slack_poller_loop(self) -> None:
        """Continuous loop for Slack poller."""
        logger.info(f"Starting Slack poller loop (active: {self.config.poll_slack_active_seconds}s, idle: {self.config.poll_slack_idle_seconds}s)")
        
        # Determine if we should use active or idle interval
        # For now, always use active interval - could be enhanced to detect activity
        interval = self.config.poll_slack_active_seconds
        
        while self.running:
            try:
                await self.run_slack_poller()
            except Exception as e:
                logger.exception(f"Unexpected error in Slack poller loop: {e}")
            
            # Sleep for the configured interval
            for _ in range(interval):
                if not self.running:
                    break
                await asyncio.sleep(1)

    async def start(self) -> None:
        """Start the daemon and run all pollers."""
        self.running = True
        logger.info(f"Starting poller daemon at {datetime.now(timezone.utc).isoformat()}")
        logger.info(f"Configuration:")
        logger.info(f"  - Google pollers interval: {self.config.poll_gmail_drive_calendar_seconds}s")
        logger.info(f"  - Slack active interval: {self.config.poll_slack_active_seconds}s")
        logger.info(f"  - Slack idle interval: {self.config.poll_slack_idle_seconds}s")
        
        # Create tasks for both poller loops
        google_task = asyncio.create_task(self.google_poller_loop())
        slack_task = asyncio.create_task(self.slack_poller_loop())
        self._tasks = [google_task, slack_task]
        
        # Wait for shutdown signal
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Poller tasks cancelled")
        
        logger.info("Poller daemon stopped")

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        logger.info("Stopping poller daemon...")
        self.running = False
        
        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("All poller tasks stopped")


async def main() -> None:
    """Main entry point for the daemon."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
    
    daemon = PollerDaemon()
    
    try:
        await daemon.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        await daemon.stop()


if __name__ == "__main__":
    asyncio.run(main())


__all__ = ["PollerDaemon", "main"]

