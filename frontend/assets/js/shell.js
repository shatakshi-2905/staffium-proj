// App shell shared by both portals: auth guard, sidebar, top bar, hash router, notifications.
import { get, session, signOut } from "./api.js";
import { brandMark, clear, empty, errorText, h, icon, initials, loading } from "./ui.js";

export async function startApp({ portal, nav, routes, onboarding }) {
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

  const content = h("main", { class: "content", id: "content", tabindex: "-1" });
  const crumbs = h("nav", { class: "crumbs", "aria-label": "Breadcrumb" });
  const bellCount = h("span", { class: "count", hidden: true });
  const navEl = h("nav", { class: "nav", "aria-label": "Main" });
  const shell = h("div", { class: "shell" });

  const displayName = me.profile ? `${me.profile.first_name} ${me.profile.last_name}` : me.email;
  const sidebar = h("aside", { class: "sidebar" },
    h("a", { class: "brand", href: "#/" }, brandMark(), "Shortlist", h("small", {}, portal === "recruiter" ? "Recruiter" : "Applicant")),
    navEl,
    h("div", { class: "sidebar-foot" },
      h("div", { class: "who" }, h("span", { class: "avatar" }, initials(displayName)),
        h("div", { class: "who-text" }, h("div", {}, displayName),
          h("div", { class: "faint xs" }, me.profile?.company_name || me.email))),
      h("button", { class: "btn btn-ghost btn-sm", style: { justifyContent: "flex-start" }, onclick: async () => {
        await signOut();
        window.location.replace(`/${portal}/login.html`);
      } }, icon("logout"), "Sign out")));

  const topbar = h("header", { class: "topbar" },
    h("button", { class: "btn btn-ghost icon-btn menu-btn", "aria-label": "Open menu", onclick: () => {
      shell.dataset.navOpen = shell.dataset.navOpen === "true" ? "false" : "true";
    } }, icon("menu")),
    crumbs);
  shell.addEventListener("click", (e) => { if (e.target === shell) shell.dataset.navOpen = "false"; });

  clear(shell, sidebar, h("div", { class: "main" }, topbar, content));
  clear(root, shell);

  if (!me.onboarded) {
    navEl.append(h("span", { class: "nav-group" }, "Finish setting up to continue"));
    clear(crumbs, h("span", { class: "here" }, "Get started"));
    onboarding(content, me);
    return;
  }

  // ---- navigation
  const navLinks = {};
  for (const item of nav) {
    if (item.group) { navEl.append(h("span", { class: "nav-group" }, item.group)); continue; }
    const a = h("a", { href: `#/${item.path}` }, icon(item.icon), item.label, item.key === "notifications" ? bellCount : null);
    navLinks[item.key] = a;
    navEl.append(a);
  }

  async function refreshUnread() {
    try {
      const n = (await get("/api/notifications", { unread: true, limit: 1 })).unread;
      bellCount.hidden = !n;
      bellCount.textContent = n > 99 ? "99+" : String(n);
    } catch { /* non-critical */ }
  }

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
    for (const [key, a] of Object.entries(navLinks)) a.toggleAttribute("aria-current", key === match.nav);
    if (navLinks[match.nav]) navLinks[match.nav].setAttribute("aria-current", "page");
    clear(crumbs, h("span", { class: "here" }, match.title));
    clear(content, loading());
    document.title = `${match.title} - Shortlist`;
    const ctx = {
      me, params, query: new URLSearchParams(queryPart || ""), el: content,
      alive: () => token === navToken,
      crumbs: (items) => {
        if (token !== navToken) return;
        clear(crumbs, items.flatMap(([label, href], i) => [
          i ? h("span", { class: "sep", "aria-hidden": "true" }, "/") : null,
          href ? h("a", { href }, label) : h("span", { class: "here" }, label),
        ]));
        document.title = `${items[items.length - 1][0]} - Shortlist`;
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

  window.addEventListener("hashchange", route);
  refreshUnread();
  setInterval(() => { if (document.visibilityState === "visible") refreshUnread(); }, 60_000);
  await route();
}
