import os
from typing import Optional, Dict, Any, List
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

class DatabaseService:
    def __init__(self):
        self.url = os.getenv("SUPABASE_PRODUCT_URL")
        # Note: SERVICE_ROLE_KEY is required for backend operations (RLS bypass)
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        self._client: Client = None

        if not self.url or not self.key:
            print("[DATABASE] Warning: Supabase credentials missing.")

    @property
    def client(self) -> Client:
        """Lazy-initialize client to bypass schema cache on migrations."""
        if self._client is None:
            if self.url and self.key:
                self._client = create_client(self.url, self.key)
        return self._client

    def refresh_client(self) -> None:
        """Force reconnection to pick up schema changes (e.g., after migrations)."""
        self._client = None
        _ = self.client  # Re-initialize

    async def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Fetch workspace details using workspace_id (mapped to workspace_id column)."""
        if not self.client:
            return None
            
        try:
            # We keep the column name as 'workspace_id' in the DB for now
            response = self.client.table("workspaces").select("*").eq("workspace_id", workspace_id).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch workspace: {e}")
            return None

    async def save_workspace(self, workspace_data: Dict[str, Any]) -> bool:
        """Upsert workspace record in the registry."""
        if not self.client:
            return False

        try:
            self.client.table("workspaces").upsert(workspace_data).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save workspace: {e}")
            return False

    async def create_workspace(self, workspace_id: str, user_id: str, name: str = "My Workspace") -> bool:
        """Create a new workspace for a user."""
        if not self.client:
            return False

        try:
            self.client.table("workspaces").insert({
                "workspace_id": workspace_id,
                "user_id": user_id,
                "name": name,
            }).execute()
            # Auto-add creator as owner in workspace_members
            await self.add_workspace_member(workspace_id, user_id, role="owner")
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to create workspace: {e}")
            return False

    async def has_workspace_access(self, workspace_id: str, user_id: str) -> bool:
        """Check if user has any access (owner, member, or viewer) to workspace."""
        if not self.client:
            return False
        try:
            response = self.client.table("workspace_members").select("id").eq("workspace_id", workspace_id).eq("user_id", user_id).execute()
            return bool(response.data)
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to check workspace access: {e}")
            return False

    async def add_workspace_member(self, workspace_id: str, user_id: str, role: str = "member") -> bool:
        """Add a user to a workspace with specified role."""
        if not self.client:
            return False
        try:
            self.client.table("workspace_members").upsert({
                "workspace_id": workspace_id,
                "user_id": user_id,
                "role": role,
            }, on_conflict="workspace_id,user_id").execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to add workspace member: {e}")
            return False

    async def remove_workspace_member(self, workspace_id: str, user_id: str) -> bool:
        """Remove a user from a workspace."""
        if not self.client:
            return False
        try:
            self.client.table("workspace_members").delete().eq("workspace_id", workspace_id).eq("user_id", user_id).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to remove workspace member: {e}")
            return False

    async def get_workspace_members(self, workspace_id: str) -> List[Dict[str, Any]]:
        """Get all members of a workspace."""
        if not self.client:
            return []
        try:
            response = self.client.table("workspace_members").select("*").eq("workspace_id", workspace_id).execute()
            return response.data or []
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to get workspace members: {e}")
            return []

    async def update_credits(self, workspace_id: str, amount: int) -> bool:
        """Atomically update credit balance."""
        if not self.client:
            return False
        
        try:
            workspace = await self.get_workspace(workspace_id)
            if not workspace:
                return False
                
            new_balance = workspace.get("credits_balance", 0) + amount
            self.client.table("workspaces").update({"credits_balance": new_balance}).eq("workspace_id", workspace_id).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to update credits: {e}")
            return False

    async def increment_query_count(self, workspace_id: str):
        """Update total query count for a workspace."""
        if not self.client: return
        try:
            # Fetch current
            res = self.client.table("workspaces").select("total_queries").eq("workspace_id", workspace_id).execute()
            if res.data:
                curr = res.data[0].get("total_queries", 0)
                self.client.table("workspaces").update({"total_queries": curr + 1}).eq("workspace_id", workspace_id).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to increment query count: {e}")

    async def log_search(self, analytics_data: Dict[str, Any]):
        """Log search telemetry to Supabase."""
        if not self.client: return
        try:
            self.client.table("search_logs").insert(analytics_data).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to log search: {e}")

    async def log_attribution_event(self, event_data: Dict[str, Any]):
        """Log click or cart event to Supabase."""
        if not self.client: return
        try:
            self.client.table("attribution_events").insert(event_data).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to log attribution event: {e}")

    async def get_dashboard_analytics(self, workspace_id: str, days: int = 30) -> Optional[Dict[str, Any]]:
        """
        Calls the highly-performant Supabase RPC to aggregate all dashboard metrics
        (Searches, Carts, Checkouts, Trending Queries, Missed Opportunities, Top Products)
        in a single database transaction.
        """
        if not self.client: return None
        try:
            # We use the Supabase JS-equivalent rpc call
            res = self.client.rpc(
                "get_dashboard_analytics", 
                {"target_workspace": workspace_id, "days_back": days}
            ).execute()
            
            return res.data
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch analytics: {e}")
            return None

    async def save_google_tokens(
        self,
        workspace_id: str,
        access_token: str,
        refresh_token: str,
        expiry: str,
        user_id: str = None,
    ) -> bool:
        """Store or update Google OAuth tokens in consolidation_workspaces."""
        if not self.client:
            return False
        try:
            self.client.table("consolidation_workspaces").upsert({
                "workspace_id": workspace_id,
                "user_id": user_id,
                "google_access_token": access_token,
                "google_refresh_token": refresh_token,
                "google_token_expiry": expiry,
            }, on_conflict="consolidation_workspaces_workspace_user_unique").execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save Google tokens: {e}")
            return False

    async def get_google_tokens(self, workspace_id: str, user_id: str = None) -> dict | None:
        """Retrieve stored Google OAuth tokens from consolidation_workspaces."""
        if not self.client:
            return None
        try:
            query = (
                self.client.table("consolidation_workspaces")
                .select("google_access_token, google_refresh_token, google_token_expiry")
                .eq("workspace_id", workspace_id)
            )
            if user_id:
                query = query.eq("user_id", user_id)

            res = query.execute()
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to get Google tokens: {e}")
            return None

    async def get_recent_searches(self, workspace_id: str, limit: int = 50) -> list:
        """Fetch the most recent searches for the live feed."""
        if not self.client: return []
        try:
            res = self.client.table("search_logs")\
                .select("id, query, result_count, created_at, latency_ms")\
                .eq("workspace_id", workspace_id)\
                .order("created_at", desc=True)\
                .limit(limit)\
                .execute()
            return res.data
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch recent searches: {e}")
            return []

    async def get_indexed_files(self, workspace_id: str) -> dict:
        """Return {source_id: etag} for all files already indexed for a workspace."""
        if not self.client:
            return {}
        try:
            res = (
                self.client.table("consolidation_indexed_files")
                .select("source_id, etag")
                .eq("workspace_id", workspace_id)
                .execute()
            )
            return {row["source_id"]: row["etag"] for row in res.data}
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch indexed files: {e}")
            return {}

    async def mark_files_indexed(self, workspace_id: str, files: list) -> None:
        """Upsert a batch of {source_id, etag} records as indexed for a workspace."""
        if not self.client or not files:
            return
        try:
            rows = [
                {"workspace_id": workspace_id, "source_id": f["source_id"], "etag": f["etag"]}
                for f in files
            ]
            self.client.table("consolidation_indexed_files").upsert(
                rows, on_conflict="workspace_id,source_id"
            ).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to mark files indexed: {e}")

    async def clear_topics(self, workspace_id: str) -> None:
        """Delete all topics for a workspace before re-saving."""
        if not self.client:
            return
        try:
            self.client.table("consolidation_topics").delete().eq("workspace_id", workspace_id).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to clear topics: {e}")

    async def save_topics(self, workspace_id: str, topics: list) -> None:
        """Upsert topic summaries for a workspace."""
        if not self.client or not topics:
            return
        try:
            rows = [
                {
                    "workspace_id": workspace_id,
                    "topic_id": t["topic_id"],
                    "label": t.get("label"),
                    "keywords": t.get("keywords", []),
                    "doc_count": t.get("doc_count", 0),
                    "parent_topic_id": t.get("parent_topic_id"),
                    "semantic_summary": t.get("semantic_summary"),
                    "key_themes": t.get("key_themes", []),
                    "suggested_use_cases": t.get("suggested_use_cases", []),
                    "updated_at": "now()",
                }
                for t in topics
            ]
            self.client.table("consolidation_topics").upsert(
                rows, on_conflict="workspace_id,topic_id"
            ).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save topics: {e}")

    async def save_stories(self, workspace_id: str, stories: list) -> None:
        """Upsert story narratives for a workspace."""
        if not self.client or not stories:
            return
        try:
            rows = [
                {
                    "workspace_id": workspace_id,
                    "story_id": s["story_id"],
                    "title": s.get("title"),
                    "description": s.get("description"),
                    "topic_sequence": s.get("topic_sequence", []),
                    "reasoning": s.get("reasoning"),
                    "strength": float(s.get("strength", 0.5)),
                    "doc_count": s.get("doc_count", 0),
                    "updated_at": "now()",
                }
                for s in stories
            ]
            self.client.table("consolidation_stories").upsert(
                rows, on_conflict="workspace_id,story_id"
            ).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save stories: {e}")

    async def get_stories(self, workspace_id: str) -> list:
        """Fetch all stories for a workspace."""
        if not self.client:
            return []
        try:
            res = (
                self.client.table("consolidation_stories")
                .select("story_id, title, description, topic_sequence, reasoning, strength, doc_count")
                .eq("workspace_id", workspace_id)
                .order("strength", desc=True)
                .execute()
            )
            return res.data
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch stories: {e}")
            return []

    async def clear_stories(self, workspace_id: str) -> None:
        """Delete all stories for a workspace before re-saving."""
        if not self.client:
            return
        try:
            self.client.table("consolidation_stories").delete().eq("workspace_id", workspace_id).execute()
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to clear stories: {e}")

    async def get_topics(self, workspace_id: str) -> list:
        """Fetch all topics for a workspace."""
        if not self.client:
            return []
        try:
            res = (
                self.client.table("consolidation_topics")
                .select("topic_id, label, keywords, doc_count, parent_topic_id, semantic_summary, key_themes, suggested_use_cases, updated_at")
                .eq("workspace_id", workspace_id)
                .order("doc_count", desc=True)
                .execute()
            )
            return res.data
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch topics: {e}")
            return []

    async def add_waitlist_email(self, email: str, source: Optional[str] = None) -> bool:
        """Insert a waitlist signup. Idempotent on email (returns True if already present)."""
        if not self.client:
            return False
        try:
            self.client.table("waitlist").upsert(
                {"email": email, "source": source}, on_conflict="email"
            ).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to add waitlist email: {e}")
            return False

    async def get_raw_logs(self, workspace_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None, limit: int = 100, offset: int = 0) -> list:
        """
        Fetch raw, paginated search logs for a workspace, joining any associated attribution events.
        Ideal for CSV exports or BI tool integrations.
        """
        if not self.client: return []
        try:
            # We use Supabase relation syntax to join attribution_events
            query = self.client.table("search_logs").select("*, attribution_events(*)").eq("workspace_id", workspace_id)

            if start_date:
                query = query.gte("created_at", start_date)
            if end_date:
                query = query.lte("created_at", end_date)

            res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return res.data
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch raw logs for export: {e}")
            return []

    # ============================================================================
    # Job Tracking (persistent state across deploys)
    # ============================================================================

    async def create_job(self, workspace_id: str, user_id: str, job_type: str) -> Optional[str]:
        """Create a new job record. Returns job ID."""
        if not self.client:
            return None
        try:
            res = self.client.table("consolidation_jobs").insert({
                "workspace_id": workspace_id,
                "user_id": user_id,
                "job_type": job_type,
                "status": "running"
            }).execute()
            return res.data[0]["id"] if res.data else None
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to create job: {e}")
            return None

    async def update_job(self, job_id: str, status: str, result: dict = None, error: str = None) -> bool:
        """Update job status and result/error."""
        if not self.client:
            return False
        try:
            update_data = {
                "status": status,
                "updated_at": "now()"
            }
            if status == "done":
                update_data["completed_at"] = "now()"
                if result:
                    update_data["result"] = result
            if error:
                update_data["error"] = error
                if status != "running":
                    update_data["completed_at"] = "now()"

            self.client.table("consolidation_jobs").update(update_data).eq("id", job_id).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to update job: {e}")
            return False

    async def get_job(self, job_id: str) -> Optional[dict]:
        """Fetch job status."""
        if not self.client:
            return None
        try:
            res = self.client.table("consolidation_jobs").select("*").eq("id", job_id).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch job: {e}")
            return None

    async def get_latest_job(self, workspace_id: str, job_type: str = None) -> Optional[dict]:
        """Fetch latest job for a workspace (optionally filtered by type)."""
        if not self.client:
            return None
        try:
            query = self.client.table("consolidation_jobs").select("*").eq("workspace_id", workspace_id)
            if job_type:
                query = query.eq("job_type", job_type)
            res = query.order("created_at", desc=True).limit(1).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch latest job: {e}")
            return None

    # ============================================================================
    # Google Drive Connections (OAuth + token management)
    # ============================================================================

    async def get_drive_connection(self, user_id: str, workspace_id: str, google_account_email: str) -> Optional[Dict[str, Any]]:
        """Fetch a user's Google Drive connection for a specific workspace."""
        if not self.client:
            return None
        try:
            res = (
                self.client.table("drive_connections")
                .select("*")
                .eq("user_id", user_id)
                .eq("workspace_id", workspace_id)
                .eq("google_account_email", google_account_email)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to fetch drive connection: {e}")
            return None

    async def list_drive_connections(self, user_id: str, workspace_id: Optional[str] = None) -> list:
        """List Google Drive connections for a user, optionally filtered by workspace."""
        if not self.client:
            return []
        try:
            query = (
                self.client.table("drive_connections")
                .select("id, workspace_id, google_account_email, doc_count, last_synced_at, created_at")
                .eq("user_id", user_id)
            )
            if workspace_id:
                query = query.eq("workspace_id", workspace_id)
            res = query.order("created_at", desc=True).execute()
            return res.data
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to list drive connections: {e}")
            return []

    async def save_drive_connection(
        self,
        user_id: str,
        workspace_id: str,
        google_account_email: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        token_expiry: Optional[str] = None,
    ) -> bool:
        """Save or update a Google Drive connection for a workspace."""
        if not self.client:
            return False
        try:
            self.client.table("drive_connections").upsert(
                {
                    "user_id": user_id,
                    "workspace_id": workspace_id,
                    "google_account_email": google_account_email,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_expiry": token_expiry,
                },
                on_conflict="user_id,workspace_id,google_account_email",
            ).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save drive connection: {e}")
            return False

    async def update_drive_connection_doc_count(self, connection_id: str, doc_count: int) -> bool:
        """Update document count and last synced time for a connection."""
        if not self.client:
            return False
        try:
            self.client.table("drive_connections").update(
                {"doc_count": doc_count, "last_synced_at": "now()"}
            ).eq("id", connection_id).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to update drive connection doc_count: {e}")
            return False

    async def delete_drive_connection(self, user_id: str, workspace_id: str, google_account_email: str) -> bool:
        """Delete a Google Drive connection."""
        if not self.client:
            return False
        try:
            self.client.table("drive_connections").delete().eq(
                "user_id", user_id
            ).eq("workspace_id", workspace_id).eq(
                "google_account_email", google_account_email
            ).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to delete drive connection: {e}")
            return False

    async def mark_drive_connection_synced(self, workspace_id: str) -> bool:
        """Update last_synced_at for the active drive connection in a workspace."""
        if not self.client:
            return False
        try:
            # Get the most recent connection for this workspace
            res = (
                self.client.table("drive_connections")
                .select("id")
                .eq("workspace_id", workspace_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if not res.data:
                return False

            conn_id = res.data[0]["id"]
            self.client.table("drive_connections").update(
                {"last_synced_at": "now()"}
            ).eq("id", conn_id).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to mark drive connection synced: {e}")
            return False
