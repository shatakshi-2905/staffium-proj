# Shortlist

An applicant tracking system with AI resume screening, built database-first on PostgreSQL.

Applicants build a profile and upload a resume once; it's parsed, embedded and stored. Recruiters
post jobs, rank applicants with a three-stage pipeline (pgvector + full-text retrieval, SQL filters,
Amazon Bedrock Nova re-ranking), run interviews with calendar invites, and send offers. Every
rejected applicant keeps a persisted explanation of what the role needed and how to close the gap.

```
frontend/  static site (HTML, CSS, vanilla JS modules) ......... hosted separately (Render static site)
backend/   FastAPI API + background worker ....................... Render web service
database/  versioned SQL migrations, migration runner ........... Supabase Postgres
scripts/   demo data loader
```

## Architecture

```
Browser ── HTTPS ──> Static frontend (strict CSP, no third-party scripts)
   │
   └── fetch + Bearer JWT ──> FastAPI (rate limits, body limits, security headers, RBAC)
                                 │
            ┌────────────────────┼─────────────────────┬──────────────────┐
            ▼                    ▼                     ▼                  ▼
     Supabase Auth       Supabase Postgres         AWS S3            AWS Bedrock Nova
     (sign-in,           relational + JSONB +      private resumes,  extraction and
      JWT issuing)       pgvector, RLS, triggers   pre-signed GETs   re-ranking
                                 │
                                 └── notifications_outbox ──> worker ──> AWS SES (emails + .ics invites)
```

**Two workflows**

- *Resume processing (once per upload):* bytes validated → S3 → text extracted → structured data
  (Bedrock, rule-based fallback) → skills normalised against the dictionary → 384-d embedding
  (`BAAI/bge-small-en-v1.5`) → one transaction writes everything.
- *Ranking (per search):* job text embedded → **stage 1** pgvector + `tsvector` hybrid retrieval
  (top 100) → **stage 2** SQL filters: experience, must-have skills (`<@` array containment),
  location, notice period (top 20) → **stage 3** Bedrock re-ranks only those 20 → final score is a
  blend of the LLM score and database-computed scores.

## Security model

| Layer | What protects it |
|---|---|
| Sign-in | Proxied through the API to Supabase Auth: 5 attempts/min/IP, generic errors (no account enumeration), portal check before any token is returned; a recruiter signing in on the applicant page is refused and the just-created session revoked |
| Tokens | Verified on every request: signature (JWKS for asymmetric keys, or HS256 secret), issuer, audience, expiry; `alg: none` rejected. Role read from `public.users`, never from the token |
| Roles | `APPLICANT` / `RECRUITER` chosen by the portal you sign up on; `ADMIN` can't be self-assigned (the signup trigger coerces it) |
| Authorization | Route guards per role **and** Postgres Row-Level Security. Every user request runs as the restricted `app_user` role with the caller's id set for the transaction, so a missing check in code still can't read another company's data |
| Column privileges | `app_user` may only `UPDATE applications(status)`, `UPDATE notifications(is_read)`, etc. Changing anything else is a permission error at the database |
| State machine | Legal application status moves live in `application_status_transitions` and are enforced by a trigger (applicants can only withdraw; nobody can set `HIRED` by hand) |
| Supabase REST API | All grants revoked from `anon`/`authenticated`, so the auto-generated REST endpoints expose nothing even if the anon key leaks |
| Uploads | Type detected from bytes (not the name), 5 MB cap enforced while streaming, zip-bomb and macro checks on .docx, page/char caps on PDFs, random server-side S3 keys, SSE on S3, 5-minute pre-signed downloads forced to `attachment` |
| Input | Pydantic models forbid unknown fields, bound every string, allow only `http(s)` URLs; the DB repeats the URL checks |
| XSS | Frontend never uses `innerHTML`; API data is inserted as text. CSP `script-src 'self'` with no inline scripts or third-party CDNs |
| Abuse | Per-user/IP rate limits (login, uploads, searches that cost Bedrock calls), 256 KB JSON body limit, server-capped pagination (max 50) |
| LLM | Prompt-injection hardening: resumes passed as delimited untrusted data, no names/locations sent, output schema-validated and clamped, LLM is at most 45% of the final score |
| Transport/headers | HSTS, `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `no-store`, CORS locked to the frontend origin, API docs disabled in production |
| Audit | Logins, uploads, downloads, status changes, interviews and offers are written to `audit_logs` |

## 1. Supabase

1. **Database → Extensions:** enable `vector` and `btree_gist` (the migration also tries to).
2. **Run the migrations** from your machine (use the *Session pooler* connection string, port 5432):
   ```bash
   pip install asyncpg
   DATABASE_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres' \
     python database/migrate.py
   ```
   Re-running is safe; `--status` lists what's applied.
3. **Authentication → Sign In / Providers → Email:** turn on *Confirm email*. Set the minimum
   password length to 8 and require upper, lower and digits.
4. **Authentication → URL Configuration:** *Site URL* = your frontend URL. Add
   `https://<frontend>/**` to *Redirect URLs* (and `http://localhost:5500/**` for local work).
5. **Project Settings → API:** copy the project URL and the `anon` key. If the project still uses the
   legacy JWT secret, copy it into `SUPABASE_JWT_SECRET`; projects on asymmetric signing keys need
   nothing (the API fetches the public keys).
