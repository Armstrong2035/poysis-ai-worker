import { NextRequest } from "next/server";

export async function GET(request: NextRequest) { return proxy(request); }
export async function POST(request: NextRequest) { return proxy(request); }
export async function PUT(request: NextRequest) { return proxy(request); }
export async function DELETE(request: NextRequest) { return proxy(request); }

async function proxy(request: NextRequest) {
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) return Response.json({ detail: "Sign in required" }, { status: 401 });
  const workspace = request.nextUrl.searchParams.get("workspace_id");
  const resource = request.nextUrl.searchParams.get("resource") ?? "keys";
  const keyId = request.nextUrl.searchParams.get("key_id");
  if (!workspace) return Response.json({ detail: "Workspace is required" }, { status: 400 });
  const paths: Record<string, string> = { client: "/platform/client", keys: "/platform/api-keys", usage: "/platform/usage" };
  let path = paths[resource];
  if (!path || (request.method === "DELETE" && (!keyId || !/^[0-9a-f-]{36}$/i.test(keyId)))) return Response.json({ detail: "Invalid platform request" }, { status: 400 });
  if (request.method === "DELETE") path += `/${keyId}`;
  const target = new URL(`${process.env.HERMENEUTICS_WORKER_URL ?? "https://api.poysis.com"}${path}`);
  target.searchParams.set("workspace_id", workspace);
  if (resource === "usage") target.searchParams.set("days", request.nextUrl.searchParams.get("days") ?? "30");
  const body = ["POST", "PUT"].includes(request.method) ? await request.text() : undefined;
  const response = await fetch(target, { method: request.method, body, cache: "no-store", signal: AbortSignal.timeout(30000), headers: { Authorization: authorization, "Content-Type": "application/json" } });
  if (response.status === 204) return new Response(null, { status: 204 });
  return Response.json(await response.json(), { status: response.status, headers: { "Cache-Control": "no-store" } });
}
