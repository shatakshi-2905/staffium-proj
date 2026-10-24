# Shortlist: notes for Claude Code

Applicant tracking system with AI resume screening, built database-first on PostgreSQL (Supabase).
DBMS college project, also a portfolio piece: database design and security quality matter most.

## Layout
- `database/migrations/*.sql`: schema, functions, views, triggers, RLS. Applied by `database/migrate.py`.
- `backend/`: FastAPI. `app/routers/` (HTTP), `app/services/` (S3, Bedrock, embeddings, ranking, email worker).
- `frontend/`: static HTML/CSS + vanilla JS ES modules. No framework, no build step. Hosted separately from the API.
- `docs/technical-design.md`: the design document (source of truth for scope).

## Commands
- Run API: `cd backend && uvicorn app.main:app --reload --port 8000`
- Run frontend: `cd frontend && python -m http.server 5500`
- Tests (need a throwaway Postgres + pgvector, never Supabase):
  `DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test python database/migrate.py --local`
  then `cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test pytest -q`

## Rules
- Never read, print or edit `.env` or any secret. Config names are in `backend/.env.example`.
- Never run migrations or seed scripts against the Supabase database unless I explicitly ask.
- Schema changes go in a NEW numbered migration file. Never edit a migration that has been applied.
- Every user-initiated query runs through `db.user_tx(user_id)` so RLS applies. `system_tx()` is only for
  internal work (resume processing, worker, auth lookup) after the code has already authorised the action.
- New tables: enable RLS, add policies for `app_user`, grant only the columns users may change (see `005_security.sql`).
- Business rules that must always hold belong in triggers/constraints, raising SQLSTATE `SL4xx` with a
  user-readable message (mapped to HTTP status in `app/errors.py`).
- Request bodies use Pydantic models extending `Strict` in `app/schemas.py`. List endpoints are paginated via `Page`.
- Frontend: never use `innerHTML`; build DOM with `h()` from `assets/js/ui.js`. No inline scripts, no CDNs (strict CSP).
- UI style: Supabase-like dark theme. Use tokens in `theme.css`; borders not shadows; accent only for primary actions.
- Add or update tests in `backend/tests/` for every behaviour change, and run them before saying something works.
