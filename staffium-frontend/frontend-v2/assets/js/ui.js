// DOM helpers and shared components.
//
// XSS rule for the whole frontend: data from the API is only ever inserted as text (textContent)
// or through validated attributes. Nothing here uses innerHTML, and links built from user data
// must be http(s), so stored values like "javascript:..." can never execute.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") Object.assign(el.style, v);               // CSSOM: allowed under a strict CSP
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "href") el.setAttribute("href", safeHref(v));
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

export function safeHref(url) {
  const s = String(url || "");
  if (s.startsWith("#/") || s.startsWith("/") || /^https?:\/\//i.test(s) || s.startsWith("mailto:")) return s;
  return "#";
}

export function extLink(url, label) {
  if (!url || !/^https?:\/\//i.test(url)) return null;
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label || url.replace(/^https?:\/\//, ""));
}

// ------------------------------------------------------------------ icons (stroke paths, 24x24)
const ICONS = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zm9 3-4.35-4.35",
  layers: "M12 3 2 8l10 5 10-5zM2 13l10 5 10-5M2 18l10 5 10-5",
  calendar: "M7 3v3m10-3v3M4 8h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",
  bell: "M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9m4.3 13a1.94 1.94 0 0 0 3.4 0",
  briefcase: "M9 6V4h6v2M4 7h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1zM3 13h18",
  building: "M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16M16 9h2a2 2 0 0 1 2 2v10M2 21h20M8 7h4M8 11h4M8 15h4",
  chart: "M4 20V10m6 10V4m6 16v-7m4 7H2",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  plus: "M12 5v14M5 12h14",
  x: "M18 6 6 18M6 6l12 12",
  check: "M20 6 9 17l-5-5",
  upload: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3",
  menu: "M3 6h18M3 12h18M3 18h18",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zm0-6v-4m0-4h.01",
  alert: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4m0 4h.01",
  link: "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71",
  copy: "M9 9h11v11H9zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1",
  refresh: "M21 2v6h-6M3 22v-6h6M3.5 9a9 9 0 0 1 14.85-3.36L21 8M3 16l2.65 2.36A9 9 0 0 0 20.5 15",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6",
  compare: "M8 3v18M16 3v18M3 8h5M16 16h5",
  "chevron-down": "m6 9 6 6 6-6",
  "chevron-right": "m9 18 6-6-6-6",
  "chevrons-up-down": "m7 15 5 5 5-5M7 9l5-5 5 5",
  "chevrons-left": "m11 17-5-5 5-5M18 17l-5-5 5-5",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42",
  moon: "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z",
  monitor: "M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM8 21h8M12 17v4",
  "arrow-right": "M5 12h14M12 5l7 7-7 7",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  zap: "M13 2 3 14h9l-1 8 10-12h-9z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
};

export function icon(name, extra = {}) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", extra.width || "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  if (extra.class) svg.setAttribute("class", extra.class);
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", ICONS[name] || ICONS.info);
  svg.append(p);
  return svg;
}

export function brandMark() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "brand-mark");
  svg.setAttribute("aria-hidden", "true");
  // Three bars of decreasing length: a list being narrowed to a shortlist.
  // Colours come from theme.css (.brand-mark rect).
  [[3, 5, 18], [3, 11, 12], [3, 17, 6]].forEach(([x, y, w]) => {
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", x); r.setAttribute("y", y); r.setAttribute("width", w); r.setAttribute("height", 3); r.setAttribute("rx", 1.5);
    svg.append(r);
  });
  return svg;
}

