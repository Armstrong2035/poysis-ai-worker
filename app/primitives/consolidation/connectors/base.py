from abc import ABC, abstractmethod
from datetime import datetime
from typing import AsyncIterator, Literal, Optional
from pydantic import BaseModel

from app.primitives.consolidation.scope import ScopeConfig


class RawSourceItem(BaseModel):
    source_id: str
    source_type: Literal["google_drive", "gmail", "recordings", "notion", "slack", "github", "google_calendar", "google_mail"]
    title: str
    url: str
    etag: str
    last_modified: datetime
    content_type: Literal["document", "spreadsheet", "pdf", "office_doc", "email_thread", "audio"]
    fetch_url: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: int = 0


class BaseConnector(ABC):
    @abstractmethod
    async def list_items(self, scope: ScopeConfig) -> AsyncIterator[RawSourceItem]:
        """Yield all items within the scope boundary."""
        ...

    @abstractmethod
    async def fetch_text(self, item: RawSourceItem) -> str:
        """Fetch plain text content for text-exportable items."""
        ...

    @abstractmethod
    async def fetch_file(self, item: RawSourceItem) -> str:
        """Download binary file to a temp path and return the path."""
        ...
