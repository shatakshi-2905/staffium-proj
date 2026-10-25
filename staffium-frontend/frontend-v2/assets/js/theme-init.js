// Runs before first paint (classic script in <head>) so there is no flash of the wrong theme.
// Kept as an external file because the CSP forbids inline scripts. Logic mirrors theme.js.
(function () {
  var mode = "system";
  try { mode = localStorage.getItem("staffium.theme") || "system"; } catch (e) { /* storage blocked */ }
  var dark = mode === "dark" || (mode === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
})();
