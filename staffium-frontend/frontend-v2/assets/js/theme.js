// Theme preference: "light" | "dark" | "system". The pre-paint half lives in theme-init.js.
const KEY = "staffium.theme";
const query = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function getMode() {
  try { return localStorage.getItem(KEY) || "system"; } catch { return "system"; }
}

export const resolvedTheme = (mode = getMode()) => (mode === "dark" || (mode === "system" && query?.matches) ? "dark" : "light");

export function applyTheme(mode = getMode()) {
  document.documentElement.setAttribute("data-theme", resolvedTheme(mode));
}

export function setMode(mode) {
  try { localStorage.setItem(KEY, mode); } catch { /* storage blocked: the choice lasts for this page only */ }
  applyTheme(mode);
  window.dispatchEvent(new CustomEvent("staffium:theme", { detail: mode }));
}

// Follow the operating system while the preference is "system".
query?.addEventListener?.("change", () => { if (getMode() === "system") applyTheme("system"); });
