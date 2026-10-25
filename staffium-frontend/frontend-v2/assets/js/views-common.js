// Views used by both portals.
import { get, post } from "./api.js";
import { clear, empty, errorText, fmtAgo, h, icon, pageHead, panel, toast } from "./ui.js";

export async function notificationsPage(ctx) {
  const data = await get("/api/notifications", { limit: 50 });
  if (!ctx.alive()) return;
  const markAll = h("button", { class: "btn btn-sm", disabled: !data.unread, onclick: async () => {
    try {
      await post("/api/notifications/read-all"); ctx.refreshUnread(); ctx.rerender();
    } catch (err) { toast(errorText(err), "error"); }
  } }, icon("check"), "Mark all as read");
  clear(ctx.el, pageHead("Notifications", null, markAll),
    panel(null, data.items.length ? h("div", { class: "list" }, data.items.map((n) => h("div", { class: `list-item ${n.is_read ? "" : "unread"}` },
      n.is_read ? h("span", { style: { width: "6px" } }) : h("span", { class: "dot", "aria-label": "Unread" }),
      h("div", { class: "grow" }, n.link ? h("a", { href: n.link, class: "title-cell", style: { textDecoration: "none" } }, n.title) : h("div", { class: "title-cell" }, n.title),
        n.body ? h("div", { class: "sub" }, n.body) : null),
      h("span", { class: "faint xs nowrap" }, fmtAgo(n.created_at)))))
      : empty("You're all caught up", "New activity will appear here."), { flush: true }));
}


