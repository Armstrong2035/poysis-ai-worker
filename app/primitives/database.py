import os
from typing import Optional, Dict, Any
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

class DatabaseService:
    def __init__(self):
        self.url = os.getenv("SUPABASE_PRODUCT_URL")
        # Note: SERVICE_ROLE_KEY is required for backend operations (RLS bypass)
        self.key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not self.url or not self.key:
            # We don't raise here yet to allow the app to start, 
            # but methods will fail if keys are missing.
            print("[DATABASE] Warning: Supabase credentials missing.")
            self.client = None
        else:
            self.client: Client = create_client(self.url, self.key)

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
    ) -> bool:
        """Store or update Google OAuth tokens in consolidation_workspaces."""
        if not self.client:
            return False
        try:
            self.client.table("consolidation_workspaces").upsert({
                "workspace_id": workspace_id,
                "google_access_token": access_token,
                "google_refresh_token": refresh_token,
                "google_token_expiry": expiry,
            }).execute()
            return True
        except Exception as e:
            print(f"[DATABASE ERROR] Failed to save Google tokens: {e}")
            return False

    async def get_google_tokens(self, workspace_id: str) -> dict | None:
        """Retrieve stored Google OAuth tokens from consolidation_workspaces."""
        if not self.client:
            return None
        try:
            res = (
                self.client.table("consolidation_workspaces")
                .select("google_access_token, google_refresh_token, google_token_expiry")
                .eq("workspace_id", workspace_id)
                .execute()
            )
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
