-- 004_triggers.sql
-- Business rules enforced inside the database, so they hold no matter which code path writes.
--
-- Error convention: rule violations raise SQLSTATE 'SL4xx' (custom class). The API maps
-- SL400 -> 400, SL403 -> 403, SL409 -> 409 and passes the message through, so every message here
-- is written to be shown to an end user.

SET search_path = public, extensions;

-- =============================================================================================
-- Shared helpers
-- =============================================================================================
CREATE OR REPLACE FUNCTION app.set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION app.notify(p_user_id uuid, p_type text, p_title text, p_body text, p_link text)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    INSERT INTO notifications (user_id, type, title, body, link)
    SELECT p_user_id, p_type, left(p_title, 200), p_body, p_link
    WHERE p_user_id IS NOT NULL
$$;

CREATE OR REPLACE FUNCTION app.enqueue_email(p_event outbox_event, p_application_id bigint, p_interview_id bigint,
                                             p_email text, p_payload jsonb)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    INSERT INTO notifications_outbox (event_type, application_id, interview_id, recipient_email, payload)
    SELECT p_event, p_application_id, p_interview_id, p_email, p_payload
    WHERE p_email IS NOT NULL
$$;

-- Everything a trigger needs to describe an application to a human, in one lookup.
CREATE OR REPLACE FUNCTION app.application_context(p_application_id bigint,
    OUT applicant_user_id uuid, OUT applicant_email text, OUT applicant_name text,
    OUT job_id bigint, OUT job_title text, OUT company_id bigint, OUT company_name text,
    OUT recruiter_user_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT u.user_id, u.email, ap.first_name || ' ' || ap.last_name,
           j.job_id, j.title, c.company_id, c.company_name, r.user_id
    FROM applications a
    JOIN applicants ap  ON ap.applicant_id = a.applicant_id
    JOIN users u        ON u.user_id = ap.user_id
    JOIN job_postings j ON j.job_id = a.job_id
    JOIN companies c    ON c.company_id = j.company_id
    JOIN recruiters r   ON r.recruiter_id = j.recruiter_id
    WHERE a.application_id = p_application_id
$$;

-- =============================================================================================
-- Supabase Auth -> public.users
-- =============================================================================================
-- Only APPLICANT and RECRUITER can be requested at signup. Anything else (including ADMIN) is
-- coerced to APPLICANT; admins are created manually. The role is copied once at signup, so a user
-- later editing their own auth metadata cannot change it.
CREATE OR REPLACE FUNCTION app.handle_new_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    requested text := upper(coalesce(NEW.raw_user_meta_data ->> 'role', ''));
BEGIN
    INSERT INTO public.users (user_id, email, role)
    VALUES (NEW.id, lower(coalesce(NEW.email, NEW.id::text || '@invalid.local')),
            CASE WHEN requested IN ('APPLICANT', 'RECRUITER') THEN requested ELSE 'APPLICANT' END)
    ON CONFLICT (user_id) DO NOTHING;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION app.handle_user_email_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF NEW.email IS DISTINCT FROM OLD.email AND NEW.email IS NOT NULL THEN
        UPDATE public.users SET email = lower(NEW.email) WHERE user_id = NEW.id;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION app.handle_new_user();

DROP TRIGGER IF EXISTS on_auth_user_email_changed ON auth.users;
CREATE TRIGGER on_auth_user_email_changed
    AFTER UPDATE OF email ON auth.users
    FOR EACH ROW EXECUTE FUNCTION app.handle_user_email_change();

-- Supabase's auth service inserts into auth.users as supabase_auth_admin.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_auth_admin') THEN
        GRANT USAGE ON SCHEMA app TO supabase_auth_admin;
        GRANT EXECUTE ON FUNCTION app.handle_new_user(), app.handle_user_email_change() TO supabase_auth_admin;
    END IF;
END $$;

-- =============================================================================================
-- updated_at maintenance
-- =============================================================================================
CREATE TRIGGER trg_users_updated_at        BEFORE UPDATE ON users        FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER trg_applicants_updated_at   BEFORE UPDATE ON applicants   FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER trg_companies_updated_at    BEFORE UPDATE ON companies    FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER trg_job_postings_updated_at BEFORE UPDATE ON job_postings FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER trg_applications_updated_at BEFORE UPDATE ON applications FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
CREATE TRIGGER trg_interviews_updated_at   BEFORE UPDATE ON interviews   FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();

