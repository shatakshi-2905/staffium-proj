// Small behaviours shared by the public pages (portal chooser, sign in, reset password):
// the light/dark toggle button.
import { applyTheme, resolvedTheme, setMode } from "./theme.js";
import { clear, icon } from "./ui.js";

applyTheme();

for (const btn of document.querySelectorAll("[data-theme-toggle]")) {
  const paint = () => {
    const dark = resolvedTheme() === "dark";
    clear(btn, icon(dark ? "sun" : "moon"));
    const label = dark ? "Switch to light theme" : "Switch to dark theme";
    btn.setAttribute("aria-label", label);
    btn.title = label;
  };
  btn.addEventListener("click", () => { setMode(resolvedTheme() === "dark" ? "light" : "dark"); paint(); });
  paint();
}
