-- 007_screening_reviews.sql
-- Human review of AI screening decisions.
--
-- An applicant can, for one of their applications:
--   * EXTRACTION_ERROR - report that the system read their resume wrong (e.g. "says 2 years, I have 4")
--   * HUMAN_REVIEW     - ask a person at the hiring company to look at the decision
-- A recruiter at that company picks the request up and resolves it with one of four outcomes:
--   UPHELD      - decision stands (a written note is still required)
--   RESCREENED  - the application is scored again on the applicant's current profile
--   OVERRIDDEN  - the recruiter sets a human score; the AI score is kept beside it for the record
--   REINSTATED  - a rejected application goes back into the pipeline
-- Every step is written to audit_logs, and the AI score before/after is kept on the request so
-- disagreement between the model and people can be measured later.

SET search_path = public, extensions;

ALTER TYPE outbox_event ADD VALUE IF NOT EXISTS 'REVIEW_RESOLVED';

-- ---------------------------------------------------------------------------------------------
-- Human override columns on screening_results
-- ---------------------------------------------------------------------------------------------
ALTER TABLE screening_results
    ADD COLUMN human_score        NUMERIC(5,2) CHECK (human_score BETWEEN 0 AND 100),
    ADD COLUMN human_note         TEXT CHECK (human_note IS NULL OR length(human_note) <= 2000),
    ADD COLUMN human_reviewed_by  BIGINT REFERENCES recruiters(recruiter_id),
    ADD COLUMN human_reviewed_at  TIMESTAMPTZ,
    -- A human score never exists without the reason for it, and vice versa.
    ADD CONSTRAINT human_override_has_reason CHECK ((human_score IS NULL) = (human_note IS NULL)),
    -- The score the pipeline sorts by: a person's judgement wins over the model's.
    ADD COLUMN effective_score    NUMERIC(5,2) GENERATED ALWAYS AS (coalesce(human_score, overall_score)) STORED;

CREATE INDEX idx_screening_effective_score ON screening_results (effective_score DESC);

-- ---------------------------------------------------------------------------------------------
-- Requests
-- ---------------------------------------------------------------------------------------------
CREATE TABLE screening_review_requests (
    request_id       BIGSERIAL PRIMARY KEY,
    application_id   BIGINT NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    applicant_id     BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    request_type     VARCHAR(16) NOT NULL CHECK (request_type IN ('EXTRACTION_ERROR', 'HUMAN_REVIEW')),
    field            VARCHAR(16) CHECK (field IN ('SKILLS', 'EXPERIENCE_YEARS', 'EDUCATION', 'WORK_HISTORY', 'OTHER')),
    system_value     JSONB,                       -- what the system had, snapshotted at submission
    claimed_value    TEXT CHECK (claimed_value IS NULL OR length(claimed_value) <= 1000),
    reason           TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 10 AND 2000),
    status           VARCHAR(10) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'WITHDRAWN')),
    outcome          VARCHAR(10) CHECK (outcome IN ('UPHELD', 'RESCREENED', 'OVERRIDDEN', 'REINSTATED')),
    override_score   NUMERIC(5,2) CHECK (override_score BETWEEN 0 AND 100),
    resolution_note  TEXT CHECK (resolution_note IS NULL OR length(resolution_note) <= 2000),
    reviewer_id      BIGINT REFERENCES recruiters(recruiter_id),
    score_before     NUMERIC(5,2),
    score_after      NUMERIC(5,2),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    review_started_at TIMESTAMPTZ,
    resolved_at      TIMESTAMPTZ,

    -- An extraction report must say which field is wrong; a review request doesn't need one.
    CONSTRAINT extraction_error_names_field CHECK (request_type <> 'EXTRACTION_ERROR' OR field IS NOT NULL),
    -- Resolved <=> has an outcome, a written note and a resolution time.
    CONSTRAINT resolved_has_outcome CHECK (
        (status = 'RESOLVED') = (outcome IS NOT NULL)
        AND (status <> 'RESOLVED' OR (resolution_note IS NOT NULL AND length(trim(resolution_note)) >= 10 AND resolved_at IS NOT NULL))),
    -- An override always carries the human score; nothing else does.
    CONSTRAINT override_has_score CHECK ((outcome = 'OVERRIDDEN') = (override_score IS NOT NULL))
);

-- One open request of each type per application: repeated requests can't flood a recruiter's queue.
CREATE UNIQUE INDEX uq_review_one_open_per_type ON screening_review_requests (application_id, request_type)
    WHERE status IN ('OPEN', 'IN_REVIEW');
