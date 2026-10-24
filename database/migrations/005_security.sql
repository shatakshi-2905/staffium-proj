-- 005_security.sql
-- Privileges and Row-Level Security.
--
-- Three layers, from coarse to fine:
--   1. Supabase's public REST roles (anon, authenticated) get nothing. The project never uses the
--      auto-generated REST API, so leaving it open would only be an attack surface.
--   2. app_user gets table privileges, column-restricted where a role should only change one
--      thing (e.g. it can UPDATE applications.status and nothing else on that table).
--   3. RLS policies decide which *rows* app_user can see or change, based on the caller's claims.

SET search_path = public, extensions;

-- ---------------------------------------------------------------------------------------------
-- 1. Lock down Supabase's REST roles
-- ---------------------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated';
        EXECUTE 'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated';
    END IF;
END $$;

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA app FROM PUBLIC;
GRANT  EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO app_user;
-- The maintenance functions are for the system connection only.
REVOKE EXECUTE ON FUNCTION app.claim_outbox(integer), app.expire_stale_offers() FROM app_user;

-- ---------------------------------------------------------------------------------------------
-- 2. Table and column privileges for app_user
-- ---------------------------------------------------------------------------------------------
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO app_user;
GRANT EXECUTE ON FUNCTION calculate_candidate_experience(bigint), calculate_skill_match(bigint, bigint),
                          get_top_candidates(bigint, integer), get_application_pipeline(bigint),
                          get_recruiter_statistics(bigint) TO app_user;

GRANT SELECT ON users, applicants, companies, company_invites, recruiters, skills, job_postings, job_skills,
                candidate_skills, resumes, education, experience, projects, certifications,
                resume_embeddings, job_embeddings, applications, application_status_transitions,
                screening_results, search_history, interviews, interview_feedback, offers,
                candidate_feedback, notifications,
                candidate_ranking_view, active_jobs_view, recruitment_pipeline_view
             TO app_user;

GRANT INSERT ON applicants, job_postings, job_skills, job_embeddings, candidate_skills, resumes,
                education, experience, projects, certifications, applications, screening_results,
                search_history, interviews, interview_feedback, offers, audit_logs
             TO app_user;

GRANT DELETE ON job_skills, candidate_skills, education, experience, projects, certifications TO app_user;

-- Column-level UPDATE grants: anything not listed cannot be changed by a user, whatever the SQL.
GRANT UPDATE (first_name, last_name, headline, phone, location, date_of_birth, linkedin_url, github_url,
              portfolio_url, notice_period_days, is_discoverable)              ON applicants        TO app_user;
GRANT UPDATE (company_name, industry, description, location, website)          ON companies         TO app_user;
GRANT UPDATE (title, department, description, location, employment_type, is_remote, min_experience,
              max_experience, min_salary, max_salary, status, closing_date, deleted_at)
                                                                                ON job_postings      TO app_user;
GRANT UPDATE (is_required, importance_weight)                                   ON job_skills        TO app_user;
GRANT UPDATE (embedding, model_name, model_version, created_at)                 ON job_embeddings    TO app_user;
GRANT UPDATE (proficiency_level, years_experience, source)                      ON candidate_skills  TO app_user;
GRANT UPDATE (institution, degree, field_of_study, start_year, end_year, cgpa)  ON education         TO app_user;
GRANT UPDATE (company_name, designation, start_date, end_date, description, is_current) ON experience TO app_user;
GRANT UPDATE (title, description, technologies, start_date, end_date, project_url) ON projects       TO app_user;
GRANT UPDATE (name, issuer, issue_date, expiry_date, credential_url)            ON certifications    TO app_user;
GRANT UPDATE (status)                                                           ON applications      TO app_user;
GRANT UPDATE (semantic_score, skill_score, experience_score, education_score, overall_score,
              recommendation, strengths, missing_skills, ai_explanation, llm_used, model_name,
              model_version, screened_at)                                        ON screening_results TO app_user;
GRANT UPDATE (interviewer_id, scheduled_at, duration_minutes, interview_type, meeting_link, notes, status)
                                                                                ON interviews        TO app_user;
GRANT UPDATE (offer_status, salary, currency, joining_date, expires_at, notes)  ON offers            TO app_user;
GRANT UPDATE (is_read)                                                          ON notifications     TO app_user;

-- ---------------------------------------------------------------------------------------------
-- 3. Row-Level Security. Enabled on every table; a table with no policy for app_user is simply
--    invisible to it (notifications_outbox, for example).
-- ---------------------------------------------------------------------------------------------
DO $$
DECLARE t record;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    END LOOP;
END $$;

