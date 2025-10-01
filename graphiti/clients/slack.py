"""Slack client implementation using xoxc tokens and cookies."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import requests


@dataclass(slots=True)
class SlackClient:
    """Slack API client using xoxc token and cookie authentication."""

    token: str
    cookie: str
    base_url: str = "https://slack.com/api"
    timeout: int = 30

    def _make_request(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make authenticated request to Slack API.
        
        Args:
            endpoint: API endpoint (e.g., "search.messages")
            params: Query parameters for the request
            
        Returns:
            API response as dictionary
            
        Raises:
            requests.HTTPError: If the request fails
        """
        url = f"{self.base_url}/{endpoint}"
        
        # For xoxc tokens, use cookie-based authentication
        headers = {
            "Cookie": f"d={self.cookie}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        
        # Add token to params
        request_params = {"token": self.token}
        if params:
            request_params.update(params)
        
        response = requests.post(
            url,
            headers=headers,
            data=request_params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Check for Slack API errors
        if not data.get("ok"):
            error_msg = data.get("error", "unknown_error")
            if error_msg == "ratelimited":
                from ..pollers.slack import SlackRateLimited
                retry_after = data.get("retry_after", 1.0)
                raise SlackRateLimited(retry_after)
            raise RuntimeError(f"Slack API error: {error_msg}")
        
        return data

    def list_channels(self) -> Iterable[Mapping[str, object]]:
        """List all channels accessible by the user.
        
        Returns:
            Iterable of channel metadata dictionaries
        """
        cursor = None
        while True:
            params: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor
            
            response = self._make_request("conversations.list", params)
            channels = response.get("channels", [])
            
            for channel in channels:
                if isinstance(channel, dict):
                    yield channel
            
            cursor_value = response.get("response_metadata", {}).get("next_cursor")
            if not cursor_value:
                break
            cursor = cursor_value

    def search_messages(
        self,
        query: str,
        *,
        oldest: str | None = None,
        cursor: str | None = None,
    ) -> Mapping[str, object]:
        """Search for messages using Slack's search API.
        
        Args:
            query: Search query string
            oldest: Oldest timestamp to search from (Unix timestamp)
            cursor: Pagination cursor
            
        Returns:
            Search results with messages and pagination info
        """
        all_messages: list[dict[str, Any]] = []
        page = 1
        per_page = 100  # Slack max per page
        
        # Build query with timestamp filter
        search_query = query
        if oldest:
            from datetime import datetime
            # Convert Unix timestamp to YYYY-MM-DD format
            try:
                oldest_dt = datetime.fromtimestamp(float(oldest))
                search_query = f"{query} after:{oldest_dt.strftime('%Y-%m-%d')}"
            except (ValueError, TypeError):
                search_query = query
        
        # Fetch all pages
        while True:
            params: dict[str, Any] = {
                "query": search_query,
                "count": per_page,
                "page": page,
                "sort": "timestamp",
            }
            
            response = self._make_request("search.messages", params)
            if not response.get("ok"):
                break
            
            messages_data = response.get("messages", {}) or {}
            matches = messages_data.get("matches", []) or []
            if not matches:
                break
            
            all_messages.extend(matches)
            
            # Check if there are more pages
            pagination = messages_data.get("pagination", {}) or {}
            total_pages = pagination.get("page_count", 1)
            if page >= total_pages:
                break
            
            page += 1
            time.sleep(1.5)  # Rate limit between pages
        
        return {
            "messages": all_messages,
            "next_cursor": None,  # We fetched all pages
        }

    def fetch_message(self, channel_id: str, ts: str) -> Mapping[str, object]:
        """Fetch a specific message with full details.
        
        Args:
            channel_id: Channel ID
            ts: Message timestamp
            
        Returns:
            Message metadata
        """
        params = {
            "channel": channel_id,
            "latest": ts,
            "inclusive": "true",
            "limit": 1,
        }
        
        response = self._make_request("conversations.history", params)
        messages = response.get("messages", [])
        
        if messages and isinstance(messages[0], dict):
            return messages[0]
        return {}

    def resolve_user(self, user_id: str) -> Mapping[str, object] | None:
        """Resolve user information by ID.
        
        Args:
            user_id: Slack user ID
            
        Returns:
            User metadata or None if not found
        """
        try:
            response = self._make_request("users.info", {"user": user_id})
            user = response.get("user")
            if isinstance(user, dict):
                # Extract useful fields
                profile = user.get("profile", {})
                return {
                    "id": user_id,
                    "name": user.get("name", ""),
                    "real_name": user.get("real_name", ""),
                    "display_name": profile.get("display_name", ""),
                    "email": profile.get("email", ""),
                }
            return None
        except Exception:
            return None

    def resolve_channel(self, channel_id: str) -> Mapping[str, object] | None:
        """Resolve channel information by ID.
        
        Args:
            channel_id: Slack channel ID
            
        Returns:
            Channel metadata or None if not found
        """
        try:
            response = self._make_request("conversations.info", {"channel": channel_id})
            channel = response.get("channel")
            if isinstance(channel, dict):
                return {
                    "id": channel_id,
                    "name": channel.get("name", ""),
                    "is_private": channel.get("is_private", False),
                }
            return None
        except Exception:
            return None


__all__ = ["SlackClient"]

