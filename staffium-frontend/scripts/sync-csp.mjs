#!/usr/bin/env node
// Keeps the API origin in frontend/_headers (CSP connect-src) identical to PROD_API in config.js.
//   node scripts/sync-csp.mjs          rewrite _headers
//   node scripts/sync-csp.mjs --check  exit 1 if they differ (use in CI / before deploy)
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "frontend");
const configPath = join(root, "assets/js/config.js");
const headersPath = join(root, "_headers");

const prod = readFileSync(configPath, "utf8").match(/export const PROD_API = "([^"]+)"/)?.[1];
if (!prod) { console.error("PROD_API not found in config.js"); process.exit(2); }
const origin = new URL(prod).origin;

const headers = readFileSync(headersPath, "utf8");
const re = /(connect-src 'self') (?:https?:\/\/[^\s;]+ ?)*/;
if (!re.test(headers)) { console.error("connect-src not found in _headers"); process.exit(2); }

const fixed = headers.replace(/connect-src 'self'(?: https?:\/\/[^\s;]+)*/, `connect-src 'self' ${origin}`);

if (process.argv.includes("--check")) {
  if (fixed !== headers) { console.error(`_headers is out of date: expected connect-src to allow ${origin}. Run: node scripts/sync-csp.mjs`); process.exit(1); }
  console.log(`ok: _headers allows ${origin}`);
} else if (fixed === headers) {
  console.log(`_headers already allows ${origin}`);
} else {
  writeFileSync(headersPath, fixed);
  console.log(`_headers updated: connect-src now allows ${origin}`);
}
