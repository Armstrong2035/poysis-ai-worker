import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { createClient } from "@/utils/supabase/server";
import { getWorkspaceId } from "@/lib/workspace";
import InterpretationPanel from "./InterpretationPanel";

export default async function InterpretationsPage() {
  const supabase = createClient(await cookies());
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  const workspaceId = await getWorkspaceId(supabase, user.id);
  if (!workspaceId) redirect("/workspace");
  return <InterpretationPanel workspaceId={workspaceId} />;
}
