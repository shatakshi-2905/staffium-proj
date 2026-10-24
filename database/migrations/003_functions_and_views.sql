-- 003_functions_and_views.sql
-- (a) Security helpers used by RLS policies and triggers (private `app` schema)
-- (b) Business functions from the design document (public schema, SECURITY INVOKER -> RLS applies)
-- (c) Views (security_invoker, so querying a view never bypasses RLS)

SET search_path = public, extensions;

-- =============================================================================================
-- (a) Security helpers
-- =============================================================================================

-- Caller's user id. The API sets `request.jwt.claims` per transaction (same contract Supabase's
-- auth.uid() uses), so this returns NULL for internal/system work and the user's UUID otherwise.
CREATE OR REPLACE FUNCTION app.uid() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub', '')::uuid
$$;

-- The helpers below are SECURITY DEFINER so RLS policies can call them without recursing into
-- the policies of the tables they read. Each pins search_path to avoid search-path hijacking.
CREATE OR REPLACE FUNCTION app.user_role() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT role FROM users WHERE user_id = app.uid() AND is_active
$$;

CREATE OR REPLACE FUNCTION app.applicant_id() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT a.applicant_id FROM applicants a JOIN users u ON u.user_id = a.user_id
    WHERE a.user_id = app.uid() AND u.is_active
$$;

CREATE OR REPLACE FUNCTION app.recruiter_id() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT r.recruiter_id FROM recruiters r JOIN users u ON u.user_id = r.user_id
    WHERE r.user_id = app.uid() AND u.is_active
$$;

CREATE OR REPLACE FUNCTION app.company_id() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT r.company_id FROM recruiters r JOIN users u ON u.user_id = r.user_id
    WHERE r.user_id = app.uid() AND u.is_active
$$;

