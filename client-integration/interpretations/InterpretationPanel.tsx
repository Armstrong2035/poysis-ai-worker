"use client";

import { useEffect, useRef, useState } from "react";
import { authedFetch } from "@/utils/authedFetch";

type Dataset = { source: "ga4" | "search_console"; property_id: string; dataset: string; metrics: string[] };
type Evidence = { id: string; kind: string; locator: string; title?: string; text?: string; thesis?: string; metric?: string; value?: number; unit?: string; period?: { start: string; end: string }; statement?: string; limitations: string[] };
type Reading = { thesis: string; claims: { statement: string; limitations: string[]; evidence: { evidence_id: string; relationship: string; rationale: string }[] }[] };
type Result = { synthesis: { outcome: string; primary: Reading | null; alternatives: Reading[]; missing_context: string[]; confidence: { level: string; rationale: string } };
  context: { facts: Evidence[]; signals: Evidence[]; relevant_documents: Evidence[]; previous_interpretations: Evidence[] } };
type Job = { id: string; status: string; error_code: string | null; interpretation: Result | null };

const day = (offset: number) => new Date(Date.now() + offset * 86400000).toISOString().slice(0, 10);
const field = "w-full rounded-lg border border-neutral-300 bg-white p-3 text-neutral-900";

export default function InterpretationPanel({ workspaceId }: { workspaceId: string }) {
  const [question, setQuestion] = useState("");
  const [objective, setObjective] = useState("Understand customer acquisition performance");
  const [start, setStart] = useState(() => day(-28));
  const [end, setEnd] = useState(() => day(-1));
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [sourceError, setSourceError] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const retry = useRef<{ body: string; key: string } | null>(null);
  const endpoint = `/api/interpretations?workspace_id=${encodeURIComponent(workspaceId)}`;

  useEffect(() => {
    const controller = new AbortController();
    authedFetch(`${endpoint}&kind=datasets`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(); return response.json(); })
      .then(value => setDatasets(value.data))
      .catch(() => { if (!controller.signal.aborted) setSourceError("Metrics could not be loaded. Document analysis is still available; metric coverage will be reported as missing."); });
    return () => { controller.abort(); abort.current?.abort(); };
  }, [endpoint]);

  async function poll(id: string, signal: AbortSignal) {
    const deadline = Date.now() + 10 * 60 * 1000;
    while (Date.now() < deadline && !signal.aborted) {
      const response = await authedFetch(`${endpoint}&id=${encodeURIComponent(id)}`, { signal });
      if (!response.ok) throw new Error("Unable to retrieve analysis status. You can check again.");
      const result: Job = await response.json();
      setJob(result);
      if (result.status === "completed") return;
      if (result.status === "failed") throw new Error("Analysis could not complete. Check that your sources are available, then retry.");
      await new Promise<void>(resolve => {
        const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
        const timer = setTimeout(finish, 2000);
        signal.addEventListener("abort", finish, { once: true });
      });
    }
    if (!signal.aborted) setError("The analysis is still running. Use Check status to resume waiting.");
  }

  async function run(resume = false) {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setError(""); setBusy(true);
    try {
      let id = resume ? job?.id : undefined;
      if (!id) {
        const body = JSON.stringify({ question, frame: { objective, period: { start, end },
          datasets: selected.map(index => {
            const { source, property_id, dataset, metrics } = datasets[index];
            return { source, property_id, dataset, metrics };
          }), include_documents: true } });
        if (retry.current?.body !== body) retry.current = { body, key: crypto.randomUUID() };
        const response = await authedFetch(endpoint, { method: "POST", signal: controller.signal,
          headers: { "Content-Type": "application/json", "Idempotency-Key": retry.current!.key }, body });
        if (!response.ok) {
          const payload = await response.json();
          throw new Error(typeof payload.detail === "string" ? payload.detail : "Check the question, dates and data selection.");
        }
        const result: Job = await response.json();
        setJob(result); id = result.id;
      }
      await poll(id, controller.signal);
    } catch (caught) {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Analysis unavailable");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }

  const result = job?.interpretation;
  const evidence = result ? [...result.context.facts, ...result.context.signals, ...result.context.relevant_documents, ...result.context.previous_interpretations] : [];
  function showReading(reading: Reading) {
    return <div className="space-y-3"><p className="font-medium">{reading.thesis}</p>{reading.claims.map((claim, index) =>
      <div key={index} className="border-l-2 border-neutral-200 pl-4"><p>{claim.statement}</p>
        <ul className="text-sm text-neutral-600">{claim.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul>
        <div className="mt-2 flex flex-wrap gap-3">{claim.evidence.map((link, i) =>
          <a key={i} href={`#evidence-${link.evidence_id}`} title={link.rationale} className="text-sm underline">Evidence {evidence.findIndex(item => item.id === link.evidence_id) + 1} ({link.relationship})</a>)}</div>
      </div>)}</div>;
  }
  return <main className="mx-auto max-w-4xl space-y-8 p-6 text-neutral-900">
    <header><h1 className="text-2xl font-semibold">Explain the signals</h1><p className="mt-2 text-neutral-600">Ask a question about your workspace. Examine the explanation, competing readings and source evidence.</p></header>
    <form className="space-y-4" onSubmit={event => { event.preventDefault(); void run(); }}>
      <label className="block">Question<textarea className={field} required maxLength={4000} value={question} onChange={event => setQuestion(event.target.value)} placeholder="Why are organic signups falling?" /></label>
      <label className="block">Objective<input className={field} required maxLength={4000} value={objective} onChange={event => setObjective(event.target.value)} /></label>
      <div className="grid grid-cols-2 gap-4"><label>From<input className={field} type="date" required value={start} max={end} onChange={event => setStart(event.target.value)} /></label><label>Through<input className={field} type="date" required value={end} min={start} onChange={event => setEnd(event.target.value)} /></label></div>
      <p className="text-sm text-neutral-600">Metrics are compared with the preceding period of the same length. Relevant documents may come from other dates.</p>
      <fieldset className="space-y-2"><legend className="font-medium">Metric sources (optional, up to four)</legend>
        {datasets.map((dataset, index) => <label key={`${dataset.source}:${dataset.property_id}:${dataset.dataset}`} className="block text-sm"><input type="checkbox" checked={selected.includes(index)} disabled={!selected.includes(index) && selected.length >= 4} onChange={event => setSelected(current => event.target.checked ? [...current, index] : current.filter(item => item !== index))} /> {dataset.dataset} · {dataset.source} · {dataset.property_id}</label>)}
        {!datasets.length && !sourceError && <p className="text-sm">No structured metric sources are available. This run can use your documents.</p>}
        {sourceError && <p className="text-sm text-amber-800">{sourceError}</p>}
      </fieldset>
      <button className="rounded-lg bg-neutral-900 px-5 py-3 text-white disabled:opacity-50" disabled={busy}>{busy ? "Analyzing evidence…" : "Explain"}</button>
    </form>
    {job && <p role="status">Analysis: {job.status}</p>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {job && !busy && ["queued", "running"].includes(job.status) && <button className="underline" onClick={() => void run(true)}>Check status</button>}
    {job?.status === "failed" && !busy && <button className="underline" onClick={() => { retry.current = null; void run(); }}>Retry analysis</button>}
    {result && <section className="space-y-6">
      <h2 className="text-xl font-semibold">{result.synthesis.outcome === "insufficient_evidence" ? "Insufficient evidence" : "Explanation"}</h2>
      {result.synthesis.primary && showReading(result.synthesis.primary)}
      <p><strong>Confidence: {result.synthesis.confidence.level}.</strong> {result.synthesis.confidence.rationale}</p>
      {result.synthesis.alternatives.map((reading, index) => <section key={index}><h3 className="mb-2 font-semibold">Alternative {index + 1}</h3>{showReading(reading)}</section>)}
      <section><h3 className="font-semibold">Missing context</h3><ul className="list-disc space-y-1 pl-5">{result.synthesis.missing_context.map((gap, i) => <li key={i}>{gap}</li>)}</ul></section>
      <section className="space-y-2"><h3 className="font-semibold">Source evidence</h3>{evidence.map((item, index) => <details key={item.id} id={`evidence-${item.id}`} className="rounded-lg border p-3"><summary className="cursor-pointer">{index + 1}. {item.title ?? item.metric ?? item.statement ?? item.kind}</summary><p className="mt-2 whitespace-pre-wrap">{item.text ?? item.thesis ?? (item.value !== undefined ? `${item.value} ${item.unit ?? ""}` : item.statement)}</p>{item.period && <p className="text-sm">{item.period.start} through {item.period.end}</p>}<p className="break-all text-xs text-neutral-500">{item.locator}</p><ul className="mt-2 text-sm">{item.limitations.map((limitation, i) => <li key={i}>{limitation}</li>)}</ul></details>)}</section>
    </section>}
  </main>;
}
