// Run in the Studio directory: node scripts/test-interpretations-proxy.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { createRequire } = require('node:module');
const { NextRequest } = require('next/server');

const file = path.resolve('app/api/interpretations/route.ts');
const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const route = { exports: {} };
new Function('module', 'exports', 'require', compiled)(route, route.exports, createRequire(file));

async function main() {
  process.env.WORKER_URL = 'https://worker.example.test';
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return Response.json({ id: 'test-job', status: 'queued' }, { status: options.method === 'POST' ? 202 : 200 });
  };
  const base = 'https://studio.example.test/api/interpretations?workspace_id=test-workspace';
  const denied = await route.exports.GET(new NextRequest(base + '&kind=datasets'));
  assert.equal(denied.status, 401);
  assert.equal(calls.length, 0);
  const headers = { authorization: 'Bearer test-session', 'idempotency-key': 'test-idempotency' };
  const body = JSON.stringify({ question: 'Why?', frame: {} });
  const submitted = await route.exports.POST(new NextRequest(base, { method: 'POST', headers, body }));
  assert.equal(submitted.status, 202);
  assert.equal(calls[0].url, 'https://worker.example.test/interpretations?workspace_id=test-workspace');
  assert.equal(calls[0].options.headers['Idempotency-Key'], 'test-idempotency');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer test-session');
  assert.equal(calls[0].options.body, body);
  const data = await route.exports.GET(new NextRequest(base + '&kind=datasets', { headers }));
  assert.equal(data.status, 200);
  assert.match(calls[1].url, /\/interpretations\/datasets\?/);
  const invalid = await route.exports.GET(new NextRequest(base + '&id=../../elsewhere', { headers }));
  assert.equal(invalid.status, 400);
  global.fetch = async () => Response.json({ detail: 'private backend error' }, { status: 500 });
  const unavailable = await route.exports.GET(new NextRequest(base + '&kind=datasets', { headers }));
  assert.equal(unavailable.status, 503);
  assert.doesNotMatch(await unavailable.text(), /private/);
  console.log('Interpretation proxy checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
