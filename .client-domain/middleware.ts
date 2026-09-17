import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

/**
 * Supabase Auth Middleware (Transparent Session Refresher)
 * - Refreshes the session on every request to keep cookies alive.
 * - DOES NOT redirect. Redirection is handled by individual Server Components
 *   (e.g., in /notebook/page.tsx) to avoid breaking Server Actions and public routes.
 */

/**
 * Maximum time for the call to the Supabase auth server, in milliseconds.
 * Vercel stops the middleware at 25s and returns MIDDLEWARE_INVOCATION_TIMEOUT.
 * A short budget keeps a slow auth server from taking down every route.
 */
const AUTH_TIMEOUT_MS = 3000;

/**
 * Returns true if the request carries a Supabase session cookie.
 * The cookie name is `sb-<project-ref>-auth-token`, and large sessions
 * split it into `...auth-token.0`, `...auth-token.1`, and so on.
 */
function hasSessionCookie(request: NextRequest): boolean {
  return request.cookies
    .getAll()
    .some((c) => c.name.startsWith("sb-") && c.name.includes("auth-token"));
}

export async function middleware(request: NextRequest) {
  const hostname = request.headers.get("host")?.split(":")[0].toLowerCase();
  const developerHost = hostname === "developers.poysis.com";

  if (
    developerHost &&
    (request.nextUrl.pathname === "/" ||
      request.nextUrl.pathname === "/studio" ||
      request.nextUrl.pathname === "/workspace")
  ) {
    const portalUrl = request.nextUrl.clone();
    portalUrl.pathname = "/workspace/developer";
    return NextResponse.redirect(portalUrl);
  }

  // Supabase sometimes lands email-confirmation / recovery redirects on the
  // site root (e.g. https://www.poysis.com/?code=...) instead of
  // /auth/callback — this happens when the redirect falls back to the Site URL
  // rather than the configured callback. Forward any such code to the callback
  // (preserving `next` and other params) so the PKCE exchange and the
  // post-login redirect to /workspace actually run.
  if (
    request.nextUrl.pathname === "/" &&
    request.nextUrl.searchParams.has("code")
  ) {
    const callbackUrl = request.nextUrl.clone();
    callbackUrl.pathname = "/auth/callback";
    return NextResponse.redirect(callbackUrl);
  }

  let supabaseResponse = NextResponse.next({
    request: { headers: request.headers },
  });

  // An anonymous visitor has no session to refresh. Return at once and do not
  // call the auth server. This keeps all public pages off the network path.
  if (!hasSessionCookie(request)) {
    return supabaseResponse;
  }

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY;

  // If env vars are missing (e.g. during cold start or misconfigured deployment),
  // pass the request through rather than crashing every route.
  if (!supabaseUrl || !supabaseKey) {
    console.warn("[middleware] Supabase env vars missing — skipping session refresh.");
    return supabaseResponse;
  }

  // Abort the request to the auth server after the time budget. Without this
  // the fetch can hang until Vercel stops the whole invocation.
  const controller = new AbortController();
  const abortTimer = setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS);

  try {
    const supabase = createServerClient(supabaseUrl, supabaseKey, {
      global: {
        fetch: (input: RequestInfo | URL, init?: RequestInit) =>
          fetch(input, { ...init, signal: controller.signal }),
      },
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value)
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options)
          );
        },
      },
    });

    // Refreshes the session — vital for SSR and keeps the user logged in for Server Actions.
    // The race is a second guard, in case a layer above the fetch ignores the abort.
    await Promise.race([
      supabase.auth.getUser(),
      new Promise((resolve) => setTimeout(resolve, AUTH_TIMEOUT_MS + 500)),
    ]);
  } catch (err) {
    console.error("[middleware] Session refresh failed:", err);
    // Still pass the request through — auth will be handled per-page
  } finally {
    clearTimeout(abortTimer);
  }

  return supabaseResponse;
}

export const config = {
  matcher: [
    // Only /studio and /workspace read the session in a Server Component, and
    // a Server Component cannot write the refreshed cookie itself. These are
    // the only routes that need the refresh. Route Handlers under /api and
    // /auth/callback write their own cookies. The landing page, the auth
    // pages, and /preview never read the session at all.
    "/studio/:path*",
    "/workspace/:path*",
    // The site root runs only to forward a Supabase auth code to the callback.
    "/",
  ],
};
