"""Database-level tests: triggers, constraints, column privileges and Row-Level Security.

Run against a disposable PostgreSQL database that has the migrations applied with --local:

    DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test python database/migrate.py --local
    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost/ats_test pytest backend/tests/test_database.py

Each helper creates fresh, uniquely named data, so tests don't depend on each other.
"""
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
async def pool():
    p = await asyncpg.create_pool(DSN, min_size=1, max_size=4, statement_cache_size=0,
                                  server_settings={"search_path": "public, extensions"})
    yield p
    await p.close()


@asynccontextmanager
async def as_user(pool, user_id):
    """Run as the restricted app_user role with the given caller, exactly like the API does."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true), set_config('role', 'app_user', true)",
                               json.dumps({"sub": str(user_id), "role": "authenticated"}))
            yield conn


async def signup(pool, role):
    uid = uuid.uuid4()
    async with pool.acquire() as c:
        await c.execute("INSERT INTO auth.users (id, email, raw_user_meta_data) VALUES ($1, $2, $3)",
                        uid, f"{role.lower()}-{uid.hex[:8]}@Example.com", json.dumps({"role": role}))
    return uid


async def make_company(pool):
    uid = await signup(pool, "RECRUITER")
    async with pool.acquire() as c:
        cid = await c.fetchval("INSERT INTO companies (company_name, created_by) VALUES ($1, $2) RETURNING company_id",
                               f"Acme {uid.hex[:6]}", uid)
        rid = await c.fetchval("""INSERT INTO recruiters (user_id, company_id, first_name, last_name, is_company_admin)
                                  VALUES ($1, $2, 'Rita', 'Recruiter', TRUE) RETURNING recruiter_id""", uid, cid)
    return uid, cid, rid


async def make_applicant(pool, discoverable=True):
    uid = await signup(pool, "APPLICANT")
    async with as_user(pool, uid) as c:
        aid = await c.fetchval("""INSERT INTO applicants (user_id, first_name, last_name, is_discoverable)
                                  VALUES ($1, 'Asha', 'Applicant', $2) RETURNING applicant_id""", uid, discoverable)
        rid = await c.fetchval("""INSERT INTO resumes (applicant_id, s3_object_key, file_name, file_type, file_size, content_sha256, version)
                                  VALUES ($1, $2, 'cv.pdf', 'pdf', 1000, repeat('a', 64), 1) RETURNING resume_id""",
                               aid, f"resumes/{uuid.uuid4().hex}.pdf")
    async with pool.acquire() as c:  # processing is done by the system connection
        await c.execute("UPDATE resumes SET processing_status = 'COMPLETED' WHERE resume_id = $1", rid)
        await c.execute("INSERT INTO candidate_skills (applicant_id, skill_id, source) SELECT $1, skill_id, 'RESUME' FROM skills WHERE skill_name = 'Python'", aid)
    return uid, aid, rid


async def make_job(pool, recruiter_uid, cid, rid, status="OPEN"):
    async with as_user(pool, recruiter_uid) as c:
        jid = await c.fetchval("""INSERT INTO job_postings (company_id, recruiter_id, title, description, status, min_experience)
                                  VALUES ($1, $2, 'Backend Engineer', $3, $4, 2) RETURNING job_id""",
                               cid, rid, "Build and operate Python services on AWS with Docker. " * 2, status)
        await c.execute("""INSERT INTO job_skills (job_id, skill_id, is_required)
                           SELECT $1, skill_id, skill_name <> 'Docker' FROM skills WHERE skill_name IN ('Python', 'AWS', 'Docker')""", jid)
    return jid


async def apply(pool, applicant_uid, aid, resume_id, jid):
    async with as_user(pool, applicant_uid) as c:
        return await c.fetchval("INSERT INTO applications (job_id, applicant_id, resume_id) VALUES ($1, $2, $3) RETURNING application_id",
                                jid, aid, resume_id)


async def set_status(pool, uid, app_id, status):
    async with as_user(pool, uid) as c:
        return await c.execute("UPDATE applications SET status = $1 WHERE application_id = $2", status, app_id)


# ---------------------------------------------------------------------------------------------

async def test_signup_role_is_mirrored_and_admin_cannot_be_self_assigned(pool):
    rec = await signup(pool, "RECRUITER")
    sneaky = await signup(pool, "ADMIN")
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT role FROM users WHERE user_id = $1", rec) == "RECRUITER"
        assert await c.fetchval("SELECT role FROM users WHERE user_id = $1", sneaky) == "APPLICANT"
        assert (await c.fetchval("SELECT email FROM users WHERE user_id = $1", rec)).islower()


async def test_resume_versions_and_single_active(pool):
    uid, aid, first = await make_applicant(pool)
    async with as_user(pool, uid) as c:
        second = await c.fetchval("""INSERT INTO resumes (applicant_id, s3_object_key, file_name, file_type, file_size, content_sha256, version)
                                     VALUES ($1, $2, 'cv2.pdf', 'pdf', 900, repeat('b', 64), 99) RETURNING resume_id""",
                                  aid, f"resumes/{uuid.uuid4().hex}.pdf")
        rows = await c.fetch("SELECT resume_id, version, is_active FROM resumes WHERE applicant_id = $1 ORDER BY version", aid)
    assert [(r["resume_id"], r["version"], r["is_active"]) for r in rows] == [(first, 1, False), (second, 2, True)]


async def test_apply_rules_counter_and_duplicate(pool):
    r_uid, cid, rid = await make_company(pool)
    jid = await make_job(pool, r_uid, cid, rid)
    a_uid, aid, resume = await make_applicant(pool)

    app_id = await apply(pool, a_uid, aid, resume, jid)
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT application_count FROM job_postings WHERE job_id = $1", jid) == 1
        assert await c.fetchval("SELECT count(*) FROM notifications WHERE user_id = $1 AND type = 'NEW_APPLICATION'", r_uid) == 1

    with pytest.raises(asyncpg.UniqueViolationError):
        await apply(pool, a_uid, aid, resume, jid)

    draft = await make_job(pool, r_uid, cid, rid, status="DRAFT")
    b_uid, bid, b_resume = await make_applicant(pool)
    with pytest.raises(asyncpg.PostgresError) as e:
        await apply(pool, b_uid, bid, b_resume, draft)
    assert e.value.sqlstate == "SL409"
    assert app_id


async def test_unprocessed_resume_cannot_apply(pool):
    r_uid, cid, rid = await make_company(pool)
    jid = await make_job(pool, r_uid, cid, rid)
    a_uid, aid, _ = await make_applicant(pool)
    async with as_user(pool, a_uid) as c:
        fresh = await c.fetchval("""INSERT INTO resumes (applicant_id, s3_object_key, file_name, file_type, file_size, content_sha256, version)
                                    VALUES ($1, $2, 'new.pdf', 'pdf', 10, repeat('c', 64), 1) RETURNING resume_id""",
                                 aid, f"resumes/{uuid.uuid4().hex}.pdf")
    with pytest.raises(asyncpg.PostgresError) as e:
        await apply(pool, a_uid, aid, fresh, jid)
    assert e.value.sqlstate == "SL400"


async def test_status_state_machine_and_column_privileges(pool):
    r_uid, cid, rid = await make_company(pool)
    jid = await make_job(pool, r_uid, cid, rid)
    a_uid, aid, resume = await make_applicant(pool)
    app_id = await apply(pool, a_uid, aid, resume, jid)

    # Applicants can't promote themselves.
    with pytest.raises(asyncpg.PostgresError) as e:
        await set_status(pool, a_uid, app_id, "SHORTLISTED")
    assert e.value.sqlstate == "SL409"

    # Nobody can jump to HIRED by hand.
    with pytest.raises(asyncpg.PostgresError):
        await set_status(pool, r_uid, app_id, "HIRED")

    # Column-level grant: only `status` is updatable.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('request.jwt.claims', $1, true), set_config('role', 'app_user', true)",
                            json.dumps({"sub": str(a_uid)}))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute("UPDATE applications SET job_id = job_id WHERE application_id = $1", app_id)

    await set_status(pool, r_uid, app_id, "SHORTLISTED")
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM notifications_outbox WHERE application_id = $1 AND event_type = 'APPLICATION_STATUS_CHANGED'", app_id) == 1
        assert await c.fetchval("SELECT count(*) FROM audit_logs WHERE entity_id = $1 AND action = 'APPLICATION_STATUS_CHANGED'", app_id) == 1


async def test_rls_isolates_companies_and_applicants(pool):
    r1, c1, rid1 = await make_company(pool)
    r2, _, _ = await make_company(pool)
    jid = await make_job(pool, r1, c1, rid1)
    hidden_draft = await make_job(pool, r1, c1, rid1, status="DRAFT")
    a_uid, aid, resume = await make_applicant(pool, discoverable=False)
    app_id = await apply(pool, a_uid, aid, resume, jid)
    other_uid, other_aid, _ = await make_applicant(pool, discoverable=False)

    async with as_user(pool, r2) as c:  # other company
        assert await c.fetchval("SELECT count(*) FROM applications WHERE application_id = $1", app_id) == 0
        assert await c.fetchval("SELECT count(*) FROM job_postings WHERE job_id = $1", hidden_draft) == 0
        assert await c.fetchval("SELECT count(*) FROM applicants WHERE applicant_id = $1", aid) == 0
        assert await c.execute("UPDATE applications SET status = 'REJECTED' WHERE application_id = $1", app_id) == "UPDATE 0"
        assert await c.fetchval("SELECT count(*) FROM candidate_ranking_view WHERE application_id = $1", app_id) == 0

    async with as_user(pool, r1) as c:  # hiring company sees the (non-discoverable) applicant who applied...
        assert await c.fetchval("SELECT count(*) FROM applicants WHERE applicant_id = $1", aid) == 1
        assert await c.fetchval("SELECT count(*) FROM resumes WHERE applicant_id = $1", aid) == 1
        # ...but not a non-discoverable applicant who never applied.
        assert await c.fetchval("SELECT count(*) FROM applicants WHERE applicant_id = $1", other_aid) == 0

    async with as_user(pool, other_uid) as c:  # applicants can't see each other
        assert await c.fetchval("SELECT count(*) FROM applications WHERE application_id = $1", app_id) == 0
        assert await c.fetchval("SELECT count(*) FROM resumes WHERE applicant_id = $1", aid) == 0
    async with as_user(pool, other_uid) as c:  # internal tables have no grant at all
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.fetchval("SELECT count(*) FROM notifications_outbox")


async def test_interviews_double_booking_and_cascade(pool):
    r_uid, cid, rid = await make_company(pool)
    jid = await make_job(pool, r_uid, cid, rid)
    apps = []
    for _ in range(2):
        a_uid, aid, resume = await make_applicant(pool)
        app_id = await apply(pool, a_uid, aid, resume, jid)
        await set_status(pool, r_uid, app_id, "SHORTLISTED")
        apps.append(app_id)

    when = datetime.now(timezone.utc) + timedelta(days=2)
    async with as_user(pool, r_uid) as c:
        with pytest.raises(asyncpg.PostgresError) as e:
            await c.execute("INSERT INTO interviews (application_id, interviewer_id, scheduled_at, duration_minutes) VALUES ($1,$2,$3,60)",
                            apps[0], rid, datetime.now(timezone.utc) - timedelta(hours=1))
        assert e.value.sqlstate == "SL400"

    async with as_user(pool, r_uid) as c:
        iid = await c.fetchval("INSERT INTO interviews (application_id, interviewer_id, scheduled_at, duration_minutes) VALUES ($1,$2,$3,60) RETURNING interview_id",
                               apps[0], rid, when)
    async with pool.acquire() as c:
        assert await c.fetchval("SELECT status FROM applications WHERE application_id = $1", apps[0]) == "INTERVIEW"
        assert await c.fetchval("SELECT ends_at - scheduled_at FROM interviews WHERE interview_id = $1", iid) == timedelta(hours=1)

    # Same interviewer, overlapping slot, different candidate -> exclusion constraint.
    async with as_user(pool, r_uid) as c:
        with pytest.raises(asyncpg.ExclusionViolationError):
            await c.execute("INSERT INTO interviews (application_id, interviewer_id, scheduled_at, duration_minutes) VALUES ($1,$2,$3,30)",
                            apps[1], rid, when + timedelta(minutes=30))
    # Back-to-back is fine ('[)' range bounds).
    async with as_user(pool, r_uid) as c:
        await c.execute("INSERT INTO interviews (application_id, interviewer_id, scheduled_at, duration_minutes) VALUES ($1,$2,$3,30)",
                        apps[1], rid, when + timedelta(hours=1))

    # Rescheduling bumps the iCalendar sequence and queues an email.
    async with as_user(pool, r_uid) as c:
        await c.execute("UPDATE interviews SET scheduled_at = $1 WHERE interview_id = $2", when + timedelta(days=1), iid)
    # Rejecting the candidate cancels their upcoming interview.
    await set_status(pool, r_uid, apps[0], "REJECTED")
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT status, sequence FROM interviews WHERE interview_id = $1", iid)
        assert row["status"] == "CANCELLED" and row["sequence"] == 2
        events = [r[0] for r in await c.fetch("SELECT event_type::text FROM notifications_outbox WHERE interview_id = $1 ORDER BY outbox_id", iid)]
        assert events == ["INTERVIEW_SCHEDULED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED"]
        fb = await c.fetchrow("SELECT missing_skills FROM candidate_feedback WHERE application_id = $1", apps[0])
        assert sorted(json.loads(fb["missing_skills"])) == ["AWS", "Docker"]


async def test_offer_lifecycle(pool):
    r_uid, cid, rid = await make_company(pool)
    jid = await make_job(pool, r_uid, cid, rid)
    a_uid, aid, resume = await make_applicant(pool)
    app_id = await apply(pool, a_uid, aid, resume, jid)

    async with as_user(pool, r_uid) as c:
        with pytest.raises(asyncpg.PostgresError) as e:  # not shortlisted yet
            await c.execute("INSERT INTO offers (application_id, salary, joining_date, expires_at) VALUES ($1, 1200000, $2, now() + interval '7 days')",
                            app_id, date.today() + timedelta(days=30))
        assert e.value.sqlstate == "SL409"
    await set_status(pool, r_uid, app_id, "SHORTLISTED")
    async with as_user(pool, r_uid) as c:
        oid = await c.fetchval("INSERT INTO offers (application_id, salary, joining_date, expires_at) VALUES ($1, 1200000, $2, now() + interval '7 days') RETURNING offer_id",
                               app_id, date.today() + timedelta(days=30))

    async with as_user(pool, a_uid) as c:
        with pytest.raises(asyncpg.PostgresError) as e:  # candidate edits salary
            await c.execute("UPDATE offers SET salary = 9999999 WHERE offer_id = $1", oid)
        assert e.value.sqlstate == "SL403"
    async with as_user(pool, a_uid) as c:
        await c.execute("UPDATE offers SET offer_status = 'ACCEPTED' WHERE offer_id = $1", oid)

    async with pool.acquire() as c:
        assert await c.fetchval("SELECT status FROM applications WHERE application_id = $1", app_id) == "HIRED"
        assert await c.fetchval("SELECT responded_at IS NOT NULL FROM offers WHERE offer_id = $1", oid)


async def test_experience_merges_overlaps_and_soft_delete_is_final(pool):
    a_uid, aid, _ = await make_applicant(pool)
    async with as_user(pool, a_uid) as c:
        await c.execute("""INSERT INTO experience (applicant_id, company_name, designation, start_date, end_date) VALUES
                           ($1, 'A', 'Dev', '2020-01-01', '2021-12-31'),
                           ($1, 'B', 'Dev', '2021-01-01', '2021-12-31')""", aid)   # second fully overlaps
        years = await c.fetchval("SELECT calculate_candidate_experience($1)", aid)
    assert 1.9 <= float(years) <= 2.1

    r_uid, cid, rid = await make_company(pool)
    jid = await make_job(pool, r_uid, cid, rid)
    async with as_user(pool, r_uid) as c:
        await c.execute("UPDATE job_postings SET deleted_at = now() WHERE job_id = $1", jid)
        assert await c.fetchval("SELECT status FROM job_postings WHERE job_id = $1", jid) == "CLOSED"
    async with as_user(pool, r_uid) as c:
        with pytest.raises(asyncpg.PostgresError) as e:
            await c.execute("UPDATE job_postings SET title = 'x' WHERE job_id = $1", jid)
        assert e.value.sqlstate == "SL409"