-- =============================================================================================
-- Job postings: soft delete is one-way, ownership is immutable
-- =============================================================================================
CREATE OR REPLACE FUNCTION app.guard_job_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'This job has been archived and can no longer be edited' USING ERRCODE = 'SL409';
    END IF;
    IF NEW.company_id <> OLD.company_id OR NEW.recruiter_id <> OLD.recruiter_id THEN
        RAISE EXCEPTION 'A job cannot be moved to another company or owner' USING ERRCODE = 'SL400';
    END IF;
    IF NEW.deleted_at IS NOT NULL THEN
        NEW.status := 'CLOSED';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_job_postings_guard BEFORE UPDATE ON job_postings
    FOR EACH ROW EXECUTE FUNCTION app.guard_job_update();

-- =============================================================================================
-- Resumes: version numbering and the single-active-resume rule
-- =============================================================================================
CREATE OR REPLACE FUNCTION app.before_resume_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    -- Serialise concurrent uploads by the same applicant so version numbers never collide.
    PERFORM 1 FROM applicants WHERE applicant_id = NEW.applicant_id FOR UPDATE;

    NEW.version := coalesce((SELECT max(version) FROM resumes WHERE applicant_id = NEW.applicant_id), 0) + 1;
    NEW.processing_status := 'UPLOADED';
    NEW.processing_attempts := 0;
    NEW.parsed_data := NULL;
    NEW.extracted_text := NULL;

    IF NEW.is_active THEN
        UPDATE resumes SET is_active = FALSE WHERE applicant_id = NEW.applicant_id AND is_active;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_resumes_before_insert BEFORE INSERT ON resumes
    FOR EACH ROW EXECUTE FUNCTION app.before_resume_insert();

-- =============================================================================================
-- Applications
-- =============================================================================================
CREATE OR REPLACE FUNCTION app.before_application_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM job_postings
        WHERE job_id = NEW.job_id AND status = 'OPEN' AND deleted_at IS NULL
          AND (closing_date IS NULL OR closing_date >= current_date)
    ) THEN
        RAISE EXCEPTION 'This job is not accepting applications' USING ERRCODE = 'SL409';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM resumes
        WHERE resume_id = NEW.resume_id AND applicant_id = NEW.applicant_id AND processing_status = 'COMPLETED'
    ) THEN
        RAISE EXCEPTION 'Your resume must finish processing before you can apply' USING ERRCODE = 'SL400';
    END IF;

    NEW.status := 'APPLIED';
    NEW.applied_at := now();
    NEW.status_changed_at := now();
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION app.after_application_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE ctx record;
BEGIN
    UPDATE job_postings SET application_count = application_count + 1 WHERE job_id = NEW.job_id;

    SELECT * INTO ctx FROM app.application_context(NEW.application_id);
    PERFORM app.notify(ctx.recruiter_user_id, 'NEW_APPLICATION',
                       'New application for ' || ctx.job_title,
                       ctx.applicant_name || ' applied.', '#/jobs/' || ctx.job_id);
    PERFORM app.write_audit('APPLICATION_SUBMITTED', 'application', NEW.application_id,
                            jsonb_build_object('job_id', NEW.job_id));
    RETURN NEW;
END $$;

-- Validates every status change against application_status_transitions.
--   * pg_trigger_depth() > 1: the change came from another trigger (e.g. an accepted offer marking
--     the application HIRED), which is trusted.
--   * app.user_role() IS NULL: no user claims set, i.e. internal/system work.
CREATE OR REPLACE FUNCTION app.before_application_update() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_actor text := app.user_role();
BEGIN
    IF NEW.job_id <> OLD.job_id OR NEW.applicant_id <> OLD.applicant_id OR NEW.resume_id <> OLD.resume_id
       OR NEW.applied_at <> OLD.applied_at THEN
        RAISE EXCEPTION 'Only the status of an application can change' USING ERRCODE = 'SL400';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF pg_trigger_depth() = 1 AND v_actor IN ('APPLICANT', 'RECRUITER') AND NOT EXISTS (
            SELECT 1 FROM application_status_transitions
            WHERE from_status = OLD.status AND to_status = NEW.status AND application_status_transitions.actor = v_actor
        ) THEN
            RAISE EXCEPTION 'An application cannot move from % to %', initcap(OLD.status), initcap(NEW.status)
                USING ERRCODE = 'SL409';
        END IF;
        NEW.status_changed_at := now();
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION app.after_application_status_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    ctx       record;
    v_missing jsonb;
    v_score   numeric;
    v_summary text;