CREATE INDEX idx_reviews_applicant ON screening_review_requests (applicant_id, created_at DESC);
CREATE INDEX idx_reviews_open      ON screening_review_requests (application_id) WHERE status IN ('OPEN', 'IN_REVIEW');

CREATE TRIGGER trg_reviews_updated_at BEFORE UPDATE ON screening_review_requests
    FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

-- The request lifecycle, as data (same pattern as application_status_transitions).
CREATE TABLE review_status_transitions (
    from_status  VARCHAR(10) NOT NULL,
    to_status    VARCHAR(10) NOT NULL,
    actor        VARCHAR(10) NOT NULL CHECK (actor IN ('RECRUITER', 'APPLICANT')),
    PRIMARY KEY (from_status, to_status, actor)
);
INSERT INTO review_status_transitions VALUES
    ('OPEN',      'IN_REVIEW', 'RECRUITER'),
    ('OPEN',      'RESOLVED',  'RECRUITER'),
    ('IN_REVIEW', 'RESOLVED',  'RECRUITER'),
    ('OPEN',      'WITHDRAWN', 'APPLICANT'),
    ('IN_REVIEW', 'WITHDRAWN', 'APPLICANT');

-- ---------------------------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.before_review_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_app      record;
    v_screened numeric;
BEGIN
    SELECT a.applicant_id, a.status INTO v_app FROM applications a WHERE a.application_id = NEW.application_id;
    IF v_app IS NULL OR v_app.applicant_id <> NEW.applicant_id THEN
        RAISE EXCEPTION 'Application not found' USING ERRCODE = 'SL404';
    END IF;
    IF v_app.status = 'WITHDRAWN' THEN
        RAISE EXCEPTION 'You withdrew this application, so it can''t be reviewed' USING ERRCODE = 'SL409';
    END IF;
    SELECT effective_score INTO v_screened FROM screening_results WHERE application_id = NEW.application_id;
    IF NEW.request_type = 'HUMAN_REVIEW' AND v_screened IS NULL AND v_app.status <> 'REJECTED' THEN
        RAISE EXCEPTION 'A human review can be requested once your application has been screened or decided'
            USING ERRCODE = 'SL409';
    END IF;
    -- At most 3 requests per application over its lifetime.
    IF (SELECT count(*) FROM screening_review_requests WHERE application_id = NEW.application_id) >= 3 THEN
        RAISE EXCEPTION 'You''ve reached the limit of 3 review requests for this application' USING ERRCODE = 'SL409';
    END IF;

    -- Snapshot what the system knew at the time, so the reviewer sees the disputed value even if
    -- the profile changes later.
    NEW.system_value := CASE NEW.field
        WHEN 'SKILLS' THEN (SELECT coalesce(jsonb_agg(s.skill_name ORDER BY s.skill_name), '[]')
                            FROM candidate_skills cs JOIN skills s ON s.skill_id = cs.skill_id
                            WHERE cs.applicant_id = NEW.applicant_id)
        WHEN 'EXPERIENCE_YEARS' THEN to_jsonb(calculate_candidate_experience(NEW.applicant_id))
        WHEN 'EDUCATION' THEN (SELECT coalesce(jsonb_agg(jsonb_build_object('degree', degree, 'institution', institution,
                                                    'end_year', end_year) ORDER BY end_year DESC NULLS FIRST), '[]')
                               FROM education WHERE applicant_id = NEW.applicant_id)
        WHEN 'WORK_HISTORY' THEN (SELECT coalesce(jsonb_agg(jsonb_build_object('title', designation, 'company', company_name,
                                                    'start', start_date, 'end', end_date) ORDER BY start_date DESC), '[]')
                                  FROM experience WHERE applicant_id = NEW.applicant_id)
        ELSE NULL END;
    NEW.score_before := v_screened;
    NEW.status := 'OPEN';
    NEW.outcome := NULL; NEW.override_score := NULL; NEW.resolution_note := NULL; NEW.reviewer_id := NULL;
    NEW.score_after := NULL; NEW.review_started_at := NULL; NEW.resolved_at := NULL;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION app.before_review_update() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_actor text := app.user_role();
    v_app_status text;
