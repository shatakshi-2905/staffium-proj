"""End-to-end API tests: the full hiring flow plus the security behaviour around it.

External services are replaced with fakes (S3 -> in-memory dict, Supabase Auth -> stub, Bedrock off,
embeddings -> deterministic hash). Tokens are real HS256 JWTs signed with the test secret, so the
real verification code runs. Needs a migrated test database:

    DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test python database/migrate.py --local
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test pytest backend/tests
"""
import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

SECRET = "test-secret-that-is-long-enough-for-hs256-signing"
os.environ.update({
    "ENVIRONMENT": "test", "DATABASE_URL": DSN or "postgresql://unused", "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_ANON_KEY": "anon-test", "SUPABASE_JWT_SECRET": SECRET, "FRONTEND_ORIGIN": "http://localhost:5500",
    "EMBEDDINGS_PROVIDER": "hash", "LLM_ENABLED": "false", "WORKER_ENABLED": "false", "S3_BUCKET_NAME": "test-bucket",
})

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import jwt  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.security import limiter  # noqa: E402
from app.services import mailer, storage, worker  # noqa: E402

BLOB: dict[str, bytes] = {}


@pytest.fixture(scope="module", autouse=True)
async def setup():
    await db.open_pool()

    async def put(key, data, ft):
        BLOB[key] = data

    async def get(key):
        return BLOB[key]

    async def delete(key):
        BLOB.pop(key, None)

    storage.put_resume, storage.get_resume, storage.delete_resume = put, get, delete
    storage.presigned_download = lambda key, name, ft: f"https://s3.test/{key}?sig=1"
    yield
    await db.close_pool()