BEGIN
    SELECT * INTO ctx FROM app.application_context(NEW.application_id);

    PERFORM app.write_audit('APPLICATION_STATUS_CHANGED', 'application', NEW.application_id,
                            jsonb_build_object('from', OLD.status, 'to', NEW.status));

    -- In-app notification to the applicant for every visible change.
    PERFORM app.notify(ctx.applicant_user_id, 'APPLICATION_' || NEW.status,
        CASE NEW.status
            WHEN 'SCREENING'   THEN 'Your application is being reviewed'
            WHEN 'SHORTLISTED' THEN 'You were shortlisted'
            WHEN 'INTERVIEW'   THEN 'Interview stage'
            WHEN 'OFFER'       THEN 'You have an offer'
            WHEN 'HIRED'       THEN 'Welcome aboard'
            WHEN 'REJECTED'    THEN 'Application not progressed'
            WHEN 'WITHDRAWN'   THEN 'Application withdrawn'
            ELSE 'Application updated' END || ' - ' || ctx.job_title,
        ctx.company_name, '#/applications');

    -- Email only for the milestones a candidate would expect one for (interview and offer emails
    -- come from their own triggers, with calendar invites / offer details).
    IF NEW.status IN ('SHORTLISTED', 'HIRED', 'REJECTED') THEN
        PERFORM app.enqueue_email('APPLICATION_STATUS_CHANGED', NEW.application_id, NULL, ctx.applicant_email,
            jsonb_build_object('applicant_name', ctx.applicant_name, 'job_title', ctx.job_title,
                               'company_name', ctx.company_name, 'old_status', OLD.status, 'new_status', NEW.status));
    END IF;

    IF NEW.status = 'WITHDRAWN' THEN
        PERFORM app.notify(ctx.recruiter_user_id, 'APPLICATION_WITHDRAWN',
                           ctx.applicant_name || ' withdrew', ctx.job_title, '#/jobs/' || ctx.job_id);
    END IF;

    -- Closing an application releases everything still attached to it.
    IF NEW.status IN ('REJECTED', 'WITHDRAWN') THEN
        UPDATE interviews SET status = 'CANCELLED'
        WHERE application_id = NEW.application_id AND status = 'SCHEDULED' AND scheduled_at > now();
        UPDATE offers SET offer_status = 'EXPIRED'
        WHERE application_id = NEW.application_id AND offer_status = 'PENDING';
    END IF;

    -- "Why not me": every rejection leaves a persisted explanation. Reuse the AI screening if it
    -- exists; otherwise compute missing skills straight from job_skills vs candidate_skills.
    IF NEW.status = 'REJECTED' THEN
        SELECT CASE WHEN jsonb_array_length(sr.missing_skills) > 0 THEN sr.missing_skills END,
               sr.overall_score, sr.ai_explanation
        INTO v_missing, v_score, v_summary
        FROM screening_results sr WHERE sr.application_id = NEW.application_id;

        IF v_missing IS NULL THEN
            SELECT coalesce(jsonb_agg(s.skill_name ORDER BY js.is_required DESC, s.skill_name), '[]')
            INTO v_missing
            FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id
            WHERE js.job_id = NEW.job_id
              AND NOT EXISTS (SELECT 1 FROM candidate_skills cs
                              WHERE cs.applicant_id = NEW.applicant_id AND cs.skill_id = js.skill_id);
        END IF;

        INSERT INTO candidate_feedback (application_id, missing_skills, match_score, match_summary)
        VALUES (NEW.application_id, coalesce(v_missing, '[]'),
                coalesce(v_score, calculate_skill_match(NEW.applicant_id, NEW.job_id)), v_summary)
        ON CONFLICT (application_id) DO NOTHING;
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER trg_applications_before_insert BEFORE INSERT ON applications
    FOR EACH ROW EXECUTE FUNCTION app.before_application_insert();
CREATE TRIGGER trg_applications_after_insert AFTER INSERT ON applications
    FOR EACH ROW EXECUTE FUNCTION app.after_application_insert();