BEGIN
    -- Who asked, about what, and why never changes after submission.
    IF (NEW.application_id, NEW.applicant_id, NEW.request_type, NEW.field, NEW.claimed_value, NEW.reason,
        NEW.system_value, NEW.score_before, NEW.created_at)
       IS DISTINCT FROM (OLD.application_id, OLD.applicant_id, OLD.request_type, OLD.field, OLD.claimed_value,
                         OLD.reason, OLD.system_value, OLD.score_before, OLD.created_at) THEN
        RAISE EXCEPTION 'A submitted request can''t be edited' USING ERRCODE = 'SL400';
    END IF;

    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        IF OLD.status IN ('RESOLVED', 'WITHDRAWN') THEN
            RAISE EXCEPTION 'This request is closed' USING ERRCODE = 'SL409';
        END IF;
        IF v_actor = 'APPLICANT' AND (NEW.outcome, NEW.override_score, NEW.resolution_note)
              IS DISTINCT FROM (OLD.outcome, OLD.override_score, OLD.resolution_note) THEN
            RAISE EXCEPTION 'Only the reviewer can record an outcome' USING ERRCODE = 'SL403';
        END IF;
        RETURN NEW;
    END IF;

    IF v_actor IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM review_status_transitions t
        WHERE t.from_status = OLD.status AND t.to_status = NEW.status AND t.actor = v_actor) THEN
        RAISE EXCEPTION 'A review request can''t move from % to %', lower(OLD.status), lower(NEW.status)
            USING ERRCODE = 'SL409';
    END IF;

    IF NEW.status = 'IN_REVIEW' THEN
        NEW.reviewer_id := app.recruiter_id();
        NEW.review_started_at := now();
    ELSIF NEW.status = 'WITHDRAWN' THEN
        IF (NEW.outcome, NEW.override_score, NEW.resolution_note) IS DISTINCT FROM (OLD.outcome, OLD.override_score, OLD.resolution_note) THEN
            RAISE EXCEPTION 'Only the reviewer can record an outcome' USING ERRCODE = 'SL403';
        END IF;
        NEW.resolved_at := now();
    ELSIF NEW.status = 'RESOLVED' THEN
        NEW.reviewer_id := coalesce(app.recruiter_id(), OLD.reviewer_id);
        NEW.review_started_at := coalesce(OLD.review_started_at, now());
        NEW.resolved_at := now();
        IF NEW.outcome = 'REINSTATED' THEN
            SELECT status INTO v_app_status FROM applications WHERE application_id = NEW.application_id;
            IF v_app_status <> 'REJECTED' THEN
                RAISE EXCEPTION 'Only a rejected application can be reinstated' USING ERRCODE = 'SL409';
            END IF;
        END IF;
        -- Score after the review. Set here (not in the AFTER trigger) so RETURNING sees it.
        NEW.score_after := CASE WHEN NEW.outcome = 'OVERRIDDEN' THEN NEW.override_score
                                ELSE (SELECT effective_score FROM screening_results WHERE application_id = NEW.application_id) END;
        IF NEW.outcome = 'RESCREENED' AND NOT EXISTS (
            SELECT 1 FROM screening_results WHERE application_id = NEW.application_id
              AND screened_at >= coalesce(OLD.review_started_at, OLD.created_at)) THEN
            RAISE EXCEPTION 'Run the new screening before resolving as re-screened' USING ERRCODE = 'SL409';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- Applies the outcome, then audits and notifies. Runs in the same transaction as the resolution,
