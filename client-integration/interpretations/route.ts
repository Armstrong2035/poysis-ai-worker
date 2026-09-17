import { NextRequest } from "next/server";

export async function GET(request: NextRequest) {
  return proxy(request);
}

export async function POST(request: NextRequest) {
  return proxy(request);
}

async function proxy(request: NextRequest) {
  const base = process.env.WORKER_URL ?? process.env.LOCAL_WORKER_URL;
  if (!base) return Response.json({ detail: "Analysis service is not configured" }, { status: 503 });
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) {
    return Response.json({ detail: "Sign in to analyze workspace evidence" }, { status: 401 });
  }
  const workspace = request.nextUrl.searchParams.get("workspace_id");
  const job = request.nextUrl.searchParams.get("id");
  const kind = request.nextUrl.searchParams.get("kind");
  if (!workspace || workspace.length > 200 || (job && !/^[0-9a-f-]{36}$/i.test(job))) {
    return Response.json({ detail: "Invalid workspace or interpretation ID" }, { status: 400 });
  }
  const suffix = request.method === "POST" ? "" : kind === "datasets" ? "/datasets" : job ? `/${job}` : null;
  if (suffix === null) return Response.json({ detail: "Interpretation ID is required" }, { status: 400 });
  const target = new URL(`${base.replace(/\/$/, "")}/interpretations${suffix}`);
  target.searchParams.set("workspace_id", workspace);
  try {
    const body = request.method === "POST" ? await request.text() : undefined;
    if (body && body.length > 64000) return Response.json({ detail: "Request is too large" }, { status: 413 });
    const response = await fetch(target, {
      method: request.method, cache: "no-store", signal: AbortSignal.timeout(30000),
      headers: { Authorization: authorization, "Content-Type": "application/json",
        ...(request.method === "POST" ? { "Idempotency-Key": request.headers.get("idempotency-key") ?? "" } : {}) },
      body,
    });
    if (response.status >= 500) return Response.json({ detail: "Analysis service is unavailable" }, { status: 503 });
    return Response.json(await response.json(), { status: response.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ detail: "Could not reach the analysis service" }, { status: 503 });
  }
}
