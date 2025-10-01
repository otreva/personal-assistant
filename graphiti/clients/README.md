# Google API Clients

This module contains real Google API client implementations that use OAuth tokens stored by the web admin.

## Overview

Previously, the system used "noop" (no-operation) stub clients that returned empty results. These have been replaced with real implementations that:

1. Load OAuth tokens from `~/.graphiti_sync/tokens.json`
2. Automatically refresh expired access tokens
3. Make authenticated API calls to Google services
4. Handle errors and edge cases gracefully

## Architecture

### Base Class: `GoogleAPIClient`

All Google clients inherit from this base class which provides:
- Token loading from state store
- Automatic token refresh using refresh tokens
- Credential management with `google.oauth2.credentials.Credentials`

### Implementations

#### `GmailClient`
- **Service**: Gmail API v1
- **Methods**:
  - `list_history(start_history_id)` - Incremental sync using history API
  - `fallback_fetch(newer_than_days)` - Fetch messages from last N days
  - `fetch_message(message_id)` - Get full message details
- **Error Handling**: Raises `GmailHistoryNotFound` when history ID is invalid

#### `DriveClient`
- **Service**: Drive API v3
- **Methods**:
  - `list_changes(page_token)` - Incremental sync using changes API
  - `fetch_file_content(file_id, metadata)` - Get file content and metadata
- **Features**:
  - Exports Google Docs/Sheets/Slides to plain text
  - Handles file metadata and content separately
  - Supports text file reading

#### `CalendarClient`
- **Service**: Calendar API v3
- **Methods**:
  - `list_events(calendar_id, sync_token)` - Incremental sync
  - `full_sync(calendar_id)` - Full sync for last 365 days
- **Error Handling**: Raises `CalendarSyncTokenExpired` when sync token is invalid

## OAuth Token Flow

### 1. Initial Setup (via Web Admin)
```
User → Web Admin → OAuth Flow → Google → Callback → Store Tokens
```

Tokens stored in `~/.graphiti_sync/tokens.json`:
```json
{
  "google": {
    "client_id": "...",
    "client_secret": "...",
    "refresh_token": "...",
    "access_token": "...",
    "access_token_expires_at": "2025-10-01T12:00:00Z",
    "scopes": [
      "https://www.googleapis.com/auth/gmail.readonly",
      "https://www.googleapis.com/auth/drive.readonly",
      "https://www.googleapis.com/auth/calendar.readonly"
    ],
    "updated_at": "2025-10-01T11:00:00Z"
  }
}
```

### 2. Token Usage in Clients
```python
# Client loads tokens
tokens = state_store.load_tokens()
google_tokens = tokens["google"]

# Create credentials
creds = Credentials(
    token=google_tokens["access_token"],
    refresh_token=google_tokens["refresh_token"],
    token_uri="https://oauth2.googleapis.com/token",
    client_id=client_id,
    client_secret=client_secret,
    scopes=google_tokens["scopes"]
)

# Auto-refresh if expired
if creds.expired and creds.refresh_token:
    creds.refresh(Request())
    # Update stored access token
```

### 3. API Calls
```python
# Build service with credentials
service = build("gmail", "v1", credentials=creds)

# Make authenticated API calls
messages = service.users().messages().list(userId="me").execute()
```

## Integration with CLI

The `cli.py` module creates clients using factory functions:

```python
def create_gmail_client(config, state):
    """Create real Gmail client or fall back to noop."""
    try:
        from .clients.google import GmailClient
        return GmailClient(state, config.google_client_id, config.google_client_secret)
    except (ImportError, ValueError) as exc:
        print(f"Warning: Using noop Gmail client ({exc})")
        return _NoopGmailClient()
```

**Fallback Logic:**
- If Google client libraries aren't installed → noop client
- If OAuth tokens are missing → noop client (with warning)
- Otherwise → real client with OAuth authentication

## Dependencies

