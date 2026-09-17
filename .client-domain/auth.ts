"use server";

import { createClient } from "../utils/supabase/server";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { ensureWorkspace } from "./workspace";
import { landingPathForUser } from "./uiMode";

/**
 * Base URL for auth email redirects. Prefer the configured site URL so the
 * target is deterministic across environments; fall back to the request
 * origin header, which can be empty in some server-action contexts (and when
 * it is, Supabase silently falls back to the dashboard Site URL).
 */
async function authRedirectBase(): Promise<string> {
  const configured = process.env.NEXT_PUBLIC_SITE_URL;
  if (configured) return configured.replace(/\/$/, "");
  return (await headers()).get("origin") ?? "";
}

/**
 * Log in a user with email and password.
 */
export async function login(formData: FormData) {
  const cookieStore = await cookies();
  const supabase = createClient(cookieStore);

  const email = formData.get("email") as string;
  const password = formData.get("password") as string;

  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  });

  if (error) {
    console.error("Login error:", error.message);
    return redirect("/login?error=" + encodeURIComponent(error.message));
  }

  // Backfill a workspace for accounts that never got one — e.g. created before
  // signup provisioned it, or via a path that skipped ensureWorkspace. Without
  // this every workspace-scoped API route 404s ("Workspace not found"), and
  // password sign-in (unlike signup/callback) had no other place to create it.
  // Idempotent: a no-op when the user already has a workspace.
  if (data.user) {
    await ensureWorkspace(supabase, data.user.id);
  }

  revalidatePath("/", "layout");
  const hostname = (await headers()).get("host")?.split(":")[0].toLowerCase();
  if (hostname === "developers.poysis.com") {
    return redirect("/workspace/developer");
  }
  // Land the user in their chosen app; non-entitled users always get Creator.
  return redirect(await landingPathForUser(supabase, data.user));
}

/**
 * Sign up a new user with email and password.
 */
export async function signup(formData: FormData) {
  const cookieStore = await cookies();
  const supabase = createClient(cookieStore);

  const email = formData.get("email") as string;
  const password = formData.get("password") as string;
  const base = await authRedirectBase();

  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      emailRedirectTo: `${base}/auth/callback`,
    },
  });

  if (error) {
    console.error("Signup error:", error.message);
    return redirect("/signup?error=" + encodeURIComponent(error.message));
  }

  // Email confirmation is disabled — session is immediately available, so
  // create the workspace now and head straight into the app.
  if (data.session) {
    await ensureWorkspace(supabase, data.session.user.id);

    revalidatePath("/", "layout");
    return redirect("/studio");
  }

  // Email confirmation is enabled — no session yet. The workspace is created
  // in /auth/callback once the user confirms. Tell them to check their inbox.
  return redirect("/login?message=" + encodeURIComponent("Check your email to confirm your account."));
}

/**
 * Send a password-reset email. The link returns the user to /auth/callback,
 * which exchanges the code for a session and forwards to /reset.
 */
export async function requestPasswordReset(formData: FormData) {
  const cookieStore = await cookies();
  const supabase = createClient(cookieStore);

  const email = formData.get("email") as string;
  const base = await authRedirectBase();

  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: `${base}/auth/callback?next=/reset`,
  });

  if (error) {
    console.error("Password reset error:", error.message);
    return redirect("/forgot?error=" + encodeURIComponent(error.message));
  }

  // Always report success — don't reveal whether an account exists.
  return redirect(
    "/forgot?message=" +
      encodeURIComponent("Check your email for a password reset link."),
  );
}

/**
 * Update the current user's password. Requires the recovery session
 * established by clicking the reset link.
 */
export async function updatePassword(formData: FormData) {
  const cookieStore = await cookies();
  const supabase = createClient(cookieStore);

  const password = formData.get("password") as string;

  const { error } = await supabase.auth.updateUser({ password });

  if (error) {
    console.error("Update password error:", error.message);
    return redirect("/reset?error=" + encodeURIComponent(error.message));
  }

  revalidatePath("/", "layout");
  return redirect(
    "/login?message=" +
      encodeURIComponent("Password updated. Sign in with your new password."),
  );
}

/**
 * Log out the current user.
 */
export async function logout() {
  const cookieStore = await cookies();
  const supabase = createClient(cookieStore);

  await supabase.auth.signOut();
  revalidatePath("/", "layout");
  return redirect("/login");
}
