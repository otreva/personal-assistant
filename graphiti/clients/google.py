"""Google API client implementations using OAuth tokens."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..pollers.calendar import CalendarEventsPage, CalendarSyncTokenExpired
from ..pollers.drive import DriveChangesResult, DriveFileContent
from ..pollers.gmail import GmailHistoryNotFound, GmailHistoryResult
from ..state import GraphitiStateStore


class GoogleAPIClient:
    """Base class for Google API clients with OAuth token management."""

    def __init__(self, state_store: GraphitiStateStore, client_id: str, client_secret: str):
        self._state = state_store
        self._client_id = client_id
        self._client_secret = client_secret

    def _get_credentials(self) -> Credentials:
        """Load and refresh OAuth credentials from state store."""
        tokens = self._state.load_tokens()
        google_tokens = tokens.get("google", {})

        refresh_token = google_tokens.get("refresh_token")
        if not refresh_token:
            raise ValueError("No Google refresh token found. Please authenticate via the web admin.")

        # Create credentials object
        creds = Credentials(
            token=google_tokens.get("access_token"),
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._client_id,
            client_secret=self._client_secret,
            scopes=google_tokens.get("scopes", [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/calendar.readonly",
            ]),
        )

        # Refresh token if expired
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Update access token in state
                google_tokens["access_token"] = creds.token
                if creds.expiry:
                    google_tokens["access_token_expires_at"] = creds.expiry.isoformat()
                tokens["google"] = google_tokens
                self._state.save_tokens(tokens)

        return creds


class GmailClient(GoogleAPIClient):
    """Gmail API client implementation."""

    def __init__(self, state_store: GraphitiStateStore, client_id: str, client_secret: str):
        super().__init__(state_store, client_id, client_secret)
        self._service = None

    def _get_service(self):
        """Get or create Gmail API service."""
        if self._service is None:
            creds = self._get_credentials()
            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def list_history(self, start_history_id: str | None) -> GmailHistoryResult:
        """List message history since the given history ID."""
        if not start_history_id:
            raise GmailHistoryNotFound("No history ID provided")

        try:
            service = self._get_service()
            response = service.users().history().list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
            ).execute()

            message_ids = []
            if "history" in response:
                for history_record in response["history"]:
                    if "messagesAdded" in history_record:
                        for msg_added in history_record["messagesAdded"]:
                            if "message" in msg_added:
                                message_ids.append(msg_added["message"]["id"])

            latest_history_id = response.get("historyId", start_history_id)
            return GmailHistoryResult(
                message_ids=message_ids,
                latest_history_id=latest_history_id,
            )
        except Exception as exc:
            if "404" in str(exc) or "historyId" in str(exc).lower():
                raise GmailHistoryNotFound(str(exc)) from exc
            raise

    def fallback_fetch(self, newer_than_days: int) -> GmailHistoryResult:
        """Fetch messages from the last N days as a fallback."""
        service = self._get_service()
        cutoff = datetime.now(timezone.utc) - timedelta(days=newer_than_days)
        query = f"after:{cutoff.strftime('%Y/%m/%d')}"

        message_ids = []
        next_page_token = None

        while True:
            response = service.users().messages().list(
                userId="me",
                q=query,
                pageToken=next_page_token,
                maxResults=500,
            ).execute()

            if "messages" in response:
                message_ids.extend([msg["id"] for msg in response["messages"]])

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        # Get the latest history ID
        profile = service.users().getProfile(userId="me").execute()
        latest_history_id = profile.get("historyId", "unknown")

        return GmailHistoryResult(
            message_ids=message_ids,
            latest_history_id=latest_history_id,
        )

    def fetch_message(self, message_id: str) -> Mapping[str, Any]:
        """Fetch a single message by ID."""
        service = self._get_service()
        message = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()
        return message


class DriveClient(GoogleAPIClient):
    """Google Drive API client implementation."""

    def __init__(self, state_store: GraphitiStateStore, client_id: str, client_secret: str):
        super().__init__(state_store, client_id, client_secret)
        self._service = None

    def _get_service(self):
        """Get or create Drive API service."""
        if self._service is None:
            creds = self._get_credentials()
            self._service = build("drive", "v3", credentials=creds)
        return self._service

    def list_changes(self, page_token: str | None) -> DriveChangesResult:
        """List changes since the given page token."""
        service = self._get_service()

        # If no page token, get the start token
        if not page_token:
            response = service.changes().getStartPageToken().execute()
            page_token = response["startPageToken"]

        # List changes
        response = service.changes().list(
            pageToken=page_token,
            spaces="drive",
            fields="nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,modifiedTime,createdTime,trashed))",
        ).execute()

        changes = response.get("changes", [])
        new_page_token = response.get("newStartPageToken") or response.get("nextPageToken", page_token)

        return DriveChangesResult(
            changes=changes,
            new_page_token=new_page_token,
        )

    def backfill_changes(self, newer_than_days: int, page_token: str | None = None) -> DriveChangesResult:
        """List files modified in the last N days for backfill."""
        service = self._get_service()
        cutoff = datetime.now(timezone.utc) - timedelta(days=newer_than_days)
        
        # Query for files modified after the cutoff date
        query = f"modifiedTime > '{cutoff.isoformat()}'"
        
        # Note: page_token for files().list() is different from changes().list()
        # For backfill, we ignore the passed page_token (which is from changes API)
        # and use the files API pagination
        
        # List files matching the query
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,trashed,owners)",
            pageSize=100,
        ).execute()
        
        files = response.get("files", [])
        files_page_token = response.get("nextPageToken")
        
        # Convert files to change format
        changes = []
        for file in files:
            changes.append({
                "fileId": file["id"],
                "file": file,
                "time": file.get("modifiedTime"),
                "removed": False,
            })
        
        # Get current changes API page token for future incremental syncs
        token_response = service.changes().getStartPageToken().execute()
        changes_page_token = token_response["startPageToken"]
        
        # Return changes token (not files token) for future incremental syncs
        return DriveChangesResult(
            changes=changes,
            new_page_token=changes_page_token if not files_page_token else files_page_token,
        )

    def fetch_file_content(self, file_id: str, file_metadata: Mapping[str, object]) -> DriveFileContent:
        """Fetch file content and metadata."""
        service = self._get_service()

        # Get file metadata
        file = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,modifiedTime,createdTime,description,webViewLink",
        ).execute()

        # Try to get file content (text only)
        content = ""
        mime_type = file.get("mimeType", "")

        # Export Google Docs formats to plain text
        if mime_type.startswith("application/vnd.google-apps."):
            try:
                if "document" in mime_type:
                    content = service.files().export(
                        fileId=file_id,
                        mimeType="text/plain",
                    ).execute().decode("utf-8", errors="ignore")
                elif "spreadsheet" in mime_type:
                    content = service.files().export(
                        fileId=file_id,
                        mimeType="text/csv",
                    ).execute().decode("utf-8", errors="ignore")
                elif "presentation" in mime_type:
                    content = service.files().export(
                        fileId=file_id,
                        mimeType="text/plain",
                    ).execute().decode("utf-8", errors="ignore")
            except Exception:
                # If export fails, just use metadata
                content = ""
        elif mime_type.startswith("text/"):
            try:
                content = service.files().get_media(fileId=file_id).execute().decode("utf-8", errors="ignore")
            except Exception:
                content = ""

        return DriveFileContent(
            text=content if content else None,
            metadata=file,
        )


class CalendarClient(GoogleAPIClient):
    """Google Calendar API client implementation."""

    def __init__(self, state_store: GraphitiStateStore, client_id: str, client_secret: str):
        super().__init__(state_store, client_id, client_secret)
        self._service = None
        self._drive_service = None
        self._docs_service = None

    def _get_service(self):
        """Get or create Calendar API service."""
        if self._service is None:
            creds = self._get_credentials()
            self._service = build("calendar", "v3", credentials=creds)
        return self._service
    
    def _get_drive_service(self):
        """Get or create Drive API service for fetching attachments."""
        if self._drive_service is None:
            creds = self._get_credentials()
            self._drive_service = build("drive", "v3", credentials=creds)
        return self._drive_service
    
    def _get_docs_service(self):
        """Get or create Docs API service for fetching document content."""
        if self._docs_service is None:
            creds = self._get_credentials()
            self._docs_service = build("docs", "v1", credentials=creds)
        return self._docs_service
    
    def fetch_transcript_from_attachment(self, file_id: str) -> str | None:
        """Fetch transcript content from a Google Doc attachment."""
        try:
            drive_service = self._get_drive_service()
            docs_service = self._get_docs_service()
            
            # Check if it's a Google Doc
            file_metadata = drive_service.files().get(fileId=file_id, fields="mimeType").execute()
            if file_metadata.get('mimeType') != 'application/vnd.google-apps.document':
                return None
            
            # Get document content with tabs
            document = docs_service.documents().get(documentId=file_id, includeTabsContent=True).execute()
            
            # Look for "Notes" tab first
            if 'tabs' in document:
                for tab in document['tabs']:
                    tab_properties = tab.get('tabProperties', {})
                    title = tab_properties.get('title', '')
                    
                    if title.lower() == 'notes':
                        # Extract content from Notes tab
                        document_tab = tab.get('documentTab', {})
                        body = document_tab.get('body', {})
                        content = body.get('content', [])
                        
                        text_parts = []
                        for item in content:
                            if 'paragraph' in item:
                                for element in item['paragraph'].get('elements', []):
                                    if 'textRun' in element:
                                        text_parts.append(element['textRun'].get('content', ''))
                        
                        return ''.join(text_parts).strip() if text_parts else None
            
            # Fall back to exporting as plain text
            response = drive_service.files().export(fileId=file_id, mimeType='text/plain').execute()
            content = response if isinstance(response, str) else response.decode('utf-8')
            return content.strip() if content else None
            
        except Exception as e:
            # Silently fail - not all attachments will be accessible
            return None

    def list_events(self, calendar_id: str, sync_token: str | None) -> CalendarEventsPage:
        """List events using incremental sync."""
        service = self._get_service()

        try:
            request_params = {
                "calendarId": calendar_id,
                "singleEvents": False,
                "showDeleted": True,
                "fields": "items(id,summary,description,location,start,end,attendees,organizer,recurringEventId,status,updated,attachments),nextSyncToken"
            }
            if sync_token:
                request_params["syncToken"] = sync_token

            response = service.events().list(**request_params).execute()

            events = response.get("items", [])
            next_sync_token = response.get("nextSyncToken", "")

            return CalendarEventsPage(
                events=events,
                next_sync_token=next_sync_token,
            )
        except Exception as exc:
            if "410" in str(exc) or "Sync token" in str(exc):
                raise CalendarSyncTokenExpired(str(exc)) from exc
            raise

    def full_sync(self, calendar_id: str) -> CalendarEventsPage:
        """Perform a full sync (no sync token)."""
        service = self._get_service()

        # Get events from the last year
        time_min = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

        # Collect all pages of events
        all_events = []
        page_token = None
        
        while True:
            request_params = {
                "calendarId": calendar_id,
                "timeMin": time_min,
                "singleEvents": False,
                "showDeleted": True,
                "maxResults": 250,
            }
            if page_token:
                request_params["pageToken"] = page_token
                
            response = service.events().list(**request_params).execute()
            
            all_events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            
            # Break if no more pages or if we have a sync token
            if not page_token:
                break

        # Get sync token by doing another request with syncToken parameter
        sync_response = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            singleEvents=False,
            showDeleted=True,
            fields="items(id,summary,description,location,start,end,attendees,organizer,recurringEventId,status,updated,attachments),nextSyncToken"
        ).execute()
        next_sync_token = sync_response.get("nextSyncToken", "unknown")

        return CalendarEventsPage(
            events=all_events,
            next_sync_token=next_sync_token,
        )


__all__ = ["GmailClient", "DriveClient", "CalendarClient"]