// ------------------------------------------------------------------ formatting
const dtf = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const dttf = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
export const fmtDate = (v) => (v ? dtf.format(new Date(v)) : "-");
export const fmtDateTime = (v) => (v ? dttf.format(new Date(v)) : "-");
export function fmtAgo(v) {
  if (!v) return "-";
  const s = (Date.now() - new Date(v).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return fmtDate(v);
}
export function fmtMoney(v, currency = "INR") {
  if (v === null || v === undefined) return "-";
  try { return new Intl.NumberFormat("en-IN", { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(v)); }
  catch { return `${currency} ${Number(v).toLocaleString()}`; }
}
export function fmtSalaryRange(min, max) {
  if (!min && !max) return null;
  const f = (x) => fmtMoney(x);
  return min && max ? `${f(min)} - ${f(max)}` : min ? `From ${f(min)}` : `Up to ${f(max)}`;
}
export const fmtYears = (v) => (v === null || v === undefined ? "-" : `${Number(v).toFixed(1).replace(/\.0$/, "")} yrs`);
export const humanize = (s) => (s ? String(s).toLowerCase().replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "");
export const initials = (name) => String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");

// ------------------------------------------------------------------ status badges
const STATUS_COLOR = {
  APPLIED: "", SCREENING: "blue", SHORTLISTED: "violet", INTERVIEW: "amber", OFFER: "green", HIRED: "green",
  REJECTED: "red", WITHDRAWN: "", OPEN: "green", DRAFT: "", PAUSED: "amber", CLOSED: "",
  SCHEDULED: "blue", COMPLETED: "green", CANCELLED: "red", RESCHEDULED: "amber",
  PENDING: "amber", ACCEPTED: "green", EXPIRED: "",
  UPLOADED: "", PROCESSING: "blue", FAILED: "red",
  STRONG_MATCH: "green", MATCH: "blue", WEAK_MATCH: "amber", REJECT: "red",
};
export const badge = (status, label) => h("span", { class: `badge ${STATUS_COLOR[status] || ""}` }, label || humanize(status));

// ------------------------------------------------------------------ score meter
export function score(value, { large = false } = {}) {
  if (value === null || value === undefined) return h("span", { class: "faint small" }, "Not scored");
  const v = Math.max(0, Math.min(100, Number(value)));
  const tone = v >= 65 ? "" : v >= 45 ? "mid" : "low";
  return h("span", { class: `score ${large ? "score-lg" : ""}`, title: `${v.toFixed(1)} / 100` },
    h("span", { class: "val" }, Math.round(v)),
    h("span", { class: `meter ${tone}`, role: "meter", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(v) },
      h("span", { style: { width: `${v}%` } })));
}

export function breakdown(rows) {
  const box = h("div", { class: "breakdown" });
  for (const [label, val] of rows) {
    const v = val === null || val === undefined ? null : Math.max(0, Math.min(100, Number(val)));
    box.append(h("span", { class: "muted" }, label),
      h("span", { class: `meter ${v === null ? "" : v >= 65 ? "" : v >= 45 ? "mid" : "low"}` }, h("span", { style: { width: `${v ?? 0}%` } })),
      h("span", { class: "num right" }, v === null ? "-" : Math.round(v)));
  }
  return box;
}

// ------------------------------------------------------------------ feedback: toasts, dialogs
let toastBox;
export function toast(message, kind = "ok") {
  toastBox ??= document.body.appendChild(h("div", { class: "toasts", role: "status", "aria-live": "polite" }));
  const t = h("div", { class: `toast ${kind === "error" ? "error" : ""}` }, message);
  toastBox.append(t);
  setTimeout(() => t.remove(), kind === "error" ? 6000 : 3500);
}

export function errorText(e) {
  return e?.message || "Something went wrong";
}

export function modal({ title, body, footer, wide = false, onClose }) {
  const close = () => { dlg.close(); };
  const dlg = h("dialog", { class: `modal ${wide ? "wide" : ""}`, "aria-label": title },
    h("div", { class: "modal-head" }, h("h2", {}, title),
      h("button", { class: "btn btn-ghost icon-btn", type: "button", "aria-label": "Close", onclick: close }, icon("x"))),
    h("div", { class: "modal-body" }, body),
    footer ? h("div", { class: "modal-foot" }, footer) : null);
  dlg.addEventListener("close", () => { dlg.remove(); onClose?.(); });
  document.body.append(dlg);
  dlg.showModal();
  return { dlg, close };
}

export function confirmDialog(title, message, actionLabel = "Confirm", danger = false) {
  return new Promise((resolve) => {
    let result = false;
    const { close } = modal({
      title,
      body: h("p", { class: "muted" }, message),
      footer: [
        h("button", { class: "btn", type: "button", onclick: () => close() }, "Cancel"),
        h("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, type: "button", onclick: () => { result = true; close(); } }, actionLabel),
      ],
      onClose: () => resolve(result),
    });
  });
}

// ------------------------------------------------------------------ forms
let fieldSeq = 0;
export function field(label, input, { hint, name } = {}) {
  const id = input.id || `f-${name || input.name || "field"}-${++fieldSeq}`;   // unique even when two forms share field names
  input.id = id;
  return h("div", { class: "field", dataset: { field: input.name || name || "" } },
    h("label", { for: id }, label), input, hint ? h("div", { class: "hint" }, hint) : null);
}
export const input = (name, attrs = {}) => h("input", { name, ...attrs });
export const textarea = (name, attrs = {}) => { const t = h("textarea", { name, ...attrs }); if (attrs.value) t.value = attrs.value; return t; };
export function select(name, options, value, attrs = {}) {
  const s = h("select", { name, ...attrs });
  for (const o of options) {
    const [v, label] = Array.isArray(o) ? o : [o, humanize(o)];
    const opt = h("option", { value: v }, label);
    if (String(v) === String(value ?? "")) opt.selected = true;
    s.append(opt);
  }
  return s;
}

export function readForm(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "number") out[el.name] = el.value === "" ? null : Number(el.value);
    else if (el.type === "password") out[el.name] = el.value;   // spaces can be part of a password; send it exactly as typed
    else out[el.name] = el.value.trim() === "" ? null : el.value.trim();
  }
  return out;
}

export function showFormError(form, err) {
  form.querySelectorAll(".err").forEach((e) => e.remove());
  form.querySelectorAll("[aria-invalid]").forEach((e) => e.removeAttribute("aria-invalid"));
  let box = form.querySelector(".form-error");
  if (!err) { box?.remove(); return; }
  if (!box) { box = h("div", { class: "form-error", role: "alert" }); form.prepend(box); }
  box.textContent = errorText(err);
  for (const f of err.fields || []) {
    const name = String(f.field || "").split(".").pop();
    const el = form.querySelector(`[name="${CSS.escape(name)}"]`);
    if (el) {
      el.setAttribute("aria-invalid", "true");
      el.closest(".field")?.append(h("div", { class: "err" }, f.message.replace(/^Value error, /, "")));
    }
  }
}

export async function busy(button, fn) {
  if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
  try { return await fn(); } finally {
    if (button) { button.disabled = false; button.removeAttribute("aria-busy"); }
  }
}

// ------------------------------------------------------------------ layout pieces
export const loading = (text = "Loading") => h("div", { class: "loading" }, h("span", { class: "spinner" }), text);
export function empty(title, text, action) {
  return h("div", { class: "empty" }, h("h3", {}, title), text ? h("p", {}, text) : null, action || null);
}
// Cards have up to three bands: header (title + meta/actions), body, footer (the form's buttons).
export function panel(title, body, { actions, flush = false, footer } = {}) {
  return h("section", { class: "panel" },
    title ? h("div", { class: "panel-head" }, h("h2", {}, title), actions ? h("div", { class: "row" }, actions) : null) : null,
    h("div", { class: `panel-body ${flush ? "flush" : ""}` }, body),
    footer ? h("div", { class: "panel-foot" }, footer) : null);
}

// Shown while the server works on something (e.g. reading an uploaded resume). The bar is
// indeterminate: the API reports a status, not a percentage.
export function processing(title, text) {
  return h("div", { class: "processing", role: "status", "aria-live": "polite" },
    h("div", { class: "processing-icon" }, icon("refresh")),
    h("h3", {}, title), text ? h("p", {}, text) : null,
    h("div", { class: "progress", "aria-hidden": "true" }, h("span")));
}
export function pageHead(title, description, actions) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, title), description ? h("p", {}, description) : null),
    actions ? h("div", { class: "row" }, actions) : null);
}