-- A recruiter may see an applicant who opted into talent search, or who applied to one of the
-- recruiter's company's jobs. Nobody else's data is visible.
CREATE OR REPLACE FUNCTION app.recruiter_can_view_applicant(p_applicant_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT app.company_id() IS NOT NULL AND (
        EXISTS (SELECT 1 FROM applicants a WHERE a.applicant_id = p_applicant_id AND a.is_discoverable)
        OR EXISTS (SELECT 1 FROM applications ap JOIN job_postings j ON j.job_id = ap.job_id
                   WHERE ap.applicant_id = p_applicant_id AND j.company_id = app.company_id())
    )
$$;

CREATE OR REPLACE FUNCTION app.application_company(p_application_id bigint) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT j.company_id FROM applications a JOIN job_postings j ON j.job_id = a.job_id
    WHERE a.application_id = p_application_id
$$;

CREATE OR REPLACE FUNCTION app.application_applicant(p_application_id bigint) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT applicant_id FROM applications WHERE application_id = p_application_id
$$;

CREATE OR REPLACE FUNCTION app.job_company(p_job_id bigint) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT company_id FROM job_postings WHERE job_id = p_job_id
$$;

CREATE OR REPLACE FUNCTION app.job_is_public(p_job_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM job_postings WHERE job_id = p_job_id AND status = 'OPEN' AND deleted_at IS NULL)
$$;

-- Used by triggers to write audit rows regardless of the caller's own table privileges.
CREATE OR REPLACE FUNCTION app.write_audit(p_action text, p_entity_type text, p_entity_id bigint, p_metadata jsonb DEFAULT '{}')
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    INSERT INTO audit_logs (user_id, action, entity_type, entity_id, metadata)
    VALUES (app.uid(), p_action, p_entity_type, p_entity_id, coalesce(p_metadata, '{}'))
$$;

-- =============================================================================================
-- (b) Business functions
-- =============================================================================================

-- Total years of experience. Overlapping roles are merged first (range_agg over dateranges), so
-- two concurrent jobs in the same year count once. Falls back to the figure parsed from the active
-- resume when the applicant has no experience rows.
CREATE OR REPLACE FUNCTION calculate_candidate_experience(p_applicant_id bigint) RETURNS numeric
LANGUAGE sql STABLE AS $$
    WITH merged AS (
        SELECT range_agg(daterange(start_date, LEAST(coalesce(end_date, current_date), current_date), '[]')) AS spans
        FROM experience
        WHERE applicant_id = p_applicant_id AND start_date <= current_date
    ),
    total AS (
        SELECT sum(upper(r) - lower(r)) AS days FROM merged, unnest(merged.spans) AS r
    ),
    parsed AS (
        SELECT (parsed_data ->> 'total_experience_years')::numeric AS years
        FROM resumes
        WHERE applicant_id = p_applicant_id AND is_active
          AND jsonb_typeof(parsed_data -> 'total_experience_years') = 'number'
    )
    SELECT LEAST(GREATEST(coalesce(
        nullif(round((SELECT days FROM total)::numeric / 365.25, 1), 0),
        (SELECT round(years, 1) FROM parsed),
        0), 0), 60)
$$;

-- Weighted share (0-100) of a job's skills the applicant has. Required skills count double.
CREATE OR REPLACE FUNCTION calculate_skill_match(p_applicant_id bigint, p_job_id bigint) RETURNS numeric
LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN coalesce(sum(w), 0) = 0 THEN NULL
                ELSE round(100 * coalesce(sum(w) FILTER (WHERE has_skill), 0) / sum(w), 2) END
    FROM (
        SELECT js.importance_weight * CASE WHEN js.is_required THEN 2 ELSE 1 END AS w,
               EXISTS (SELECT 1 FROM candidate_skills cs
                       WHERE cs.applicant_id = p_applicant_id AND cs.skill_id = js.skill_id) AS has_skill
        FROM job_skills js
        WHERE js.job_id = p_job_id
    ) t
$$;

-- Top-N screened candidates for a job, ranked with a window function.
CREATE OR REPLACE FUNCTION get_top_candidates(p_job_id bigint, p_limit integer DEFAULT 20)
RETURNS TABLE (application_id bigint, applicant_id bigint, full_name text, overall_score numeric,
               recommendation varchar, rank_in_job bigint)
LANGUAGE sql STABLE AS $$
    SELECT a.application_id, a.applicant_id, ap.first_name || ' ' || ap.last_name,
           sr.overall_score, sr.recommendation,
           RANK() OVER (ORDER BY sr.overall_score DESC)
    FROM applications a
    JOIN applicants ap        ON ap.applicant_id = a.applicant_id
    JOIN screening_results sr ON sr.application_id = a.application_id
    WHERE a.job_id = p_job_id AND a.status NOT IN ('WITHDRAWN')
    ORDER BY sr.overall_score DESC
    LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;

-- Count of applications in every pipeline stage for a job (zero rows included).
CREATE OR REPLACE FUNCTION get_application_pipeline(p_job_id bigint)
RETURNS TABLE (status varchar, total bigint)
LANGUAGE sql STABLE AS $$
    SELECT s.status::varchar, count(a.application_id)
    FROM unnest(ARRAY['APPLIED', 'SCREENING', 'SHORTLISTED', 'INTERVIEW', 'OFFER', 'HIRED', 'REJECTED', 'WITHDRAWN'])
         WITH ORDINALITY AS s(status, ord)
    LEFT JOIN applications a ON a.status = s.status AND a.job_id = p_job_id
    GROUP BY s.status, s.ord
    ORDER BY s.ord
$$;

CREATE OR REPLACE FUNCTION get_recruiter_statistics(p_recruiter_id bigint)
RETURNS TABLE (active_jobs bigint, total_applications bigint, shortlisted bigint,
               upcoming_interviews bigint, pending_offers bigint, hires bigint)
LANGUAGE sql STABLE AS $$
    SELECT
        (SELECT count(*) FROM job_postings WHERE recruiter_id = p_recruiter_id AND status = 'OPEN' AND deleted_at IS NULL),
        count(a.application_id),
        count(a.application_id) FILTER (WHERE a.status = 'SHORTLISTED'),
        (SELECT count(*) FROM interviews i WHERE i.interviewer_id = p_recruiter_id
            AND i.status = 'SCHEDULED' AND i.scheduled_at > now()),
        (SELECT count(*) FROM offers o JOIN applications a2 ON a2.application_id = o.application_id
            JOIN job_postings j2 ON j2.job_id = a2.job_id
            WHERE j2.recruiter_id = p_recruiter_id AND o.offer_status = 'PENDING'),
        count(a.application_id) FILTER (WHERE a.status = 'HIRED')
    FROM job_postings j
    LEFT JOIN applications a ON a.job_id = j.job_id
    WHERE j.recruiter_id = p_recruiter_id
$$;

-- =============================================================================================
-- (c) Views. security_invoker = true makes each view run with the caller's privileges, so RLS on
-- the underlying tables still filters rows (by default a view runs as its owner and would not).
-- =============================================================================================
CREATE OR REPLACE VIEW candidate_ranking_view WITH (security_invoker = true) AS
SELECT a.application_id,
       a.job_id,
       j.title                                   AS job_title,
       a.applicant_id,
       ap.first_name || ' ' || ap.last_name      AS candidate_name,
       a.status,
       sr.overall_score,
       sr.skill_score,
       sr.experience_score,
       sr.semantic_score,
       sr.recommendation,
       RANK()         OVER (PARTITION BY a.job_id ORDER BY sr.overall_score DESC) AS rank_in_job,
       round((PERCENT_RANK() OVER (PARTITION BY a.job_id ORDER BY sr.overall_score) * 100)::numeric, 0) AS percentile
FROM applications a
JOIN job_postings j       ON j.job_id = a.job_id
JOIN applicants ap        ON ap.applicant_id = a.applicant_id
JOIN screening_results sr ON sr.application_id = a.application_id;

CREATE OR REPLACE VIEW active_jobs_view WITH (security_invoker = true) AS
SELECT j.job_id, j.title, j.department, j.location, j.employment_type, j.is_remote,
       j.min_experience, j.max_experience, j.min_salary, j.max_salary,
       j.closing_date, j.application_count, j.created_at,
       c.company_id, c.company_name, c.industry,
       (current_date - j.created_at::date) AS days_open
FROM job_postings j
JOIN companies c ON c.company_id = j.company_id
WHERE j.status = 'OPEN' AND j.deleted_at IS NULL
  AND (j.closing_date IS NULL OR j.closing_date >= current_date);

CREATE OR REPLACE VIEW recruitment_pipeline_view WITH (security_invoker = true) AS
SELECT j.job_id, j.company_id, j.title, j.status AS job_status,
       count(a.application_id)                                        AS total,
       count(a.application_id) FILTER (WHERE a.status = 'APPLIED')     AS applied,
       count(a.application_id) FILTER (WHERE a.status = 'SCREENING')   AS screening,
       count(a.application_id) FILTER (WHERE a.status = 'SHORTLISTED') AS shortlisted,
       count(a.application_id) FILTER (WHERE a.status = 'INTERVIEW')   AS interview,
       count(a.application_id) FILTER (WHERE a.status = 'OFFER')       AS offer,
       count(a.application_id) FILTER (WHERE a.status = 'HIRED')       AS hired,
       count(a.application_id) FILTER (WHERE a.status = 'REJECTED')    AS rejected,
       count(a.application_id) FILTER (WHERE a.status = 'WITHDRAWN')   AS withdrawn
FROM job_postings j
LEFT JOIN applications a ON a.job_id = j.job_id
WHERE j.deleted_at IS NULL
GROUP BY j.job_id;

-- =============================================================================================
-- (d) Maintenance functions run by the background worker (system connection only)
-- =============================================================================================

-- Lease a batch of due outbox rows. FOR UPDATE SKIP LOCKED lets several API instances run the
-- worker without delivering the same email twice; the lease (next_attempt_at) doubles as backoff.
CREATE OR REPLACE FUNCTION app.claim_outbox(p_batch integer)
RETURNS SETOF notifications_outbox
LANGUAGE sql AS $$
    UPDATE notifications_outbox o
    SET attempts = o.attempts + 1,
        next_attempt_at = now() + make_interval(mins => LEAST(60, 2 ^ o.attempts)::int)
    WHERE o.outbox_id IN (
        SELECT outbox_id FROM notifications_outbox
        WHERE status = 'PENDING' AND next_attempt_at <= now()
        ORDER BY next_attempt_at
        LIMIT p_batch
        FOR UPDATE SKIP LOCKED
    )
    RETURNING o.*
$$;

-- Offers past their expiry date can't be caught by a trigger (no row changes when time passes),
-- so the worker calls this on a schedule.
CREATE OR REPLACE FUNCTION app.expire_stale_offers() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
    UPDATE offers SET offer_status = 'EXPIRED'
    WHERE offer_status = 'PENDING' AND expires_at < now();
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;
