from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, field_validator


class ScopeConfig(BaseModel):
    workspace_id: str
    sources: List[Literal["google_drive", "gmail", "recordings"]]
    time_window_days: int = 90          # 0 = all time
    doc_limit: int = 500                # -1 = unlimited
    drive_folder_ids: List[str] = []    # [] = all accessible folders
    gmail_labels: List[str] = ["INBOX"]
    recording_channel_ids: List[str] = []
    sync_frequency: Literal["manual", "weekly", "daily"] = "manual"
    google_access_token: Optional[str] = None
    google_refresh_token: Optional[str] = None
    cluster_instructions: List[dict] = []
    indexed_files: Dict[str, str] = {}  # source_id -> etag (modifiedTime)

    @field_validator("sources")
    @classmethod
    def sources_not_empty(cls, v):
        if not v:
            raise ValueError("At least one source must be specified.")
        return v

    @field_validator("time_window_days")
    @classmethod
    def valid_window(cls, v):
        if v < 0:
            raise ValueError("time_window_days must be >= 0 (use 0 for all time).")
        return v