CREATE TRIGGER trg_applications_before_update BEFORE UPDATE ON applications
    FOR EACH ROW EXECUTE FUNCTION app.before_application_update();
CREATE TRIGGER trg_applications_status_change AFTER UPDATE OF status ON applications
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION app.after_application_status_change();

-- =============================================================================================
-- Interviews
-- =============================================================================================
CREATE OR REPLACE FUNCTION app.before_interview_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_app_status text;
BEGIN
    -- ends_at is derived here rather than as a generated column: timestamptz + interval is only
    -- STABLE (timezone dependent), and generated columns require IMMUTABLE expressions.
    NEW.ends_at := NEW.scheduled_at + make_interval(mins => NEW.duration_minutes);

    IF TG_OP = 'INSERT' THEN
        IF NEW.scheduled_at <= now() THEN
            RAISE EXCEPTION 'Interviews must be scheduled in the future' USING ERRCODE = 'SL400';
        END IF;
        SELECT status INTO v_app_status FROM applications WHERE application_id = NEW.application_id;
        IF v_app_status NOT IN ('SHORTLISTED', 'INTERVIEW') THEN
            RAISE EXCEPTION 'Shortlist the candidate before scheduling an interview' USING ERRCODE = 'SL409';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM recruiters r
                       WHERE r.recruiter_id = NEW.interviewer_id
                         AND r.company_id = app.application_company(NEW.application_id)) THEN
            RAISE EXCEPTION 'The interviewer must belong to the company that owns this job' USING ERRCODE = 'SL400';
        END IF;
        NEW.status := 'SCHEDULED';
        NEW.sequence := 0;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF NEW.application_id <> OLD.application_id THEN
        RAISE EXCEPTION 'An interview cannot be moved to another application' USING ERRCODE = 'SL400';
    END IF;
    IF OLD.status IN ('COMPLETED', 'CANCELLED') AND pg_trigger_depth() = 1 THEN
        RAISE EXCEPTION 'This interview is % and can no longer be changed', lower(OLD.status) USING ERRCODE = 'SL409';
    END IF;
    IF NEW.interviewer_id <> OLD.interviewer_id AND NOT EXISTS (
        SELECT 1 FROM recruiters r WHERE r.recruiter_id = NEW.interviewer_id
          AND r.company_id = app.application_company(NEW.application_id)) THEN
        RAISE EXCEPTION 'The interviewer must belong to the company that owns this job' USING ERRCODE = 'SL400';
    END IF;
    IF (NEW.scheduled_at, NEW.duration_minutes) IS DISTINCT FROM (OLD.scheduled_at, OLD.duration_minutes) THEN
        IF NEW.scheduled_at <= now() THEN
            RAISE EXCEPTION 'Interviews must be scheduled in the future' USING ERRCODE = 'SL400';
        END IF;
        NEW.sequence := OLD.sequence + 1;
        NEW.status := 'SCHEDULED';
    END IF;
    IF NEW.status = 'COMPLETED' AND OLD.status <> 'COMPLETED' AND NEW.scheduled_at > now() THEN
        RAISE EXCEPTION 'An interview cannot be completed before it starts' USING ERRCODE = 'SL409';
    END IF;
    IF NEW.status = 'CANCELLED' AND OLD.status <> 'CANCELLED' THEN
        NEW.sequence := OLD.sequence + 1;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION app.after_interview_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    ctx      record;
    v_event  outbox_event;
    v_iname  text;
    v_payload jsonb;
