// Where the API lives. This is the ONE place to set it.
// After changing PROD_API run `node scripts/sync-csp.mjs` so the Content-Security-Policy in
// _headers allows the same origin (the CSP can't read this file, so the script copies it over).
// `node scripts/sync-csp.mjs --check` fails when they differ; wire it into your deploy step.
export const PROD_API = "https://staffium-api.onrender.com";
export const DEV_API = "http://localhost:8000";

const LOCAL = ["localhost", "127.0.0.1"].includes(window.location.hostname);

export const API_BASE = LOCAL ? DEV_API : PROD_API;
export const APP_NAME = "Staffium.ai";
