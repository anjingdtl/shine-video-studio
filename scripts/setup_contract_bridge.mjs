// Execute the shipped TypeScript service; Python forwards fetches to real FastAPI routes.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { pathToFileURL } from 'node:url';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shine-setup-contract-'));
const input = readline.createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
try {
  for (const name of ['api', 'setupExecution']) {
    let source = fs.readFileSync(new URL(`../frontend/src/${name}.ts`, import.meta.url), 'utf8');
    source = source.replace("from './api'", "from './api.mjs'");
    fs.writeFileSync(path.join(temp, name + '.mjs'), ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
    }).outputText);
  }
  const stored = new Map();
  globalThis.localStorage = { getItem: k => stored.get(k), setItem: (k,v) => stored.set(k,v), removeItem: k => stored.delete(k) };
  globalThis.fetch = async (url, init) => {
    process.stdout.write(JSON.stringify({ request: { url, method: init?.method ?? 'GET',
      body: init?.body ? JSON.parse(init.body) : null, headers: Object.fromEntries(new Headers(init?.headers)) } }) + '\n');
    const { value } = await lines.next();
    const response = JSON.parse(value);
    return new Response(JSON.stringify(response.body), { status: response.status });
  };
  const { approveAndExecuteSetupPlan } = await import(pathToFileURL(path.join(temp, 'setupExecution.mjs')));
  const result = await approveAndExecuteSetupPlan(JSON.parse(process.argv[2]));
  process.stdout.write(JSON.stringify({ result }) + '\n');
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String(error) }) + '\n');
} finally {
  input.close();
  fs.rmSync(temp, { recursive: true, force: true });
}