BEGIN
    SELECT * INTO ctx FROM app.application_context(NEW.application_id);
    SELECT first_name || ' ' || last_name INTO v_iname FROM recruiters WHERE recruiter_id = NEW.interviewer_id;

    IF TG_OP = 'INSERT' THEN
        v_event := 'INTERVIEW_SCHEDULED';
        -- First interview moves a shortlisted application into the INTERVIEW stage.
        UPDATE applications SET status = 'INTERVIEW'
        WHERE application_id = NEW.application_id AND status = 'SHORTLISTED';
        PERFORM app.write_audit('INTERVIEW_SCHEDULED', 'interview', NEW.interview_id,
                                jsonb_build_object('application_id', NEW.application_id));
    ELSIF NEW.status = 'CANCELLED' AND OLD.status <> 'CANCELLED' THEN
        v_event := 'INTERVIEW_CANCELLED';
        PERFORM app.write_audit('INTERVIEW_CANCELLED', 'interview', NEW.interview_id, '{}');
    ELSIF NEW.sequence > OLD.sequence THEN
        v_event := 'INTERVIEW_RESCHEDULED';
        PERFORM app.write_audit('INTERVIEW_RESCHEDULED', 'interview', NEW.interview_id,
                                jsonb_build_object('from', OLD.scheduled_at, 'to', NEW.scheduled_at));
    ELSE
        RETURN NEW;   -- notes/feedback-only edits don't notify the candidate
    END IF;

    v_payload := jsonb_build_object(
        'applicant_name', ctx.applicant_name, 'job_title', ctx.job_title, 'company_name', ctx.company_name,
        'interviewer_name', v_iname, 'scheduled_at', NEW.scheduled_at, 'ends_at', NEW.ends_at,
        'duration_minutes', NEW.duration_minutes, 'interview_type', NEW.interview_type,
        'meeting_link', NEW.meeting_link, 'sequence', NEW.sequence);

    PERFORM app.enqueue_email(v_event, NEW.application_id, NEW.interview_id, ctx.applicant_email, v_payload);
    PERFORM app.notify(ctx.applicant_user_id, v_event::text,
        CASE v_event WHEN 'INTERVIEW_SCHEDULED' THEN 'Interview scheduled'
                     WHEN 'INTERVIEW_RESCHEDULED' THEN 'Interview rescheduled'
                     ELSE 'Interview cancelled' END || ' - ' || ctx.job_title,
        ctx.company_name, '#/interviews');
    RETURN NEW;
END $$;

CREATE TRIGGER trg_interviews_before_write BEFORE INSERT OR UPDATE ON interviews
    FOR EACH ROW EXECUTE FUNCTION app.before_interview_write();
CREATE TRIGGER trg_interviews_after_write AFTER INSERT OR UPDATE ON interviews
    FOR EACH ROW EXECUTE FUNCTION app.after_interview_write();

CREATE OR REPLACE FUNCTION app.before_feedback_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_interview record;
BEGIN
    SELECT * INTO v_interview FROM interviews WHERE interview_id = NEW.interview_id;
    IF v_interview.status = 'CANCELLED' THEN
        RAISE EXCEPTION 'Feedback cannot be added to a cancelled interview' USING ERRCODE = 'SL409';
    END IF;
    IF v_interview.scheduled_at > now() THEN
        RAISE EXCEPTION 'Feedback can only be submitted after the interview starts' USING ERRCODE = 'SL409';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM recruiters WHERE recruiter_id = NEW.interviewer_id
                   AND company_id = app.application_company(v_interview.application_id)) THEN
        RAISE EXCEPTION 'Only recruiters from the hiring company can leave feedback' USING ERRCODE = 'SL403';
    END IF;
    -- Submitting feedback marks the interview completed.
    UPDATE interviews SET status = 'COMPLETED' WHERE interview_id = NEW.interview_id AND status = 'SCHEDULED';
    RETURN NEW;
END $$;

CREATE TRIGGER trg_feedback_before_insert BEFORE INSERT ON interview_feedback
    FOR EACH ROW EXECUTE FUNCTION app.before_feedback_insert();

