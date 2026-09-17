import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { createClient } from "@/utils/supabase/server";
import { getWorkspaceId } from "@/lib/workspace";
import DeveloperPanel from "./DeveloperPanel";

export default async function DeveloperPage() {
  const supabase = createClient(await cookies());
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  const workspaceId = await getWorkspaceId(supabase, user.id);
  if (!workspaceId) redirect("/workspace");
  return <DeveloperPanel workspaceId={workspaceId} />;
}
