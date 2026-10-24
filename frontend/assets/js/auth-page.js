// Sign in / create account / forgot password for one portal (data-portal on <body>).
import { publicPost, session } from "./api.js";
import { busy, clear, field, h, input, readForm, showFormError } from "./ui.js";

const portal = document.body.dataset.portal;            // "applicant" | "recruiter"
const appUrl = `/${portal}/app.html`;
const params = new URLSearchParams(window.location.search);

// Supabase's email-confirmation redirect carries tokens in the URL fragment; never leave them there.
if (window.location.hash) history.replaceState(null, "", window.location.pathname + window.location.search);

const existing = session.get();
if (existing?.user?.role === portal.toUpperCase() && existing.expires_at > Date.now()) {
  window.location.replace(appUrl);
}

const root = document.getElementById("auth-root");
const title = document.getElementById("auth-title");
const lede = document.getElementById("auth-lede");
const originalLede = lede.textContent;

function note() {
  if (params.has("confirmed")) return h("div", { class: "form-note" }, "Email confirmed. Sign in to continue.");
  if (params.has("expired")) return h("div", { class: "form-note" }, "Your session ended. Sign in again.");
  return null;
}

function signIn() {
  title.textContent = "Sign in";
  lede.textContent = originalLede;
  const form = h("form", { class: "form", novalidate: true },
    note(),
    field("Email", input("email", { type: "email", autocomplete: "email", required: true, maxlength: 254 })),
    field("Password", input("password", { type: "password", autocomplete: "current-password", required: true, maxlength: 72 })),
    h("button", { class: "btn btn-primary btn-lg btn-block", type: "submit" }, "Sign in"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    const data = readForm(form);
    await busy(form.querySelector("[type=submit]"), async () => {
      try {
        const s = await publicPost("/api/auth/login", { ...data, portal });
        session.set(s);
        window.location.replace(appUrl);
      } catch (err) {
        showFormError(form, err);
        if (err.code === "wrong_portal") {
          const other = portal === "applicant" ? "recruiter" : "applicant";
          form.querySelector(".form-error").append(" ", h("a", { href: `/${other}/login.html` }, `Go to the ${other} portal`));
        }
      }
    });
  });
  clear(root, form, h("div", { class: "auth-switch" },
    h("span", {}, "New here? ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); signUp(); } }, "Create an account")),
    h("span", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); forgot(); } }, "Forgot your password?"))));
  form.querySelector("input").focus();
}

function signUp() {
  title.textContent = "Create your account";
  lede.textContent = portal === "recruiter"
    ? "You'll set up your company, or join one with an invite code, right after."
    : "You'll add your profile and resume right after.";
  const form = h("form", { class: "form", novalidate: true },
    field("Work email", input("email", { type: "email", autocomplete: "email", required: true, maxlength: 254 })),
    field("Password", input("password", { type: "password", autocomplete: "new-password", required: true, minlength: 8, maxlength: 72 }),
      { hint: "At least 8 characters, with upper and lower case letters and a number." }),
    h("button", { class: "btn btn-primary btn-lg btn-block", type: "submit" }, "Create account"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    const data = readForm(form);
    await busy(form.querySelector("[type=submit]"), async () => {
      try {
        const res = await publicPost("/api/auth/signup", { ...data, portal });
        if (!res.needs_confirmation) {
          const s = await publicPost("/api/auth/login", { ...data, portal });
          session.set(s);
          window.location.replace(appUrl);
          return;
        }
        clear(root, h("div", { class: "form-note" }, res.message),
          h("div", { class: "auth-switch" }, h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); signIn(); } }, "Back to sign in")));
      } catch (err) { showFormError(form, err); }
    });
  });
  clear(root, form, h("div", { class: "auth-switch" },
    h("span", {}, "Already have an account? ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); signIn(); } }, "Sign in"))));
  form.querySelector("input").focus();
}

function forgot() {
  title.textContent = "Reset your password";
  lede.textContent = "We'll email you a link to set a new one.";
  const form = h("form", { class: "form", novalidate: true },
    field("Email", input("email", { type: "email", autocomplete: "email", required: true })),
    h("button", { class: "btn btn-primary btn-lg btn-block", type: "submit" }, "Send reset link"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    await busy(form.querySelector("[type=submit]"), async () => {
      try {
        const res = await publicPost("/api/auth/forgot-password", { ...readForm(form), portal });
        clear(root, h("div", { class: "form-note" }, res.message),
          h("div", { class: "auth-switch" }, h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); signIn(); } }, "Back to sign in")));
      } catch (err) { showFormError(form, err); }
    });
  });
  clear(root, form, h("div", { class: "auth-switch" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); signIn(); } }, "Back to sign in")));
}

signIn();
