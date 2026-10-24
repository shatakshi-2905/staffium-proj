// Where the API lives. Set this to your Render backend URL before deploying, and add the same
// origin to connect-src in the Content-Security-Policy (see _headers / render.yaml).
const LOCAL = ["localhost", "127.0.0.1"].includes(window.location.hostname);

export const API_BASE = LOCAL ? "http://localhost:8000" : "https://shortlist-api.onrender.com";
export const APP_NAME = "Shortlist";