-- =============================================================================================
-- Offers
-- =============================================================================================
CREATE OR REPLACE FUNCTION app.before_offer_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    v_actor text := app.user_role();
    v_app_status text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO v_app_status FROM applications WHERE application_id = NEW.application_id;
        IF v_app_status NOT IN ('SHORTLISTED', 'INTERVIEW') THEN
            RAISE EXCEPTION 'Offers can only be made to shortlisted or interviewed candidates' USING ERRCODE = 'SL409';
        END IF;
        IF NEW.expires_at <= now() THEN
            RAISE EXCEPTION 'The offer expiry must be in the future' USING ERRCODE = 'SL400';
        END IF;
        IF NEW.joining_date < current_date THEN
            RAISE EXCEPTION 'The joining date cannot be in the past' USING ERRCODE = 'SL400';
        END IF;
        NEW.offer_status := 'PENDING';
        NEW.issued_at := now();
        NEW.responded_at := NULL;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF pg_trigger_depth() > 1 OR v_actor IS NULL THEN
        RETURN NEW;    -- cascades and the expiry sweep
    END IF;

    IF NEW.application_id <> OLD.application_id THEN
        RAISE EXCEPTION 'An offer cannot be moved to another application' USING ERRCODE = 'SL400';
    END IF;

    IF v_actor = 'APPLICANT' THEN
        IF (NEW.salary, NEW.currency, NEW.joining_date, NEW.expires_at, NEW.notes, NEW.issued_at)
           IS DISTINCT FROM (OLD.salary, OLD.currency, OLD.joining_date, OLD.expires_at, OLD.notes, OLD.issued_at) THEN
            RAISE EXCEPTION 'Candidates can only accept or decline an offer' USING ERRCODE = 'SL403';
        END IF;
        IF OLD.offer_status <> 'PENDING' OR NEW.offer_status NOT IN ('ACCEPTED', 'REJECTED') THEN
            RAISE EXCEPTION 'This offer can no longer be answered' USING ERRCODE = 'SL409';
        END IF;
        IF OLD.expires_at < now() THEN
            RAISE EXCEPTION 'This offer has expired' USING ERRCODE = 'SL409';
        END IF;
        NEW.responded_at := now();
    ELSE
        -- Recruiters can edit terms or withdraw (EXPIRED) while pending; only the candidate responds.
        IF OLD.offer_status <> 'PENDING' THEN
            RAISE EXCEPTION 'This offer has already been answered' USING ERRCODE = 'SL409';
        END IF;
        IF NEW.offer_status NOT IN ('PENDING', 'EXPIRED') THEN
            RAISE EXCEPTION 'Only the candidate can accept or decline an offer' USING ERRCODE = 'SL403';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION app.after_offer_write() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE ctx record;
BEGIN
    SELECT * INTO ctx FROM app.application_context(NEW.application_id);

    IF TG_OP = 'INSERT' THEN
        UPDATE applications SET status = 'OFFER'
        WHERE application_id = NEW.application_id AND status IN ('SHORTLISTED', 'INTERVIEW');
        PERFORM app.enqueue_email('OFFER_ISSUED', NEW.application_id, NULL, ctx.applicant_email,
            jsonb_build_object('applicant_name', ctx.applicant_name, 'job_title', ctx.job_title,
                               'company_name', ctx.company_name, 'salary', NEW.salary, 'currency', NEW.currency,
                               'joining_date', NEW.joining_date, 'expires_at', NEW.expires_at));
        PERFORM app.write_audit('OFFER_CREATED', 'offer', NEW.offer_id,
                                jsonb_build_object('application_id', NEW.application_id));
        RETURN NEW;
    END IF;

    IF NEW.offer_status IS NOT DISTINCT FROM OLD.offer_status THEN
        RETURN NEW;
    END IF;

    PERFORM app.write_audit('OFFER_' || NEW.offer_status, 'offer', NEW.offer_id, '{}');

    IF NEW.offer_status = 'ACCEPTED' THEN
        UPDATE applications SET status = 'HIRED' WHERE application_id = NEW.application_id;
        PERFORM app.notify(ctx.recruiter_user_id, 'OFFER_ACCEPTED', ctx.applicant_name || ' accepted the offer',
                           ctx.job_title, '#/jobs/' || ctx.job_id);
    ELSIF NEW.offer_status = 'REJECTED' THEN
        -- A declined offer closes the application from the candidate's side.
        UPDATE applications SET status = 'WITHDRAWN' WHERE application_id = NEW.application_id AND status = 'OFFER';
        PERFORM app.notify(ctx.recruiter_user_id, 'OFFER_DECLINED', ctx.applicant_name || ' declined the offer',
                           ctx.job_title, '#/jobs/' || ctx.job_id);
    ELSIF NEW.offer_status = 'EXPIRED' THEN
        PERFORM app.notify(ctx.applicant_user_id, 'OFFER_EXPIRED', 'Offer expired - ' || ctx.job_title,
                           ctx.company_name, '#/applications');
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_offers_before_write BEFORE INSERT OR UPDATE ON offers
    FOR EACH ROW EXECUTE FUNCTION app.before_offer_write();
CREATE TRIGGER trg_offers_after_write AFTER INSERT OR UPDATE ON offers
    FOR EACH ROW EXECUTE FUNCTION app.after_offer_write();
