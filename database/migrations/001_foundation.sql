-- 001_foundation.sql
-- Extensions, private schema and the restricted database role the API runs user requests as.
--
-- Why a dedicated role: Supabase exposes the `public` schema over its auto-generated REST API to the
-- `anon` and `authenticated` roles. This project does not use that API, so those roles get no access
-- (see 005_security.sql). Instead the FastAPI backend connects as `postgres` and, for every request
-- made on behalf of a user, switches to `app_user` inside the transaction and sets the caller's JWT
-- claims. Row-Level Security policies are written against `app_user`, so they are enforced for every
-- user-initiated query no matter which endpoint issued it.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector     WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA extensions;

-- Private schema for helper functions; never exposed over any API.
CREATE SCHEMA IF NOT EXISTS app;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user NOLOGIN NOINHERIT NOBYPASSRLS;
    END IF;
END $$;

-- Lets the connecting role (postgres on Supabase) do SET ROLE app_user.
GRANT app_user TO CURRENT_USER;

GRANT USAGE ON SCHEMA public, app, extensions TO app_user;
