"""OAuth session management and utilities."""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any, Mapping

GOOGLE_OAUTH_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)

GOOGLE_OAUTH_SESSION_TTL = 600

# In-memory OAuth session storage
_oauth_sessions: dict[str, dict[str, Any]] = {}


def purge_oauth_sessions() -> None:
    """Remove expired OAuth sessions."""
    now = time.time()
    expired = [
        state
        for state, payload in _oauth_sessions.items()
        if payload.get("expires_at", 0) <= now
    ]
    for state in expired:
        _oauth_sessions.pop(state, None)


def register_oauth_session(state: str, payload: Mapping[str, Any]) -> None:
    """Register a new OAuth session with expiry."""
    purge_oauth_sessions()
    data = dict(payload)
    data["expires_at"] = time.time() + GOOGLE_OAUTH_SESSION_TTL
    _oauth_sessions[state] = data


def pop_oauth_session(state: str) -> dict[str, Any] | None:
    """Pop and return an OAuth session if it exists and hasn't expired."""
    payload = _oauth_sessions.pop(state, None)
    if not payload:
        return None
    if payload.get("expires_at", 0) <= time.time():
        return None
    return payload


def normalise_scope_list(value: Any) -> list[str]:
    """Normalise various scope formats into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.replace(",", " ")
        return [scope for scope in cleaned.split() if scope]
    if isinstance(value, Mapping):
        return normalise_scope_list(value.get("scopes"))
    try:
        iterator = iter(value)  # type: ignore[arg-type]
    except TypeError:
        return []
    result: list[str] = []
    for entry in iterator:
        text = str(entry).strip()
        if text:
            result.append(text)
    return result


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code verifier and challenge pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def load_token_section(state_store, section: str) -> dict[str, Any]:
    """Load a token section from state storage."""
    tokens = state_store.load_tokens()
    payload = tokens.get(section)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def persist_token_section(
    state_store, section: str, values: Mapping[str, Any]
) -> dict[str, Any]:
    """Persist a token section to state storage."""
    tokens = state_store.load_tokens()
    merged = dict(tokens)
    merged[section] = dict(values)
    state_store.save_tokens(merged)
    return merged[section]


__all__ = [
    "GOOGLE_OAUTH_SCOPES",
    "GOOGLE_OAUTH_SESSION_TTL",
    "purge_oauth_sessions",
    "register_oauth_session",
    "pop_oauth_session",
    "normalise_scope_list",
    "generate_pkce_pair",
    "load_token_section",
    "persist_token_section",
]


