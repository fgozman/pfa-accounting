// Refresh the pinned OpenAPI contract from a running backend.
//
// The frontend is a separate project: it does not read the backend's filesystem.
// It talks to the backend over HTTP instead, then regenerates the TypeScript
// types from the freshly downloaded document.
//
//   OPENAPI_URL=http://127.0.0.1:8000/openapi.json npm run sync:openapi
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = join(here, "..", "openapi.json");
const url = process.env.OPENAPI_URL ?? "http://127.0.0.1:8000/openapi.json";

const response = await fetch(url);
if (!response.ok) {
  console.error(`GET ${url} -> ${response.status} ${response.statusText}`);
  process.exit(1);
}
const document = await response.json();
mkdirSync(dirname(target), { recursive: true });
// Match the backend export format (sorted keys) so regenerating types from either
// path produces an identical schema.ts.
const sorted = JSON.stringify(sortKeys(document), null, 2);
writeFileSync(target, `${sorted}\n`);
const paths = Object.keys(document.paths ?? {}).length;
console.log(`Wrote ${target} (${paths} paths, version ${document.info?.version})`);

function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortKeys(value[key])]),
    );
  }
  return value;
}