@pytest.fixture(autouse=True)
def reset_limits():
    limiter.reset()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app, client=("10.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c


def token(uid, **overrides):
    now = int(time.time())
    claims = {"sub": str(uid), "aud": "authenticated", "iss": "https://example.supabase.co/auth/v1",
              "iat": now, "exp": now + 3600, "role": "authenticated", **overrides}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def H(uid, **kw):
    return {"Authorization": f"Bearer {token(uid, **kw)}"}


async def new_user(role):
    uid = uuid.uuid4()
    conn = await asyncpg.connect(DSN)
    await conn.execute("INSERT INTO auth.users (id, email, raw_user_meta_data) VALUES ($1, $2, $3)",
                       uid, f"{role.lower()}-{uid.hex[:8]}@example.com", json.dumps({"role": role}))
    await conn.close()
    return uid


def make_pdf(text: str) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


RESUME_A = """Asha Verma
Backend Engineer | Bengaluru
asha@example.com  +91 98765 43210
Summary: Backend engineer with 4 years of experience building Python services on AWS.
Skills: Python, FastAPI, PostgreSQL, AWS, Docker, Redis, REST APIs, Kubernetes
Experience
Senior Software Engineer, Finlytics (2022 - Present): Built FastAPI microservices on AWS Lambda and Docker.
Software Engineer, DataNest (2020 - 2022): PostgreSQL query tuning and Redis caching for APIs.
Education: B.Tech Computer Science, VIT Vellore, 2016 - 2020
Projects: Fraud detection pipeline with scikit-learn and Kafka."""

RESUME_B = """Rohan Iyer
Frontend Developer | Pune
Summary: Frontend developer with 2 years of experience in React, TypeScript, HTML and CSS.
Skills: React, TypeScript, JavaScript, HTML, CSS, Figma, Jest
Experience: Frontend Developer, PixelWorks (2022 - 2024). Built design systems in React.
Education: BCA, Pune University, 2019 - 2022"""

JOB_DESCRIPTION = ("We are hiring a backend engineer to design and run Python services on AWS. You will build FastAPI "
                   "microservices, tune PostgreSQL, containerise with Docker and own production reliability.")


async def skill_ids(names):
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch("SELECT skill_name, skill_id FROM skills WHERE skill_name = ANY($1::text[])", names)
    await conn.close()
    return {r["skill_name"]: r["skill_id"] for r in rows}


async def onboard_applicant(client, resume_text, discoverable=True, first="Asha"):
    uid = await new_user("APPLICANT")
    r = await client.post("/api/applicant/profile", headers=H(uid),
                          json={"first_name": first, "last_name": "Test", "is_discoverable": discoverable, "location": "Bengaluru"})
    assert r.status_code == 201, r.text
    r = await client.post("/api/resumes", headers=H(uid), files={"file": ("cv.pdf", make_pdf(resume_text), "application/pdf")})
    assert r.status_code == 202, r.text
    return uid, r.json()["resume_id"]


async def onboard_recruiter(client, company="Acme"):
    uid = await new_user("RECRUITER")
    r = await client.post("/api/companies", headers=H(uid),
                          json={"company_name": company, "first_name": "Rita", "last_name": "Rao", "website": "https://acme.test"})
    assert r.status_code == 201, r.text
    return uid


async def create_job(client, uid, status="OPEN"):
    ids = await skill_ids(["Python", "AWS", "Docker", "FastAPI", "Kubernetes"])
    r = await client.post("/api/recruiter/jobs", headers=H(uid), json={
        "title": "Backend Engineer", "description": JOB_DESCRIPTION, "status": status, "min_experience": 3,
        "location": "Bengaluru",
        "skills": [{"skill_id": ids["Python"]}, {"skill_id": ids["AWS"]}, {"skill_id": ids["Docker"]},
                   {"skill_id": ids["FastAPI"], "is_required": False}, {"skill_id": ids["Kubernetes"], "is_required": False}]})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


# =============================================================================================
# The full hiring flow
# =============================================================================================
async def test_full_hiring_flow(client):
    a1, resume1 = await onboard_applicant(client, RESUME_A)
    a2, _ = await onboard_applicant(client, RESUME_B, discoverable=False, first="Rohan")
    r1 = await onboard_recruiter(client)
    r2 = await onboard_recruiter(client, "Globex")

    # Workflow A: the resume was processed once, in the background, right after upload.
    detail = (await client.get(f"/api/resumes/{resume1}", headers=H(a1))).json()
    assert detail["processing_status"] == "COMPLETED", detail
    assert {"Python", "AWS", "Docker", "FastAPI", "PostgreSQL"} <= set(detail["analysis"]["skills"])
    profile = (await client.get("/api/applicant/profile", headers=H(a1))).json()
    assert len(profile["skills"]) >= 5 and profile["completeness"]["percent"] > 0

    job = await create_job(client, r1)
    draft = await create_job(client, r1, status="DRAFT")

    # Discovery
    listing = (await client.get("/api/jobs", params={"q": "backend python"}, headers=H(a1))).json()
    assert job in [j["job_id"] for j in listing["items"]] and draft not in [j["job_id"] for j in listing["items"]]
    rec = (await client.get("/api/jobs/recommended", headers=H(a1))).json()
    assert job in [j["job_id"] for j in rec["items"]] and draft not in [j["job_id"] for j in rec["items"]]
    assert (await client.get(f"/api/jobs/{draft}", headers=H(a1))).status_code == 404

    # Apply (and the unique constraint stops a second application)
    app1 = (await client.post(f"/api/jobs/{job}/apply", headers=H(a1))).json()["application_id"]
    assert (await client.post(f"/api/jobs/{job}/apply", headers=H(a1))).status_code == 409
    app2 = (await client.post(f"/api/jobs/{job}/apply", headers=H(a2))).json()["application_id"]
    assert (await client.post(f"/api/jobs/{draft}/apply", headers=H(a1))).status_code == 409

    # Workflow B: screening ranks the backend engineer above the frontend developer.
    s = await client.post(f"/api/recruiter/jobs/{job}/screen", headers=H(r1), json={"use_ai": True})
    assert s.status_code == 200 and s.json()["screened"] == 2, s.text
    pipeline = (await client.get(f"/api/recruiter/jobs/{job}/applications", headers=H(r1))).json()
    assert [i["application_id"] for i in pipeline["items"]] == [app1, app2]
    assert pipeline["items"][0]["rank_in_job"] == 1 and pipeline["items"][0]["status"] == "SCREENING"

    # Another company can't see any of it.
    assert (await client.get(f"/api/recruiter/jobs/{job}", headers=H(r2))).status_code == 404
    assert (await client.get(f"/api/recruiter/applications/{app1}", headers=H(r2))).status_code == 404
    assert (await client.get(f"/api/recruiter/jobs/{job}/applications", headers=H(r2))).json()["items"] == []
    assert (await client.patch(f"/api/recruiter/applications/{app1}/status", headers=H(r2), json={"status": "REJECTED"})).status_code == 404

    # Shortlist -> interview (with double-booking protection) -> offer -> hire
    assert (await client.patch(f"/api/recruiter/applications/{app1}/status", headers=H(r1), json={"status": "SHORTLISTED"})).status_code == 200
    when = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    iv = await client.post("/api/recruiter/interviews", headers=H(r1), json={
        "application_id": app1, "scheduled_at": when.isoformat(), "duration_minutes": 60, "meeting_link": "https://meet.test/abc"})
    assert iv.status_code == 201, iv.text
    assert (await client.patch(f"/api/recruiter/applications/{app2}/status", headers=H(r1), json={"status": "SHORTLISTED"})).status_code == 200
    clash = await client.post("/api/recruiter/interviews", headers=H(r1), json={
        "application_id": app2, "scheduled_at": (when + timedelta(minutes=15)).isoformat(), "duration_minutes": 30})
    assert clash.status_code == 409 and "already has an interview" in clash.json()["error"]["message"]
    naive = await client.post("/api/recruiter/interviews", headers=H(r1), json={
        "application_id": app2, "scheduled_at": "2030-01-01T10:00:00", "duration_minutes": 30})
    assert naive.status_code == 422

    mine = (await client.get("/api/applicant/interviews", headers=H(a1))).json()["items"]
    assert mine[0]["interviewer_name"] == "Rita Rao"

    offer = await client.post("/api/recruiter/offers", headers=H(r1), json={
        "application_id": app1, "salary": 2400000, "joining_date": (date.today() + timedelta(days=30)).isoformat()})
    assert offer.status_code == 201, offer.text
    offer_id = offer.json()["offer_id"]
    assert (await client.post(f"/api/offers/{offer_id}/respond", headers=H(a2), json={"decision": "ACCEPTED"})).status_code == 404
    assert (await client.post(f"/api/offers/{offer_id}/respond", headers=H(a1), json={"decision": "ACCEPTED"})).status_code == 200
    apps = (await client.get("/api/applications", headers=H(a1))).json()["items"]
    assert apps[0]["status"] == "HIRED"

    # "Why not me": rejecting the second candidate leaves a persisted, explained record.
    assert (await client.patch(f"/api/recruiter/applications/{app2}/status", headers=H(r1), json={"status": "REJECTED"})).status_code == 200
    fb = (await client.get(f"/api/applications/{app2}/feedback", headers=H(a2))).json()
    assert {"Python", "AWS", "Docker"} <= set(fb["missing_skills"])
    assert (await client.get(f"/api/applications/{app2}/feedback", headers=H(a1))).status_code == 404
    await worker.fill_feedback_suggestions()
    fb = (await client.get(f"/api/applications/{app2}/feedback", headers=H(a2))).json()
    assert fb["suggestions_status"] == "READY" and fb["suggested_resources"][0]["search_url"].startswith("https://")

    # Talent search only surfaces candidates who opted in.
    sr = await client.post("/api/recruiter/search", headers=H(r1), json={
        "description": JOB_DESCRIPTION, "filters": {"min_experience": 1}})
    assert sr.status_code == 200, sr.text
    found = {i["applicant_id"] for i in sr.json()["items"]}
    a1_id = profile["profile"]["applicant_id"]
    assert a1_id in found
    hist = (await client.get("/api/recruiter/search/history", headers=H(r1))).json()
    assert hist["total"] >= 1

    # Resume downloads: pre-signed URL for allowed viewers, 404 for everyone else.
    a2_resume = (await client.get("/api/resumes", headers=H(a2))).json()["items"][0]["resume_id"]
    assert (await client.get(f"/api/resumes/{a2_resume}/download", headers=H(r1))).json()["url"].startswith("https://")
    assert (await client.get(f"/api/resumes/{a2_resume}/download", headers=H(r2))).status_code == 404
    assert (await client.get(f"/api/resumes/{a2_resume}/download", headers=H(a1))).status_code == 404

    analytics = (await client.get("/api/recruiter/analytics", headers=H(r1))).json()
    assert analytics["kpis"]["hires"] == 1 and analytics["funnel"]["applied"] == 2 and len(analytics["timeline"]) == 30

    # Every queued email renders (including the calendar invites), and with SES off they're skipped.
    conn = await asyncpg.connect(DSN)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM notifications_outbox WHERE application_id = ANY($1::bigint[]) ORDER BY outbox_id", [app1, app2])]
    await conn.close()
    events = {r["event_type"] for r in rows}
    assert {"APPLICATION_STATUS_CHANGED", "INTERVIEW_SCHEDULED", "OFFER_ISSUED"} <= events
    for r in rows:
        msg = mailer.build_message(r)
        if r["event_type"] == "INTERVIEW_SCHEDULED":
            cal = next(p for p in msg.walk() if p.get_content_type() == "text/calendar")
            ics = cal.get_payload(decode=True).decode()
            assert cal.get_param("method") == "REQUEST" and "METHOD:REQUEST" in ics
            assert f"UID:interview-{r['interview_id']}@shortlist" in ics
    assert await worker.deliver_outbox(batch=200) >= len(rows)


# =============================================================================================
# Authentication and authorization
# =============================================================================================
async def test_token_verification(client):
    uid = await new_user("APPLICANT")
    assert (await client.get("/api/me")).status_code == 401
    assert (await client.get("/api/me", headers=H(uid))).status_code == 200
    assert (await client.get("/api/me", headers=H(uid, exp=int(time.time()) - 60))).json()["error"]["code"] == "token_expired"
    assert (await client.get("/api/me", headers=H(uid, aud="anon"))).status_code == 401
    assert (await client.get("/api/me", headers=H(uid, iss="https://evil.test/auth/v1"))).status_code == 401
    forged = jwt.encode({"sub": str(uid), "aud": "authenticated", "iss": "https://example.supabase.co/auth/v1",
                         "iat": int(time.time()), "exp": int(time.time()) + 60}, "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    assert (await client.get("/api/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401
    unsigned = jwt.encode({"sub": str(uid)}, None, algorithm="none")
    assert (await client.get("/api/me", headers={"Authorization": f"Bearer {unsigned}"})).status_code == 401
    assert (await client.get("/api/me", headers=H(uuid.uuid4()))).status_code == 403   # valid token, unknown user


async def test_role_guards_and_disabled_accounts(client):
    applicant = await new_user("APPLICANT")
    recruiter = await new_user("RECRUITER")
    assert (await client.get("/api/recruiter/jobs", headers=H(applicant))).json()["error"]["code"] == "wrong_role"
    assert (await client.get("/api/applicant/profile", headers=H(recruiter))).json()["error"]["code"] == "wrong_role"
    assert (await client.get("/api/recruiter/jobs", headers=H(recruiter))).json()["error"]["code"] == "onboarding_required"
    conn = await asyncpg.connect(DSN)
    await conn.execute("UPDATE users SET is_active = FALSE WHERE user_id = $1", applicant)
    await conn.close()
    assert (await client.get("/api/me", headers=H(applicant))).json()["error"]["code"] == "account_disabled"


async def test_wrong_portal_login_is_refused_and_session_revoked(client, monkeypatch):
    recruiter = await new_user("RECRUITER")
    calls = []

    async def fake_gotrue(method, path, *, json=None, token=None, params=None):
        calls.append(path)
        if path == "/token":
            return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "user": {"id": str(recruiter)}})
        return httpx.Response(204)

    monkeypatch.setattr(auth_router, "_gotrue", fake_gotrue)
    r = await client.post("/api/auth/login", json={"email": "r@example.com", "password": "Passw0rd!", "portal": "applicant"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "wrong_portal"
    assert "access_token" not in r.text and calls == ["/token", "/logout"]
    r = await client.post("/api/auth/login", json={"email": "r@example.com", "password": "Passw0rd!", "portal": "recruiter"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "RECRUITER"


async def test_login_rate_limit(client, monkeypatch):
    async def fake_gotrue(*a, **k):
        return httpx.Response(400, json={"error_code": "invalid_credentials"})
    monkeypatch.setattr(auth_router, "_gotrue", fake_gotrue)
    codes = [(await client.post("/api/auth/login", json={"email": "x@example.com", "password": "nope", "portal": "applicant"})).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


async def test_signup_does_not_reveal_existing_accounts(client, monkeypatch):
    async def exists(*a, **k):
        return httpx.Response(422, json={"error_code": "user_already_exists"})

    async def fresh(*a, **k):
        return httpx.Response(200, json={"id": "x"})

    monkeypatch.setattr(auth_router, "_gotrue", exists)
    one = await client.post("/api/auth/signup", json={"email": "a@example.com", "password": "Passw0rd1", "portal": "applicant"})
    monkeypatch.setattr(auth_router, "_gotrue", fresh)
    two = await client.post("/api/auth/signup", json={"email": "b@example.com", "password": "Passw0rd1", "portal": "applicant"})
    assert one.status_code == two.status_code == 200 and one.json() == two.json()
    weak = await client.post("/api/auth/signup", json={"email": "c@example.com", "password": "password", "portal": "applicant"})
    assert weak.status_code == 422


# =============================================================================================
# Input hardening
# =============================================================================================
async def test_input_validation_and_upload_hardening(client):
    uid = await new_user("APPLICANT")
    base = {"first_name": "Mallory", "last_name": "X"}
    assert (await client.post("/api/applicant/profile", headers=H(uid), json={**base, "role": "ADMIN"})).status_code == 422
    assert (await client.post("/api/applicant/profile", headers=H(uid), json={**base, "linkedin_url": "javascript:alert(1)"})).status_code == 422
    assert (await client.post("/api/applicant/profile", headers=H(uid), json=base)).status_code == 201
    assert (await client.post("/api/applicant/experience", headers=H(uid), json={
        "company_name": "X", "designation": "Y", "start_date": "2030-01-01"})).status_code == 422

    # Type comes from the bytes: an executable renamed to .pdf is rejected.
    fake = await client.post("/api/resumes", headers=H(uid), files={"file": ("cv.pdf", b"MZ\x90\x00not a pdf" * 50, "application/pdf")})
    assert fake.status_code == 415
    # A text-free PDF (e.g. a scanned image) is accepted, then marked FAILED with a clear reason.
    import pymupdf
    doc = pymupdf.open()
    doc.new_page()
    empty = await client.post("/api/resumes", headers=H(uid), files={"file": ("scan.pdf", doc.tobytes(), "application/pdf")})
    assert empty.status_code == 202
    rows = (await client.get("/api/resumes", headers=H(uid))).json()["items"]
    assert rows[0]["processing_status"] == "FAILED" and "readable text" in rows[0]["processing_error"]
    # Oversized bodies are cut off by the middleware before reaching the handler.
    big = await client.post("/api/resumes", headers=H(uid), files={"file": ("big.pdf", b"%PDF-" + b"0" * (6 * 1024 * 1024), "application/pdf")})
    assert big.status_code == 413
    huge_json = await client.patch("/api/applicant/profile", headers=H(uid), content=b'{"headline":"' + b"a" * 300_000 + b'"}',
                                   )
    assert huge_json.status_code == 413


async def test_security_headers_and_cors(client):
    r = await client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store" and r.headers.get("x-request-id")
    ok = await client.options("/api/me", headers={"Origin": "http://localhost:5500", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5500"
    evil = await client.options("/api/me", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in evil.headers


async def test_pagination_is_capped(client):
    uid = await new_user("APPLICANT")
    assert (await client.get("/api/jobs", params={"limit": 500}, headers=H(uid))).status_code == 422
    r = (await client.get("/api/jobs", params={"limit": 2}, headers=H(uid))).json()
    assert r["limit"] == 2 and len(r["items"]) <= 2


# =============================================================================================
# LLM path (Bedrock mocked): output is validated, clamped and blended, never trusted blindly
# =============================================================================================
async def test_llm_extraction_and_rerank_are_validated(client, monkeypatch):
    from app.config import get_settings
    from app.services import llm

    monkeypatch.setattr(get_settings(), "llm_enabled", True)

    def fake_converse(system, user, max_tokens):
        if "extract structured data" in system:
            return json.dumps({"name": "Asha", "headline": "Backend Engineer", "total_experience_years": 4,
                               "skills": ["python", "k8s", "Postgres", "Underwater Basket Weaving"],
                               "experience": [{"company": "Finlytics", "title": "SSE", "start": "2022-01", "end": None, "is_current": True}],
                               "education": [{"institution": "VIT", "degree": "B.Tech", "start_year": 2016, "end_year": 2020}]})
        if "scoring candidates" in system:
            refs = [c["ref"] for c in json.loads(user.split("<candidates>")[1].split("</candidates>")[0])]
            return "Here you go:\n```json\n" + json.dumps([
                {"ref": r, "overall_score": 1000, "skill_score": -5, "experience_score": 90, "education_score": 80,
                 "strengths": ["Strong Python"] * 9, "recommendation": "hire them!!", "explanation": "Solid backend profile."}
                for r in refs] + [{"ref": "c999", "overall_score": 100}]) + "\n```"
        return "[]"

    monkeypatch.setattr(llm, "_converse_sync", fake_converse)

    a, resume = await onboard_applicant(client, RESUME_A)
    analysis = (await client.get(f"/api/resumes/{resume}", headers=H(a))).json()["analysis"]
    assert analysis["extracted_by"] == "llm"
    assert {"Python", "Kubernetes", "PostgreSQL"} <= set(analysis["skills"])          # aliases normalised
    assert "Underwater Basket Weaving" in analysis["unrecognised_skills"]

    r = await onboard_recruiter(client)
    job = await create_job(client, r)
    app_id = (await client.post(f"/api/jobs/{job}/apply", headers=H(a))).json()["application_id"]
    res = (await client.post(f"/api/recruiter/jobs/{job}/screen", headers=H(r), json={"use_ai": True})).json()
    assert res["llm_used"] is True
    detail = (await client.get(f"/api/recruiter/applications/{app_id}", headers=H(r))).json()["screening"]
    assert 0 <= float(detail["overall_score"]) <= 100                                    # 1000 was clamped
    assert detail["recommendation"] in ("STRONG_MATCH", "MATCH", "WEAK_MATCH", "REJECT")
    assert len(detail["strengths"]) <= 5 and detail["ai_explanation"] == "Solid backend profile."
    assert detail["llm_used"] is True


# =============================================================================================
# Screening review workflow
# =============================================================================================
async def test_review_workflow_end_to_end(client):
    a, _ = await onboard_applicant(client, RESUME_B, first="Rohan")
    r = await onboard_recruiter(client)
    other = await onboard_recruiter(client, "Globex")
    job = await create_job(client, r)
    app_id = (await client.post(f"/api/jobs/{job}/apply", headers=H(a))).json()["application_id"]

    # Too early: not screened or decided yet.
    early = await client.post(f"/api/applications/{app_id}/reviews", headers=H(a),
                              json={"request_type": "HUMAN_REVIEW", "reason": "Please have a person look at this."})
    assert early.status_code == 409
    assert (await client.post(f"/api/applications/{app_id}/reviews", headers=H(a),
                              json={"request_type": "EXTRACTION_ERROR", "reason": "Short"})).status_code == 422

    await client.post(f"/api/recruiter/jobs/{job}/screen", headers=H(r), json={"use_ai": False})
    await client.patch(f"/api/recruiter/applications/{app_id}/status", headers=H(r), json={"status": "REJECTED"})
    before = (await client.get(f"/api/recruiter/jobs/{job}/applications", headers=H(r))).json()["items"][0]["overall_score"]

    # The applicant reports missing skills, then fixes their profile.
    rq = await client.post(f"/api/applications/{app_id}/reviews", headers=H(a), json={
        "request_type": "EXTRACTION_ERROR", "field": "SKILLS", "claimed_value": "Python, AWS, Docker",
        "reason": "I used Python, AWS and Docker at PixelWorks for our build tooling."})
    assert rq.status_code == 201, rq.text
    rq_id = rq.json()["request_id"]
    ids = await skill_ids(["Python", "AWS", "Docker"])
    for sid in ids.values():
        assert (await client.put("/api/applicant/skills", headers=H(a), json={"skill_id": sid})).status_code == 200

    # Only the hiring company sees the request.
    assert (await client.get(f"/api/recruiter/reviews/{rq_id}", headers=H(other))).status_code == 404
    queue = (await client.get("/api/recruiter/reviews", headers=H(r))).json()
    assert queue["open"] == 1 and queue["items"][0]["request_id"] == rq_id
    detail = (await client.get(f"/api/recruiter/reviews/{rq_id}", headers=H(r))).json()
    assert "Python" not in detail["system_value"] and "Python" in detail["current"]["skills"]

    assert (await client.post(f"/api/recruiter/reviews/{rq_id}/start", headers=H(r))).status_code == 200
    res = await client.post(f"/api/recruiter/reviews/{rq_id}/resolve", headers=H(r), json={
        "outcome": "RESCREENED", "note": "Skills confirmed on the portfolio; re-scored with the updated profile."})
    assert res.status_code == 200, res.text
    out = res.json()
    assert float(out["score_before"]) == float(before) and float(out["score_after"]) > float(before)

    # A second request: human review of the rejection, reinstated.
    hr = (await client.post(f"/api/applications/{app_id}/reviews", headers=H(a), json={
        "request_type": "HUMAN_REVIEW", "reason": "With the corrected skills I meet every requirement."})).json()["request_id"]
    bad = await client.post(f"/api/recruiter/reviews/{hr}/resolve", headers=H(r), json={"outcome": "OVERRIDDEN", "note": "Missing the score here."})
    assert bad.status_code == 422
    ok = await client.post(f"/api/recruiter/reviews/{hr}/resolve", headers=H(r), json={
        "outcome": "REINSTATED", "note": "Moving back to screening after the correction."})
    assert ok.status_code == 200, ok.text
    mine = (await client.get("/api/reviews", headers=H(a))).json()["items"]
    assert [m["outcome"] for m in mine] == ["REINSTATED", "RESCREENED"]
    apps = (await client.get("/api/applications", headers=H(a))).json()["items"]
    assert apps[0]["status"] == "SCREENING" and apps[0]["review_status"] == "RESOLVED"
    assert (await client.post(f"/api/recruiter/reviews/{hr}/resolve", headers=H(r), json={
        "outcome": "UPHELD", "note": "Trying to resolve a closed request."})).status_code == 409