-- users: yourself, applicants you may view, and your own teammates.
CREATE POLICY users_select ON users FOR SELECT TO app_user USING (
    user_id = app.uid()
    OR EXISTS (SELECT 1 FROM applicants a WHERE a.user_id = users.user_id
               AND app.recruiter_can_view_applicant(a.applicant_id))
    OR EXISTS (SELECT 1 FROM recruiters r WHERE r.user_id = users.user_id AND r.company_id = app.company_id())
);

-- applicants
CREATE POLICY applicants_select ON applicants FOR SELECT TO app_user
    USING (user_id = app.uid() OR app.recruiter_can_view_applicant(applicant_id));
CREATE POLICY applicants_insert ON applicants FOR INSERT TO app_user
    WITH CHECK (user_id = app.uid() AND app.user_role() = 'APPLICANT');
CREATE POLICY applicants_update ON applicants FOR UPDATE TO app_user
    USING (user_id = app.uid()) WITH CHECK (user_id = app.uid());

-- companies are public (job listings show them); only the company's admins edit them.
CREATE POLICY companies_select ON companies FOR SELECT TO app_user USING (TRUE);
CREATE POLICY companies_update ON companies FOR UPDATE TO app_user
    USING (EXISTS (SELECT 1 FROM recruiters r WHERE r.company_id = companies.company_id
                   AND r.user_id = app.uid() AND r.is_company_admin));

CREATE POLICY company_invites_select ON company_invites FOR SELECT TO app_user
    USING (EXISTS (SELECT 1 FROM recruiters r WHERE r.company_id = company_invites.company_id
                   AND r.user_id = app.uid() AND r.is_company_admin));

-- recruiters: your company's team, plus anyone interviewing you.
CREATE POLICY recruiters_select ON recruiters FOR SELECT TO app_user USING (
    company_id = app.company_id()
    OR EXISTS (SELECT 1 FROM interviews i WHERE i.interviewer_id = recruiters.recruiter_id
               AND app.application_applicant(i.application_id) = app.applicant_id())
);

CREATE POLICY skills_select ON skills FOR SELECT TO app_user USING (TRUE);
CREATE POLICY transitions_select ON application_status_transitions FOR SELECT TO app_user USING (TRUE);

-- job_postings: open jobs are public; a company sees all of its own; applicants keep seeing jobs
-- they applied to after those close.
CREATE POLICY jobs_select ON job_postings FOR SELECT TO app_user USING (
    (status = 'OPEN' AND deleted_at IS NULL)
    OR company_id = app.company_id()
    OR job_id IN (SELECT job_id FROM applications WHERE applicant_id = app.applicant_id())
);
CREATE POLICY jobs_insert ON job_postings FOR INSERT TO app_user
    WITH CHECK (company_id = app.company_id() AND recruiter_id = app.recruiter_id());
CREATE POLICY jobs_update ON job_postings FOR UPDATE TO app_user
    USING (company_id = app.company_id()) WITH CHECK (company_id = app.company_id());

CREATE POLICY job_skills_select ON job_skills FOR SELECT TO app_user USING (
    app.job_is_public(job_id) OR app.job_company(job_id) = app.company_id()
    OR EXISTS (SELECT 1 FROM applications a WHERE a.job_id = job_skills.job_id AND a.applicant_id = app.applicant_id())
);
CREATE POLICY job_skills_write ON job_skills FOR ALL TO app_user
    USING (app.job_company(job_id) = app.company_id()) WITH CHECK (app.job_company(job_id) = app.company_id());

CREATE POLICY job_embeddings_select ON job_embeddings FOR SELECT TO app_user
    USING (app.job_is_public(job_id) OR app.job_company(job_id) = app.company_id());
CREATE POLICY job_embeddings_insert ON job_embeddings FOR INSERT TO app_user
    WITH CHECK (app.job_company(job_id) = app.company_id());
CREATE POLICY job_embeddings_update ON job_embeddings FOR UPDATE TO app_user
    USING (app.job_company(job_id) = app.company_id()) WITH CHECK (app.job_company(job_id) = app.company_id());

