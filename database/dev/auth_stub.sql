-- LOCAL DEVELOPMENT ONLY. Supabase already provides auth.users; this stub lets the migrations and
-- tests run against a plain PostgreSQL instance. Never run this against Supabase.
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT,
    raw_user_meta_data  JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
