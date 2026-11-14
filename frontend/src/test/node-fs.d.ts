// Vitest runs in Node; the browser app itself does not import this shim.
declare module 'node:fs' {
  export function readFileSync(path: URL | string, encoding: 'utf8'): string;
}