-- Applicant-owned profile tables: the owner has full access, recruiters with visibility can read.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['candidate_skills', 'education', 'experience', 'projects', 'certifications'] LOOP
        EXECUTE format('CREATE POLICY %1$s_owner ON %1$I FOR ALL TO app_user
                        USING (applicant_id = app.applicant_id()) WITH CHECK (applicant_id = app.applicant_id())', t);
        EXECUTE format('CREATE POLICY %1$s_recruiter_read ON %1$I FOR SELECT TO app_user
                        USING (app.recruiter_can_view_applicant(applicant_id))', t);
    END LOOP;
END $$;

-- resumes: the owner uploads and reads; visible recruiters read. Processing is system-only.
CREATE POLICY resumes_select ON resumes FOR SELECT TO app_user
    USING (applicant_id = app.applicant_id() OR app.recruiter_can_view_applicant(applicant_id));
CREATE POLICY resumes_insert ON resumes FOR INSERT TO app_user
    WITH CHECK (applicant_id = app.applicant_id());

-- An embedding is visible exactly when its resume is: the policy defers to resumes' own RLS.
CREATE POLICY resume_embeddings_select ON resume_embeddings FOR SELECT TO app_user
    USING (EXISTS (SELECT 1 FROM resumes r WHERE r.resume_id = resume_embeddings.resume_id));

-- applications
CREATE POLICY applications_select ON applications FOR SELECT TO app_user
    USING (applicant_id = app.applicant_id() OR app.job_company(job_id) = app.company_id());
CREATE POLICY applications_insert ON applications FOR INSERT TO app_user
    WITH CHECK (applicant_id = app.applicant_id() AND app.user_role() = 'APPLICANT');
CREATE POLICY applications_update ON applications FOR UPDATE TO app_user
    USING (applicant_id = app.applicant_id() OR app.job_company(job_id) = app.company_id())
    WITH CHECK (applicant_id = app.applicant_id() OR app.job_company(job_id) = app.company_id());

-- AI screening scores are internal to the hiring company. Candidates see candidate_feedback.
CREATE POLICY screening_company ON screening_results FOR ALL TO app_user
    USING (app.application_company(application_id) = app.company_id())
    WITH CHECK (app.application_company(application_id) = app.company_id());

CREATE POLICY search_history_own ON search_history FOR ALL TO app_user
    USING (recruiter_id = app.recruiter_id()) WITH CHECK (recruiter_id = app.recruiter_id());

CREATE POLICY interviews_select ON interviews FOR SELECT TO app_user USING (
    app.application_company(application_id) = app.company_id()
    OR app.application_applicant(application_id) = app.applicant_id()
);
CREATE POLICY interviews_insert ON interviews FOR INSERT TO app_user
    WITH CHECK (app.application_company(application_id) = app.company_id());
CREATE POLICY interviews_update ON interviews FOR UPDATE TO app_user
    USING (app.application_company(application_id) = app.company_id())
    WITH CHECK (app.application_company(application_id) = app.company_id());

CREATE POLICY interview_feedback_select ON interview_feedback FOR SELECT TO app_user
    USING (EXISTS (SELECT 1 FROM interviews i WHERE i.interview_id = interview_feedback.interview_id
                   AND app.application_company(i.application_id) = app.company_id()));
CREATE POLICY interview_feedback_insert ON interview_feedback FOR INSERT TO app_user
    WITH CHECK (interviewer_id = app.recruiter_id()
                AND EXISTS (SELECT 1 FROM interviews i WHERE i.interview_id = interview_feedback.interview_id
                            AND app.application_company(i.application_id) = app.company_id()));

CREATE POLICY offers_select ON offers FOR SELECT TO app_user USING (
    app.application_company(application_id) = app.company_id()
    OR app.application_applicant(application_id) = app.applicant_id()
);
CREATE POLICY offers_insert ON offers FOR INSERT TO app_user
    WITH CHECK (app.application_company(application_id) = app.company_id());
CREATE POLICY offers_update ON offers FOR UPDATE TO app_user
    USING (app.application_company(application_id) = app.company_id()
           OR app.application_applicant(application_id) = app.applicant_id());

CREATE POLICY candidate_feedback_select ON candidate_feedback FOR SELECT TO app_user USING (
    app.application_applicant(application_id) = app.applicant_id()
    OR app.application_company(application_id) = app.company_id()
);

CREATE POLICY notifications_own ON notifications FOR SELECT TO app_user USING (user_id = app.uid());
CREATE POLICY notifications_mark_read ON notifications FOR UPDATE TO app_user
    USING (user_id = app.uid()) WITH CHECK (user_id = app.uid());

-- Users can append audit rows about themselves; nobody reads them through the API.
CREATE POLICY audit_insert_self ON audit_logs FOR INSERT TO app_user WITH CHECK (user_id = app.uid());
