// App shell shared by both portals: auth guard, sidebar (collapsible, nested items), user menu with
// theme control, Ctrl/Cmd+K quick jump, hash router, notification counts.
import { get, session, signOut } from "./api.js";
import { applyTheme, getMode, setMode } from "./theme.js";
import { brandMark, clear, empty, errorText, h, icon, initials, loading } from "./ui.js";

const NAV_KEY = "staffium.nav";
const readCollapsed = () => { try { return localStorage.getItem(NAV_KEY) === "collapsed"; } catch { return false; } };
const saveCollapsed = (v) => { try { localStorage.setItem(NAV_KEY, v ? "collapsed" : "open"); } catch { /* not persisted */ } };
const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");

export function startApp(config) {
  applyTheme();
  return boot(config);
}

async function boot(config) {
  const { portal, nav, routes, onboarding, quick = [] } = config;
  const s = session.get();
  if (!s || s.user?.role !== portal.toUpperCase()) {
    window.location.replace(`/${portal}/login.html`);
    return;
  }

  const root = document.getElementById("app");
  clear(root, loading("Loading your workspace"));

  let me;
  try {
    me = await get("/api/me");
  } catch (e) {
    clear(root, empty("Can't load your account", errorText(e),
      h("button", { class: "btn", onclick: () => window.location.reload() }, "Try again")));
    return;
  }
  if (me.role !== portal.toUpperCase()) {
    await signOut();
    window.location.replace(`/${portal}/login.html`);
    return;
  }

  // Everything that outlives a render (timers, global listeners) is registered here and removed on sign out.
  const disposers = [];
  const listen = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); disposers.push(() => target.removeEventListener(type, fn, opts)); };
  const dispose = () => { while (disposers.length) disposers.pop()(); };
  const leave = async () => { dispose(); await signOut(); window.location.replace(`/${portal}/login.html`); };

  const shell = h("div", { class: "shell" });
  shell.dataset.collapsed = String(readCollapsed());
  shell.dataset.navOpen = "false";
  const content = h("main", { class: "content", id: "content", tabindex: "-1" });
  const crumbs = h("nav", { class: "crumbs", "aria-label": "Breadcrumb" });
  const navEl = h("nav", { class: "nav", "aria-label": "Main" });
  const bellCount = h("span", { class: "count", hidden: true });
  const bellDot = h("span", { class: "bell-dot", hidden: true });

  const displayName = me.profile ? `${me.profile.first_name} ${me.profile.last_name}` : me.email;
  const wordmark = () => h("span", { class: "wordmark" }, "Staffium", h("span", { class: "tld" }, ".ai"));
  const portalLabel = portal === "recruiter" ? "Recruiter" : "Applicant";

  // ---- user menu (theme + sign out)
  const modeButtons = [["light", "Light", "sun"], ["dark", "Dark", "moon"], ["system", "System", "monitor"]].map(([mode, label, ic]) =>
    h("button", { type: "button", "aria-pressed": String(getMode() === mode), onclick: () => {
      setMode(mode);
      modeButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    } }, icon(ic), label));
  ["light", "dark", "system"].forEach((m, i) => { modeButtons[i].dataset.mode = m; });
  const menu = h("div", { class: "menu", role: "menu", hidden: true },
    h("div", { class: "menu-label" }, "Theme"), h("div", { class: "seg" }, modeButtons),
    h("div", { class: "menu-label" }, "Account"),
    h("button", { class: "btn btn-ghost btn-sm", type: "button", role: "menuitem", onclick: leave }, icon("logout"), "Sign out"));
  const userBtn = h("button", { class: "user", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", title: displayName,
    onclick: () => toggleMenu() },
    h("span", { class: "avatar" }, initials(displayName)),
    h("div", { class: "who-text" }, h("div", { class: "name" }, displayName), h("div", { class: "faint xs" }, me.profile?.company_name || me.email)),
    icon("chevrons-up-down"));
  const footer = h("div", { class: "sidebar-foot" }, menu, userBtn);
  function toggleMenu(force) {
    const open = force ?? menu.hidden;
    menu.hidden = !open;
    userBtn.setAttribute("aria-expanded", String(open));
  }
  listen(document, "click", (e) => { if (!menu.hidden && !footer.contains(e.target)) toggleMenu(false); });
  listen(document, "keydown", (e) => { if (e.key === "Escape") { toggleMenu(false); shell.dataset.navOpen = "false"; } });

  // ---- sidebar
  const startCollapsed = shell.dataset.collapsed === "true";
  const collapseBtn = h("button", { class: "btn btn-ghost icon-btn collapse-btn", type: "button",
    "aria-label": startCollapsed ? "Expand sidebar" : "Collapse sidebar", title: startCollapsed ? "Expand sidebar" : "Collapse sidebar", onclick: () => {
    const next = shell.dataset.collapsed !== "true";
    shell.dataset.collapsed = String(next);
    saveCollapsed(next);
    collapseBtn.setAttribute("aria-label", next ? "Expand sidebar" : "Collapse sidebar");
    collapseBtn.title = next ? "Expand sidebar" : "Collapse sidebar";
  } }, icon("chevrons-left"));
  const searchBtn = h("button", { class: "search-btn", type: "button", "aria-label": "Search pages", title: "Search pages", onclick: () => openPalette() },
    icon("search"), h("span", {}, "Search"), h("kbd", {}, isMac ? "⌘ K" : "Ctrl K"));
  const sidebar = h("aside", { class: "sidebar" },
    h("div", { class: "sidebar-top" },
      h("a", { class: "brand", href: "#/", "aria-label": `Staffium.ai ${portalLabel}` }, brandMark(), wordmark()), collapseBtn),
    searchBtn, navEl, footer);
  sidebar.addEventListener("click", (e) => { if (e.target.closest("a")) shell.dataset.navOpen = "false"; });

  const topbar = h("header", { class: "topbar" },
    h("button", { class: "btn btn-ghost icon-btn", type: "button", "aria-label": "Open menu", onclick: () => {
      shell.dataset.navOpen = shell.dataset.navOpen === "true" ? "false" : "true";
    } }, icon("menu")),
    h("a", { class: "brand", href: "#/" }, brandMark(), wordmark()),
    h("a", { class: "btn btn-ghost icon-btn bell", href: "#/notifications", "aria-label": "Notifications" }, icon("bell"), bellDot));
  shell.addEventListener("click", (e) => { if (e.target === shell) shell.dataset.navOpen = "false"; });

  clear(shell, sidebar, h("div", { class: "main" }, topbar, h("div", { class: "crumbbar" }, crumbs), content));
  clear(root, shell);

  if (!me.onboarded) {
    navEl.append(h("span", { class: "nav-group" }, "Finish setting up to continue"));
    searchBtn.hidden = true;
    clear(crumbs, h("span", { class: "here" }, "Get started"));
    // done(hash): go to the next screen without a page reload.
    onboarding(content, me, { done: (hash = "#/") => { dispose(); window.location.hash = hash; boot(config); } });
    return;
  }

  // ---- navigation (groups, items, one level of children)
  const navLinks = {};
  const sections = {};
  const openState = {};
  const counters = [[bellCount, async () => {
    const n = (await get("/api/notifications", { unread: true, limit: 1 })).unread;
    bellDot.hidden = !n;
    return n;
  }]];
  const makeCounter = (item) => {
    if (item.key === "notifications") return bellCount;
    if (!item.count) return null;
    const el = h("span", { class: "count", hidden: true });
    counters.push([el, item.count]);
    return el;
  };
  const link = (item, extra = {}) => {
    const a = h("a", { href: `#/${item.path}`, title: item.label, ...extra },
      icon(item.icon || "file"), h("span", { class: "nav-label" }, item.label), makeCounter(item));
    navLinks[item.key] = a;
    return a;
  };
  for (const item of nav) {
    if (item.group) { navEl.append(h("span", { class: "nav-group" }, item.group)); continue; }
    if (!item.children?.length) { navEl.append(link(item)); continue; }
    const sub = h("div", { class: "nav-sub", id: `sub-${item.key}` }, item.children.map((c) => link(c)));
    const toggle = h("button", { class: "nav-toggle", type: "button", "aria-label": `Show ${item.label} sections`, "aria-expanded": "false", "aria-controls": `sub-${item.key}`,
      onclick: () => setOpen(item.key, !openState[item.key]) }, icon("chevron-down"));
    const wrap = h("div", { class: "nav-section" }, h("div", { class: "nav-parent" }, link(item), toggle), sub);
    sections[item.key] = { wrap, toggle, children: item.children.map((c) => c.key) };
    navEl.append(wrap);
  }
  function setOpen(key, open) {
    openState[key] = open;
    sections[key].wrap.dataset.open = String(open);
    sections[key].toggle.setAttribute("aria-expanded", String(open));
  }

  async function refreshUnread() {
    for (const [el, fetchCount] of counters) {
      try {
        const n = await fetchCount();
        el.hidden = !n;
        el.textContent = n > 99 ? "99+" : String(n);
      } catch { /* non-critical */ }
    }
  }

  // ---- quick jump (Ctrl/Cmd + K): jump to any page without touching the sidebar
  const jumpItems = [];
  let group = "";
  for (const item of nav) {
    if (item.group) { group = item.group; continue; }
    jumpItems.push({ label: item.label, hash: `#/${item.path}`, icon: item.icon, hint: group });
    for (const c of item.children || []) jumpItems.push({ label: c.label, hash: `#/${c.path}`, icon: c.icon || item.icon, hint: item.label });
  }
  for (const q of quick) jumpItems.push({ ...q, hint: q.hint || "Action" });

  function openPalette() {
    let active = 0, shown = jumpItems;
    const field = h("input", { type: "text", placeholder: "Jump to a page or action", "aria-label": "Search pages", autocomplete: "off", spellcheck: "false", role: "combobox", "aria-expanded": "true", "aria-controls": "palette-list" });
    const list = h("div", { class: "palette-list", id: "palette-list", role: "listbox" });
    const dlg = h("dialog", { class: "modal palette", "aria-label": "Quick jump" },
      h("div", { class: "palette-input" }, icon("search"), field), list,
      h("div", { class: "palette-foot" }, h("span", {}, "↑↓ to move"), h("span", {}, "Enter to open"), h("span", {}, "Esc to close")));
    const go = (item) => { dlg.close(); if (window.location.hash === item.hash) route(); else window.location.hash = item.hash; };
    const paint = () => {
      clear(list, shown.length ? shown.map((item, i) => h("button", { class: "palette-item", type: "button", role: "option", id: `pal-${i}`, "aria-selected": String(i === active),
        onclick: () => go(item), onmousemove: () => { if (active !== i) { active = i; paint(); } } },
        icon(item.icon || "file"), item.label, h("span", { class: "hint" }, item.hint || ""))) : h("div", { class: "palette-empty" }, "Nothing matches that"));
      field.setAttribute("aria-activedescendant", shown.length ? `pal-${active}` : "");
      list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    };
    field.addEventListener("input", () => {
      const q = field.value.trim().toLowerCase();
      shown = q ? jumpItems.filter((i) => `${i.label} ${i.hint || ""}`.toLowerCase().includes(q)) : jumpItems;
      active = 0;
      paint();
    });
    field.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); paint(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); paint(); }
      else if (e.key === "Enter" && shown[active]) { e.preventDefault(); go(shown[active]); }
    });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // click on the backdrop
    dlg.addEventListener("close", () => dlg.remove());
    document.body.append(dlg);
    paint();
    dlg.showModal();
    field.focus();
  }
  listen(document, "keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (!document.querySelector("dialog[open]")) openPalette();
    }
  });

  // ---- router
  let navToken = 0;
  async function route() {
    const token = ++navToken;
    shell.dataset.navOpen = "false";
    const hash = window.location.hash.replace(/^#\/?/, "");
    const [pathPart, queryPart] = hash.split("?");
    const segments = pathPart.split("/").filter(Boolean);
    let match = null, params = {};
    for (const r of routes) {
      const parts = r.path.split("/").filter(Boolean);
      if (parts.length !== segments.length) continue;
      const p = {};
      if (parts.every((part, i) => (part.startsWith(":") ? ((p[part.slice(1)] = decodeURIComponent(segments[i])), true) : part === segments[i]))) {
        match = r; params = p; break;
      }
    }
    match ??= routes[0];
    for (const [key, a] of Object.entries(navLinks)) {
      if (key === match.nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    for (const [key, sec] of Object.entries(sections)) {
      if (sec.children.includes(match.nav) || key === match.nav) setOpen(key, true);   // keep the current page visible
      else if (openState[key] === undefined) setOpen(key, false);
    }
    clear(crumbs, h("span", { class: "here" }, match.title));
    clear(content, loading());
    document.title = `${match.title} - Staffium.ai`;
    const ctx = {
      me, params, query: new URLSearchParams(queryPart || ""), el: content,
      alive: () => token === navToken,
      crumbs: (items) => {
        if (token !== navToken) return;
        clear(crumbs, items.flatMap(([label, href], i) => [
          i ? icon("chevron-right", { class: "sep" }) : null,
          href ? h("a", { href }, label) : h("span", { class: "here" }, label),
        ]));
        document.title = `${items[items.length - 1][0]} - Staffium.ai`;
      },
      refreshUnread,
      rerender: () => route(),
    };
    try {
      await match.render(ctx);
    } catch (e) {
      if (token === navToken) clear(content, empty("This page couldn't load", errorText(e),
        h("button", { class: "btn", onclick: () => route() }, "Try again")));
    }
    if (token === navToken) content.focus({ preventScroll: true });
  }

  listen(window, "hashchange", route);
  refreshUnread();
  const timer = setInterval(() => { if (document.visibilityState === "visible") refreshUnread(); }, 60_000);
  disposers.push(() => clearInterval(timer));
  listen(document, "visibilitychange", () => { if (document.visibilityState === "visible") refreshUnread(); });
  await route();
}
