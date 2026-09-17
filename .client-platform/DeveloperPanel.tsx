"use client";
import { useCallback, useEffect, useState } from "react";
import { authedFetch } from "@/utils/authedFetch";

type Key = { key_id: string; name: string; key_prefix: string; scopes: string[]; created_at: string; last_used_at?: string; revoked_at?: string };
type Usage = { day: string; scope: string; requests: number };

export default function DeveloperPanel({ workspaceId }: { workspaceId: string }) {
  const base = `/api/platform?workspace_id=${encodeURIComponent(workspaceId)}`;
  const [keys, setKeys] = useState<Key[]>([]); const [usage, setUsage] = useState<Usage[]>([]);
  const [name, setName] = useState("Production"); const [created, setCreated] = useState(""); const [error, setError] = useState("");
  const load = useCallback(async () => {
    const [k, u] = await Promise.all([authedFetch(`${base}&resource=keys`), authedFetch(`${base}&resource=usage`)]);
    if (!k.ok || !u.ok) throw new Error("API access settings could not be loaded");
    setKeys((await k.json()).data); setUsage((await u.json()).data);
  }, [base]);
  useEffect(() => { void load().catch(e => setError(e.message)); }, [load]);
  async function create() {
    setError(""); setCreated("");
    const response = await authedFetch(`${base}&resource=keys`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, scopes: ["marketing:read"] }) });
    const body = await response.json(); if (!response.ok) return setError(body.detail ?? "Key could not be created");
    setCreated(body.api_key); await load();
  }
  async function revoke(id: string) { const r = await authedFetch(`${base}&resource=keys&key_id=${id}`, { method: "DELETE" }); if (r.ok) await load(); else setError("Key could not be revoked"); }
  return <main className="mx-auto max-w-4xl space-y-8 p-6 text-neutral-900">
    <header><h1 className="text-2xl font-semibold">Developer API</h1><p className="mt-2 text-neutral-600">Issue server-side keys for products that consume Poysis infrastructure.</p></header>
    <section className="rounded-xl border p-5 space-y-3"><h2 className="font-semibold">Create API key</h2><div className="flex gap-3"><input className="flex-1 rounded-lg border p-3" maxLength={200} value={name} onChange={e => setName(e.target.value)} /><button className="rounded-lg bg-neutral-900 px-5 text-white" onClick={() => void create()}>Create key</button></div><p className="text-sm text-neutral-600">Scope: marketing:read. The secret is shown once.</p></section>
    {created && <section className="rounded-xl border border-amber-300 bg-amber-50 p-5"><h2 className="font-semibold">Copy this key now</h2><code className="mt-3 block break-all rounded bg-white p-3">{created}</code><button className="mt-3 underline" onClick={() => void navigator.clipboard.writeText(created)}>Copy</button></section>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
    <section><h2 className="mb-3 font-semibold">API keys</h2><div className="space-y-2">{keys.map(k => <div key={k.key_id} className="flex items-center justify-between rounded-lg border p-4"><div><p>{k.name}</p><p className="text-sm text-neutral-500">poysis_live_{k.key_prefix}… · {k.revoked_at ? "Revoked" : k.last_used_at ? `Last used ${new Date(k.last_used_at).toLocaleString()}` : "Never used"}</p></div>{!k.revoked_at && <button className="text-red-700 underline" onClick={() => void revoke(k.key_id)}>Revoke</button>}</div>)}</div></section>
    <section><h2 className="mb-3 font-semibold">Usage, last 30 days</h2><div className="rounded-lg border p-4">{usage.length ? usage.map((u,i) => <p key={i}>{new Date(u.day).toLocaleDateString()} · {u.scope} · {u.requests}</p>) : <p className="text-neutral-500">No API requests yet.</p>}</div></section>
    <section className="rounded-xl bg-neutral-100 p-5"><h2 className="font-semibold">Server configuration</h2><pre className="mt-3 overflow-x-auto text-sm">POYSIS_API_URL=https://api.poysis.com{"\n"}POYSIS_API_TOKEN=poysis_live_...</pre></section>
  </main>;
}