// On narrow screens each row becomes a card (theme.css). Every cell carries its column label, the
// main column becomes the card title (column option mobile: "title"), and cells without a label are
// treated as actions (mobile: "actions") or, for a row checkbox, pinned to the corner (mobile: "select").
export function table(columns, rows, { onRowClick, emptyState } = {}) {
  if (!rows.length) return emptyState || empty("Nothing here yet");
  let titleIdx = columns.findIndex((c) => c.mobile === "title");
  if (titleIdx < 0) titleIdx = columns.findIndex((c) => c.label && c.label !== "#" && !c.mobile);
  const mobileClass = (c, i) => (i === titleIdx ? "m-title" : c.mobile ? `m-${c.mobile}` : c.label ? "" : "m-actions");
  const tbody = h("tbody");
  for (const r of rows) {
    const tr = h("tr", { class: onRowClick ? "clickable" : "" });
    if (onRowClick) {
      tr.tabIndex = 0;
      tr.addEventListener("click", (e) => { if (!e.target.closest("button, a, input, select")) onRowClick(r); });
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === tr) onRowClick(r); });
    }
    columns.forEach((c, i) => tr.append(h("td", { class: [c.class, mobileClass(c, i)].filter(Boolean).join(" "), "data-label": c.label || null },
      c.render ? c.render(r) : r[c.key] ?? "-")));
    tbody.append(tr);
  }
  return h("div", { class: "table-wrap", "data-stack": true },
    h("table", { class: "table", "data-stack": true },
      h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.class || "", scope: "col" }, c.label)))), tbody));
}