6. Optional hardening: **Project Settings → Data API**, remove `public` from exposed schemas. The
   migrations already revoke all access for those roles, so this is belt and braces.

Admins are never created through sign-up. To make one, sign up normally, then in the SQL editor:
`UPDATE public.users SET role = 'ADMIN' WHERE email = 'you@example.com';`

## 2. AWS

**S3:** create a bucket in `ap-south-1` with *Block all public access* on and default encryption
enabled. No bucket CORS is needed (downloads are plain navigations to pre-signed URLs).

**Bedrock:** in the Bedrock console, *Model access* → request Amazon Nova Lite. Check which ID your
region exposes:
```bash
aws bedrock list-inference-profiles --region ap-south-1 --type-equals SYSTEM_DEFINED \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId,'nova')].inferenceProfileId"
```
and set `BEDROCK_MODEL_ID` to it (default `apac.amazon.nova-lite-v1:0`).

**SES (optional):** verify your sender address/domain. In sandbox mode SES only delivers to
verified recipients, so verify the demo inboxes too. Set `EMAIL_ENABLED=true` and `SES_FROM_EMAIL`.
With email off, queued emails are marked `SKIPPED` and in-app notifications still work.

**IAM policy** for the API's access key (least privilege):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "Resumes", "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::YOUR-BUCKET/resumes/*" },
    { "Sid": "Nova", "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": ["arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0",
                   "arn:aws:bedrock:*:YOUR-ACCOUNT-ID:inference-profile/*nova-lite*"] },
    { "Sid": "Email", "Effect": "Allow", "Action": ["ses:SendRawEmail", "ses:SendEmail"], "Resource": "*" }
  ]
}
```

## 3. Run locally

```bash
# API
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # fill in real values; .env is git-ignored
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal), any static server on port 5500
cd frontend && python -m http.server 5500
```
Open http://localhost:5500. `FRONTEND_ORIGIN` in `.env` must be `http://localhost:5500`.

## 4. Tests

The suite runs against a throwaway PostgreSQL with pgvector (Docker shown; CI does the same):
```bash
docker run -d --name ats-test -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ats_test -p 5432:5432 pgvector/pgvector:pg16
DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test python database/migrate.py --local
cd backend && TEST_DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test pytest -q
```
`--local` adds a stand-in `auth.users` table; never use it against Supabase. 19 tests cover the
hiring flow end to end, triggers, constraints, RLS isolation, column privileges, token forgery,
portal mismatch, rate limits, upload attacks, headers/CORS and LLM output validation.

## 5. Deploy on Render

1. Push to GitHub. In Render: **New → Blueprint**, pick the repo; `render.yaml` creates
   `shortlist-api` and `shortlist-web`.
2. Fill in the secret env vars when prompted (`DATABASE_URL`, `SUPABASE_*`, `AWS_*`,
   `S3_BUCKET_NAME`, `FRONTEND_ORIGIN` = the `shortlist-web` URL).
3. If your API URL differs from `https://shortlist-api.onrender.com`, change it in
   `frontend/assets/js/config.js` **and** in the CSP `connect-src` in `render.yaml` (and `_headers`).
4. In Supabase, set the Site URL / Redirect URLs to the `shortlist-web` URL.
5. Load demo data for the presentation:
   ```bash
   API_BASE=https://shortlist-api.onrender.com SUPABASE_URL=https://<ref>.supabase.co \
   SUPABASE_SERVICE_ROLE_KEY=... DEMO_PASSWORD='Demo-Passw0rd!' python scripts/seed_demo.py
   ```
   The service-role key is only used here, on your machine. Never put it on Render or in git.

Free Render instances sleep when idle; the first request after a nap takes ~30-60 s while the
embedding model loads. Open the site a minute before you demo.

## Database objects worth showing in the viva

| Concept | Where |
|---|---|
| 3NF, composite keys | `job_skills (job_id, skill_id)`, `candidate_skills`, `company_invites` split from `companies` |
| Partial unique indexes | one active resume per applicant; one live offer per application |
| Exclusion constraints | `interviews`: no overlapping slots per interviewer or per candidate (`btree_gist`, `tstzrange`) |
| Generated columns | `tsvector` search columns on `job_postings` and `resumes` |
| Triggers | status state machine, notifications, outbox, audit, "why not me" feedback, interview/offer rules, application counter |
| Stored functions | `calculate_candidate_experience` (merges overlapping jobs with `range_agg`), `calculate_skill_match`, `get_top_candidates`, `get_application_pipeline`, `get_recruiter_statistics` |
| Views | `candidate_ranking_view` (`RANK()`, `PERCENT_RANK()`), `active_jobs_view`, `recruitment_pipeline_view`, all `security_invoker` |
| Window functions | ranking, `count(*) OVER ()` for pagination totals |
| Transactions | resume processing, applying (insert + counter + notification + audit), outbox lease with `FOR UPDATE SKIP LOCKED` |
| JSONB | `parsed_data`, `strengths`, `missing_skills`, `search_history.filters/results`, outbox payloads |
| Vector search | HNSW indexes on `resume_embeddings` / `job_embeddings`, cosine distance `<=>` |
| RLS + roles | `005_security.sql` |

## Known limits

- Rate limits are in memory: right for one API instance. For several, point slowapi at Redis.
- Scanned (image-only) resumes are rejected with a clear message rather than OCR'd.
- The embedding model is English-centric.
- Interview times are stored in UTC and shown in each viewer's local time zone.
