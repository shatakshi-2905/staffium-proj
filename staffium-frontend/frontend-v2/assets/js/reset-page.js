// Password reset landing page. Supabase puts a short-lived recovery token in the URL fragment;
// it is read once, kept only in memory, and wiped from the address bar immediately.
import { publicPost } from "./api.js";
import "./public.js";
import { busy, clear, field, h, input, readForm, showFormError } from "./ui.js";

const fragment = new URLSearchParams(window.location.hash.slice(1));
const token = fragment.get("access_token");
const portal = new URLSearchParams(window.location.search).get("portal") === "recruiter" ? "recruiter" : "applicant";
history.replaceState(null, "", window.location.pathname + window.location.search);

const root = document.getElementById("reset-root");

if (!token) {
  clear(root, h("div", { class: "form-error" }, "This reset link is incomplete or has expired. Request a new one from the sign-in page."),
    h("div", { class: "auth-switch" }, h("a", { href: `/${portal}/login.html` }, "Back to sign in")));
} else {
  const form = h("form", { class: "form", novalidate: true },
    field("New password", input("password", { type: "password", autocomplete: "new-password", minlength: 8, maxlength: 72, required: true })),
    h("button", { class: "btn btn-primary btn-lg btn-block", type: "submit" }, "Update password"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    await busy(e.submitter, async () => {
      try {
        const res = await publicPost("/api/auth/reset-password", { access_token: token, password: readForm(form).password });
        clear(root, h("div", { class: "form-note" }, res.message),
          h("div", { class: "auth-switch" }, h("a", { href: `/${portal}/login.html` }, "Sign in")));
      } catch (err) { showFormError(form, err); }
    });
  });
  clear(root, form);
}