export function pager(data, onPage) {
  const { total, limit, offset } = data;
  if (total <= limit) return null;
  const from = offset + 1, to = Math.min(offset + limit, total);
  return h("div", { class: "pager" },
    h("span", { class: "num" }, `${from}-${to} of ${total}`),
    h("div", { class: "row" },
      h("button", { class: "btn btn-sm", disabled: offset === 0, onclick: () => onPage(Math.max(0, offset - limit)) }, "Previous"),
      h("button", { class: "btn btn-sm", disabled: to >= total, onclick: () => onPage(offset + limit) }, "Next")));
}

export function tabs(items, active, onSelect) {
  return h("div", { class: "tabs", role: "tablist" },
    items.map(([key, label, n]) => h("button", {
      class: "tab", role: "tab", "aria-selected": String(key === active), type: "button", onclick: () => onSelect(key),
    }, label, n !== undefined && n !== null ? h("span", { class: "n" }, n) : null)));
}

export function chips(names, cls = "") {
  if (!names?.length) return h("span", { class: "faint small" }, "None");
  return h("div", { class: "chips" }, names.map((n) => h("span", { class: `chip ${cls}` }, n)));
}

// ------------------------------------------------------------------ review requests
const REVIEW_COLOR = { OPEN: "amber", IN_REVIEW: "blue", RESOLVED: "green", WITHDRAWN: "" };
export const REVIEW_STATUS = { OPEN: "Waiting", IN_REVIEW: "In review", RESOLVED: "Resolved", WITHDRAWN: "Withdrawn" };
export const REVIEW_OUTCOME = { UPHELD: "Decision upheld", RESCREENED: "Re-screened", OVERRIDDEN: "Score adjusted", REINSTATED: "Reinstated" };
export const REVIEW_FIELD = { SKILLS: "Skills", EXPERIENCE_YEARS: "Years of experience", EDUCATION: "Education", WORK_HISTORY: "Work history", OTHER: "Something else" };
export const reviewBadge = (status) => h("span", { class: `badge ${REVIEW_COLOR[status] || ""}` }, REVIEW_STATUS[status] || humanize(status));