-- so an outcome is never recorded without its effect (or the other way round).
CREATE OR REPLACE FUNCTION app.after_review_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    ctx record;
BEGIN
    SELECT * INTO ctx FROM app.application_context(NEW.application_id);

    IF TG_OP = 'INSERT' THEN
        PERFORM app.write_audit('REVIEW_REQUESTED', 'review_request', NEW.request_id,
            jsonb_build_object('application_id', NEW.application_id, 'type', NEW.request_type, 'field', NEW.field));
        PERFORM app.notify(ctx.recruiter_user_id, 'REVIEW_REQUESTED',
            ctx.applicant_name || ' asked for a review - ' || ctx.job_title,
            CASE NEW.request_type WHEN 'EXTRACTION_ERROR' THEN 'Reported incorrect ' || lower(replace(NEW.field, '_', ' '))
                                  ELSE 'Requested a human review of the screening decision' END,
            '#/reviews/' || NEW.request_id);
        RETURN NEW;
    END IF;

    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;

    IF NEW.status = 'IN_REVIEW' THEN
        PERFORM app.write_audit('REVIEW_STARTED', 'review_request', NEW.request_id, '{}');
        PERFORM app.notify(ctx.applicant_user_id, 'REVIEW_STARTED', 'Your review request is being looked at - ' || ctx.job_title,
                           ctx.company_name, '#/reviews');
        RETURN NEW;
    END IF;

    IF NEW.status = 'WITHDRAWN' THEN
        PERFORM app.write_audit('REVIEW_WITHDRAWN', 'review_request', NEW.request_id, '{}');
        RETURN NEW;
    END IF;

    -- RESOLVED: apply the outcome.
    IF NEW.outcome = 'OVERRIDDEN' THEN
        UPDATE screening_results
        SET human_score = NEW.override_score, human_note = NEW.resolution_note,
            human_reviewed_by = NEW.reviewer_id, human_reviewed_at = now()
        WHERE application_id = NEW.application_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'This application has no screening to override yet' USING ERRCODE = 'SL409';
        END IF;
    ELSIF NEW.outcome = 'REINSTATED' THEN
        UPDATE applications SET status = 'SCREENING' WHERE application_id = NEW.application_id;
        DELETE FROM candidate_feedback WHERE application_id = NEW.application_id;   -- no longer a rejection
    END IF;

    PERFORM app.write_audit('REVIEW_RESOLVED', 'review_request', NEW.request_id,
        jsonb_build_object('outcome', NEW.outcome, 'application_id', NEW.application_id,
                           'score_before', NEW.score_before, 'score_after', NEW.score_after));
    PERFORM app.notify(ctx.applicant_user_id, 'REVIEW_RESOLVED',
        'Review complete - ' || ctx.job_title,
        CASE NEW.outcome WHEN 'UPHELD' THEN 'The decision stands. The reviewer left a note.'
                         WHEN 'RESCREENED' THEN 'Your application was screened again with your updated profile.'
                         WHEN 'OVERRIDDEN' THEN 'A reviewer adjusted your assessment.'
                         ELSE 'Your application is back in the running.' END,
        '#/reviews');
    PERFORM app.enqueue_email('REVIEW_RESOLVED', NEW.application_id, NULL, ctx.applicant_email,
        jsonb_build_object('applicant_name', ctx.applicant_name, 'job_title', ctx.job_title,
                           'company_name', ctx.company_name, 'outcome', NEW.outcome, 'note', NEW.resolution_note));
    RETURN NEW;
END $$;

CREATE TRIGGER trg_reviews_before_insert BEFORE INSERT ON screening_review_requests
    FOR EACH ROW EXECUTE FUNCTION app.before_review_insert();
CREATE TRIGGER trg_reviews_before_update BEFORE UPDATE ON screening_review_requests
    FOR EACH ROW EXECUTE FUNCTION app.before_review_update();
CREATE TRIGGER trg_reviews_after_write AFTER INSERT OR UPDATE OF status ON screening_review_requests
    FOR EACH ROW EXECUTE FUNCTION app.after_review_write();

-- ---------------------------------------------------------------------------------------------
-- Privileges and RLS
-- ---------------------------------------------------------------------------------------------
ALTER TABLE screening_review_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE review_status_transitions ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON screening_review_requests, review_status_transitions TO app_user;
-- Column-level INSERT: an applicant can only supply these fields; everything else is set by triggers.
GRANT INSERT (application_id, applicant_id, request_type, field, claimed_value, reason) ON screening_review_requests TO app_user;
GRANT UPDATE (status, outcome, override_score, resolution_note) ON screening_review_requests TO app_user;
GRANT USAGE ON SEQUENCE screening_review_requests_request_id_seq TO app_user;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO app_user;
REVOKE EXECUTE ON FUNCTION app.claim_outbox(integer), app.expire_stale_offers() FROM app_user;

CREATE POLICY review_transitions_read ON review_status_transitions FOR SELECT TO app_user USING (TRUE);

CREATE POLICY reviews_select ON screening_review_requests FOR SELECT TO app_user USING (
    applicant_id = app.applicant_id() OR app.application_company(application_id) = app.company_id());
CREATE POLICY reviews_insert ON screening_review_requests FOR INSERT TO app_user
    WITH CHECK (applicant_id = app.applicant_id() AND app.user_role() = 'APPLICANT');
CREATE POLICY reviews_update ON screening_review_requests FOR UPDATE TO app_user
    USING (applicant_id = app.applicant_id() OR app.application_company(application_id) = app.company_id())
    WITH CHECK (applicant_id = app.applicant_id() OR app.application_company(application_id) = app.company_id());

-- Supabase's REST roles never get access to these tables (belt and braces on top of 005).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated';
    END IF;
END $$;