Added to `requirements.txt`:
```
google-auth>=2.30.0
google-auth-oauthlib>=1.2.0
google-auth-httplib2>=0.2.0
google-api-python-client>=2.130.0
```

## Usage

### From Web Admin
1. Configure Google OAuth credentials
2. Complete OAuth flow
3. Run backfills or pollers
4. Clients automatically use stored tokens

### From CLI
```python
from graphiti.cli import create_gmail_client
from graphiti.config import load_config
from graphiti.state import GraphitiStateStore

config = load_config()
state = GraphitiStateStore()
client = create_gmail_client(config, state)

# Use client
history = client.list_history(start_history_id="12345")
```

## Error Handling

### Token Errors
- **Missing refresh token**: `ValueError` raised, falls back to noop
- **Invalid credentials**: `ValueError` raised with helpful message
- **Token refresh fails**: Exception propagated from `google.auth`

### API Errors
- **Gmail history not found**: `GmailHistoryNotFound` exception
- **Calendar sync token expired**: `CalendarSyncTokenExpired` exception
- **Drive API errors**: Exception propagated with context

### Network Errors
- All network errors from Google API propagate up
- Pollers should handle transient failures gracefully
- State store ensures no data loss on failure

## Testing

### Manual Testing
1. Start web admin: `docker-compose up`
2. Navigate to <http://localhost:8128>
3. Configure and authenticate Google OAuth
4. Run manual backfills from Operations tab
5. Check logs for successful data retrieval

### Verification
```python
# Check stored tokens
from graphiti.state import GraphitiStateStore
state = GraphitiStateStore()
tokens = state.load_tokens()
print(tokens.get("google", {}).get("refresh_token"))  # Should exist

# Test client creation
from graphiti.cli import create_gmail_client
from graphiti.config import load_config
client = create_gmail_client(load_config(), state)
print(type(client))  # Should be GmailClient, not _NoopGmailClient
```

## Security Considerations

1. **Token Storage**: Tokens stored in `~/.graphiti_sync/tokens.json` with user-only permissions
2. **Token Encryption**: Consider encrypting at rest (future enhancement)
3. **Scope Limiting**: Only request readonly scopes
4. **Token Rotation**: Refresh tokens automatically refreshed
5. **Client Secrets**: Never commit `client_secret` to version control

## Troubleshooting

### "Using noop client" warnings
- **Cause**: Google client libraries not installed or tokens missing
- **Fix**: Run `pip install -r requirements.txt` and complete OAuth flow

### "No Google refresh token found"
- **Cause**: OAuth flow not completed
- **Fix**: Go to web admin → Google Workspace → Sign in with Google

### "Token has been expired or revoked"
- **Cause**: Refresh token revoked or expired
- **Fix**: Re-authenticate via web admin

### Zero processed items
- **Before**: Noop clients always return 0 items
- **After**: Real clients fetch actual data from Google APIs
- **Verify**: Check logs for API errors or network issues

## Migration Notes

### Before (Noop Clients)
```python
class _NoopGmailClient:
    def list_history(self, start_history_id):
        return GmailHistoryResult(message_ids=[], latest_history_id="noop")
```
- Always returned empty results
- No API calls made
- Used for testing/scaffolding

### After (Real Clients)
```python
class GmailClient(GoogleAPIClient):
    def list_history(self, start_history_id):
        response = self._get_service().users().history().list(
            userId="me",
            startHistoryId=start_history_id
        ).execute()
        # Returns real message IDs from Gmail
```
- Makes authenticated API calls
- Returns actual data
- Production-ready implementation

## Future Enhancements

1. **Rate Limiting**: Implement exponential backoff for API quota
2. **Batch Requests**: Use batch API for multiple calls
3. **Caching**: Cache frequently accessed metadata
4. **Monitoring**: Add metrics for API call counts/errors
5. **Retry Logic**: Add automatic retries for transient failures
6. **Token Encryption**: Encrypt tokens at rest
7. **Multiple Accounts**: Support multiple Google accounts

