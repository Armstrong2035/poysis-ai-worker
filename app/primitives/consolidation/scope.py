from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, field_validator, model_validator

# The vocabulary callers use to declare what a run should ingest. Add new sources
# here and the API contract picks them up automatically — SnapshotRequest shares
# this type, so there is one list to keep current, not two.
SourceName = Literal["google_drive", "gmail", "recordings", "youtube"]


class ScopeConfig(BaseModel):
    workspace_id: str
    sources: List[SourceName] = []
    time_window_days: int = 0           # 0 = all time (beta: maximize coverage)
    doc_limit: int = 500                # -1 = unlimited
    drive_folder_ids: List[str] = []    # [] = all accessible folders
    gmail_labels: List[str] = ["INBOX"]
    recording_channel_ids: List[str] = []
    sync_frequency: Literal["manual", "weekly", "daily"] = "manual"
    google_access_token: Optional[str] = None
    google_refresh_token: Optional[str] = None
    cluster_instructions: List[dict] = []
    indexed_files: Dict[str, str] = {}  # source_id -> etag (modifiedTime)
    nango_sources: List[str] = []       # providers managed via Nango, e.g. ["notion", "slack"]
    youtube_channel_ids: List[str] = [] # YouTube channel IDs (no OAuth needed)
    # channel_id -> youtube_channels.id (the "connection" the playground scopes a bot
    # to). Stamped onto each vector at ingest so retrieval can filter a bot to its
    # connection. Empty map = no tagging.
    youtube_channel_connections: Dict[str, str] = {}
    # Minimum video length to ingest. Defaults to 45min, which suits long-form
    # sermon/lecture channels but silently excludes every video on a channel of
    # short talks — set this per workspace when seeding a bot from a channel whose
    # typical upload is shorter. Shorts are <=60s, so ~120 is the floor that still
    # filters them out.
    youtube_min_duration_seconds: int = 2700

    @model_validator(mode="after")
    def at_least_one_source(self):
        if not self.sources and not self.nango_sources and not self.youtube_channel_ids:
            raise ValueError("At least one source must be specified.")
        return self

    @model_validator(mode="after")
    def youtube_declaration_matches_channels(self):
        """`sources` and `youtube_channel_ids` must agree.

        The runner gates YouTube on `youtube_channel_ids` alone. Without this check
        a caller declaring "youtube" with nothing attached gets a run that indexes
        zero videos and reports success — the exact silent no-op this contract
        exists to prevent. Fail at construction instead.
        """
        declared = "youtube" in self.sources
        if declared and not self.youtube_channel_ids:
            raise ValueError(
                "sources includes 'youtube' but no YouTube channels are attached to this workspace."
            )
        if self.youtube_channel_ids and not declared:
            raise ValueError(
                "YouTube channels were supplied but 'youtube' is missing from sources."
            )
        return self

    @field_validator("time_window_days")
    @classmethod
    def valid_window(cls, v):
        if v < 0:
            raise ValueError("time_window_days must be >= 0 (use 0 for all time).")
        return v
