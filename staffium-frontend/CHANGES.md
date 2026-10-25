# Staffium.ai frontend: redesign and fixes

## Run it
Static site, no build. Serve `frontend/` from any static host. Before deploying, if the API URL changed:
`node scripts/sync-csp.mjs` (writes the origin from `config.js` into `_headers`); `--check` verifies it.

## Design
- New `assets/css/theme.css`: warm neutrals, one burnt-orange accent (actions, focus, current page, upload),
  Inter (self-hosted in `assets/fonts/`), light and dark themes from the same tokens.
- Cards have header / body / footer bands; form buttons live in the footer.
- Sidebar: grouped, collapsible, nested items, Ctrl/Cmd+K quick jump, user menu with Light/Dark/System.
- Tables become labelled cards on phones; the application board snaps lane by lane.
- `theme-init.js` (external, CSP-safe) applies the saved theme before first paint.

## Fixes
- Passwords are no longer trimmed (`readForm`).
- API origin has one source (`config.js`); `_headers` is generated from it.
- Profile deletes use an explicit id key per section (with a logged fallback).
- Onboarding re-routes in place (no `location.reload()`).
- Polling timer and global listeners are disposed on sign out.
- `human_score` check tolerates `undefined`.
- Unique form-field ids; `.rank` no longer assumes a table cell.

## Deliberately not changed
- Session tokens stay in `localStorage` (httpOnly cookies need backend changes). The strict CSP is the mitigation.
- No linter/tests (kept zero-build).
- Resume "processing" bar is indeterminate: the API reports a status, not a percentage.
