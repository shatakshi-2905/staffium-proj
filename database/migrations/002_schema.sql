-- 002_schema.sql
-- Core relational schema (3NF). Credentials live in Supabase's auth.users; public.users mirrors each
-- account and adds the application role.

SET search_path = public, extensions;

-- ---------------------------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------------------------
CREATE TABLE users (
    user_id     UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email       VARCHAR(255) NOT NULL UNIQUE CHECK (email = lower(email)),
    role        VARCHAR(20)  NOT NULL CHECK (role IN ('APPLICANT', 'RECRUITER', 'ADMIN')),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE applicants (
    applicant_id        BIGSERIAL PRIMARY KEY,
    user_id             UUID NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
    first_name          VARCHAR(100) NOT NULL CHECK (length(trim(first_name)) > 0),
    last_name           VARCHAR(100) NOT NULL CHECK (length(trim(last_name)) > 0),
    headline            VARCHAR(160),
    phone               VARCHAR(20)  CHECK (phone IS NULL OR phone ~ '^\+?[0-9 ()-]{7,20}$'),
    location            VARCHAR(150),
    date_of_birth       DATE CHECK (date_of_birth IS NULL OR date_of_birth > DATE '1900-01-01'),
    linkedin_url        TEXT CHECK (linkedin_url  IS NULL OR linkedin_url  ~* '^https?://'),
    github_url          TEXT CHECK (github_url    IS NULL OR github_url    ~* '^https?://'),
    portfolio_url       TEXT CHECK (portfolio_url IS NULL OR portfolio_url ~* '^https?://'),
    notice_period_days  INTEGER CHECK (notice_period_days IS NULL OR notice_period_days BETWEEN 0 AND 365),
    is_discoverable     BOOLEAN NOT NULL DEFAULT TRUE,   -- applicant opt-in to recruiter talent search
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE companies (
    company_id    BIGSERIAL PRIMARY KEY,
    company_name  VARCHAR(200) NOT NULL CHECK (length(trim(company_name)) > 0),
    industry      VARCHAR(100),
    description   TEXT CHECK (description IS NULL OR length(description) <= 4000),
    location      VARCHAR(150),
    website       TEXT CHECK (website IS NULL OR website ~* '^https?://'),
    created_by    UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Invite codes live apart from companies so company rows can be public (job listings show them)
-- while the code that grants membership stays visible only to the company's own recruiters.
CREATE TABLE company_invites (
    company_id   BIGINT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
    invite_code  VARCHAR(32) NOT NULL UNIQUE CHECK (length(invite_code) >= 16),
    rotated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE recruiters (
    recruiter_id      BIGSERIAL PRIMARY KEY,
    user_id           UUID NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
    company_id        BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    first_name        VARCHAR(100) NOT NULL CHECK (length(trim(first_name)) > 0),
    last_name         VARCHAR(100) NOT NULL CHECK (length(trim(last_name)) > 0),
    designation       VARCHAR(100),
    is_company_admin  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Skills dictionary and jobs
-- ---------------------------------------------------------------------------------------------
CREATE TABLE skills (
    skill_id    BIGSERIAL PRIMARY KEY,
    skill_name  VARCHAR(100) NOT NULL,
    category    VARCHAR(50)
);
CREATE UNIQUE INDEX uq_skills_name_ci ON skills (lower(skill_name));

CREATE TABLE job_postings (
    job_id             BIGSERIAL PRIMARY KEY,
    company_id         BIGINT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    recruiter_id       BIGINT NOT NULL REFERENCES recruiters(recruiter_id),
    title              VARCHAR(200) NOT NULL CHECK (length(trim(title)) > 0),
    department         VARCHAR(100),
    description        TEXT NOT NULL CHECK (length(description) BETWEEN 30 AND 20000),
    location           VARCHAR(150),
    employment_type    VARCHAR(20) NOT NULL DEFAULT 'FULL_TIME'
                       CHECK (employment_type IN ('FULL_TIME', 'PART_TIME', 'CONTRACT', 'INTERNSHIP')),
    is_remote          BOOLEAN NOT NULL DEFAULT FALSE,
    min_experience     NUMERIC(4,1) NOT NULL DEFAULT 0 CHECK (min_experience >= 0),
    max_experience     NUMERIC(4,1) CHECK (max_experience IS NULL OR max_experience >= min_experience),
    min_salary         NUMERIC(12,2) CHECK (min_salary IS NULL OR min_salary >= 0),
    max_salary         NUMERIC(12,2) CHECK (max_salary IS NULL OR max_salary >= COALESCE(min_salary, 0)),
    status             VARCHAR(10) NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'OPEN', 'PAUSED', 'CLOSED')),
    application_count  INTEGER NOT NULL DEFAULT 0 CHECK (application_count >= 0),  -- maintained by trigger
    closing_date       DATE,
    search_vector      TSVECTOR GENERATED ALWAYS AS (
                           setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                           setweight(to_tsvector('english', coalesce(description, '')), 'B')
                       ) STORED,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at         TIMESTAMPTZ,                       -- soft delete; rows are never hard-deleted
    CONSTRAINT deleted_jobs_are_closed CHECK (deleted_at IS NULL OR status = 'CLOSED')
);

CREATE TABLE job_skills (
    job_id             BIGINT NOT NULL REFERENCES job_postings(job_id) ON DELETE CASCADE,
    skill_id           BIGINT NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
    is_required        BOOLEAN NOT NULL DEFAULT TRUE,
    importance_weight  NUMERIC(3,2) NOT NULL DEFAULT 1.0 CHECK (importance_weight > 0 AND importance_weight <= 1),
    PRIMARY KEY (job_id, skill_id)
);

-- ---------------------------------------------------------------------------------------------
-- Applicant profile
-- ---------------------------------------------------------------------------------------------
CREATE TABLE candidate_skills (
    applicant_id       BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    skill_id           BIGINT NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
    proficiency_level  VARCHAR(12) CHECK (proficiency_level IN ('BEGINNER', 'INTERMEDIATE', 'ADVANCED', 'EXPERT')),
    years_experience   NUMERIC(4,1) CHECK (years_experience IS NULL OR years_experience BETWEEN 0 AND 60),
    source             VARCHAR(12) NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('RESUME', 'MANUAL', 'AI_EXTRACTED')),
    PRIMARY KEY (applicant_id, skill_id)
);

CREATE TABLE resumes (
    resume_id             BIGSERIAL PRIMARY KEY,
    applicant_id          BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    s3_object_key         TEXT NOT NULL UNIQUE,
    file_name             VARCHAR(255) NOT NULL,
    file_type             VARCHAR(5) NOT NULL CHECK (file_type IN ('pdf', 'docx')),
    file_size             INTEGER NOT NULL CHECK (file_size > 0 AND file_size <= 5242880),
    content_sha256        CHAR(64) NOT NULL,
    version               INTEGER NOT NULL CHECK (version > 0),             -- assigned by trigger
    extracted_text        TEXT,
    parsed_data           JSONB,                                            -- structured metadata
    processing_status     VARCHAR(12) NOT NULL DEFAULT 'UPLOADED'
                          CHECK (processing_status IN ('UPLOADED', 'PROCESSING', 'COMPLETED', 'FAILED')),
    processing_error      TEXT,
    processing_attempts   SMALLINT NOT NULL DEFAULT 0,
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    search_vector         TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(extracted_text, ''))) STORED,
    uploaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at          TIMESTAMPTZ,
    UNIQUE (applicant_id, version)
);
-- At most one active resume per applicant.
CREATE UNIQUE INDEX uq_resumes_one_active ON resumes (applicant_id) WHERE is_active;

CREATE TABLE education (
    education_id    BIGSERIAL PRIMARY KEY,
    applicant_id    BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    institution     VARCHAR(200) NOT NULL,
    degree          VARCHAR(120) NOT NULL,
    field_of_study  VARCHAR(120),
    start_year      SMALLINT CHECK (start_year IS NULL OR start_year BETWEEN 1950 AND 2100),
    end_year        SMALLINT CHECK (end_year IS NULL OR end_year BETWEEN 1950 AND 2100),
    cgpa            NUMERIC(4,2) CHECK (cgpa IS NULL OR cgpa BETWEEN 0 AND 10),
    source          VARCHAR(8) NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'RESUME')),
    CONSTRAINT education_years_ordered CHECK (end_year IS NULL OR start_year IS NULL OR end_year >= start_year)
);

CREATE TABLE experience (
    experience_id  BIGSERIAL PRIMARY KEY,
    applicant_id   BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    company_name   VARCHAR(200) NOT NULL,
    designation    VARCHAR(150) NOT NULL,
    start_date     DATE NOT NULL,
    end_date       DATE,
    description    TEXT CHECK (description IS NULL OR length(description) <= 4000),
    is_current     BOOLEAN NOT NULL DEFAULT FALSE,
    source         VARCHAR(8) NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'RESUME')),
    CONSTRAINT experience_dates_ordered CHECK (end_date IS NULL OR end_date >= start_date),
    CONSTRAINT experience_current_has_no_end CHECK (NOT is_current OR end_date IS NULL)
);

CREATE TABLE projects (
    project_id    BIGSERIAL PRIMARY KEY,
    applicant_id  BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    title         VARCHAR(200) NOT NULL,
    description   TEXT CHECK (description IS NULL OR length(description) <= 4000),
    technologies  TEXT[] NOT NULL DEFAULT '{}',
    start_date    DATE,
    end_date      DATE,
    project_url   TEXT CHECK (project_url IS NULL OR project_url ~* '^https?://'),
    source        VARCHAR(8) NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'RESUME')),
    CONSTRAINT project_dates_ordered CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

CREATE TABLE certifications (
    certification_id  BIGSERIAL PRIMARY KEY,
    applicant_id      BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    name              VARCHAR(200) NOT NULL,
    issuer            VARCHAR(200),
    issue_date        DATE,
    expiry_date       DATE,
    credential_url    TEXT CHECK (credential_url IS NULL OR credential_url ~* '^https?://'),
    CONSTRAINT certification_dates_ordered CHECK (expiry_date IS NULL OR issue_date IS NULL OR expiry_date >= issue_date)
);

-- ---------------------------------------------------------------------------------------------
-- Embeddings (pgvector, 384 dims = BAAI/bge-small-en-v1.5). One current embedding per resume/job.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE resume_embeddings (
    embedding_id   BIGSERIAL PRIMARY KEY,
    resume_id      BIGINT NOT NULL UNIQUE REFERENCES resumes(resume_id) ON DELETE CASCADE,
    embedding      VECTOR(384) NOT NULL,
    model_name     VARCHAR(100) NOT NULL,
    model_version  VARCHAR(20) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job_embeddings (
    embedding_id   BIGSERIAL PRIMARY KEY,
    job_id         BIGINT NOT NULL UNIQUE REFERENCES job_postings(job_id) ON DELETE CASCADE,
    embedding      VECTOR(384) NOT NULL,
    model_name     VARCHAR(100) NOT NULL,
    model_version  VARCHAR(20) NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Recruitment pipeline
-- ---------------------------------------------------------------------------------------------
CREATE TABLE applications (
    application_id     BIGSERIAL PRIMARY KEY,
    job_id             BIGINT NOT NULL REFERENCES job_postings(job_id),
    applicant_id       BIGINT NOT NULL REFERENCES applicants(applicant_id) ON DELETE CASCADE,
    resume_id          BIGINT NOT NULL REFERENCES resumes(resume_id),
    status             VARCHAR(12) NOT NULL DEFAULT 'APPLIED'
                       CHECK (status IN ('APPLIED', 'SCREENING', 'SHORTLISTED', 'INTERVIEW',
                                         'OFFER', 'HIRED', 'REJECTED', 'WITHDRAWN')),
    applied_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, applicant_id)        -- one application per applicant per job (no reapplying)
);

-- The status lifecycle stored as data: which transitions are legal, and who may make them.
-- A trigger (004) checks every status change against this table.
CREATE TABLE application_status_transitions (
    from_status  VARCHAR(12) NOT NULL,
    to_status    VARCHAR(12) NOT NULL,
    actor        VARCHAR(10) NOT NULL CHECK (actor IN ('RECRUITER', 'APPLICANT')),
    PRIMARY KEY (from_status, to_status, actor)
);

CREATE TABLE screening_results (
    screening_id      BIGSERIAL PRIMARY KEY,
    application_id    BIGINT NOT NULL UNIQUE REFERENCES applications(application_id) ON DELETE CASCADE,
    semantic_score    NUMERIC(5,2) CHECK (semantic_score   BETWEEN 0 AND 100),
    skill_score       NUMERIC(5,2) CHECK (skill_score      BETWEEN 0 AND 100),
    experience_score  NUMERIC(5,2) CHECK (experience_score BETWEEN 0 AND 100),
    education_score   NUMERIC(5,2) CHECK (education_score  BETWEEN 0 AND 100),
    overall_score     NUMERIC(5,2) NOT NULL CHECK (overall_score BETWEEN 0 AND 100),
    recommendation    VARCHAR(12) NOT NULL CHECK (recommendation IN ('STRONG_MATCH', 'MATCH', 'WEAK_MATCH', 'REJECT')),
    strengths         JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(strengths) = 'array'),
    missing_skills    JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(missing_skills) = 'array'),
    ai_explanation    TEXT,
    llm_used          BOOLEAN NOT NULL DEFAULT FALSE,
    model_name        VARCHAR(100),
    model_version     VARCHAR(40),
    screened_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE search_history (
    search_id      BIGSERIAL PRIMARY KEY,
    recruiter_id   BIGINT NOT NULL REFERENCES recruiters(recruiter_id) ON DELETE CASCADE,
    job_id         BIGINT REFERENCES job_postings(job_id) ON DELETE SET NULL,
    query          TEXT NOT NULL CHECK (length(query) <= 20000),
    filters        JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(filters) = 'object'),
    results        JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(results) = 'array'),
    results_count  INTEGER NOT NULL DEFAULT 0 CHECK (results_count >= 0),
    llm_used       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE interviews (
    interview_id      BIGSERIAL PRIMARY KEY,
    application_id    BIGINT NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    interviewer_id    BIGINT NOT NULL REFERENCES recruiters(recruiter_id),
    scheduled_at      TIMESTAMPTZ NOT NULL,
    duration_minutes  INTEGER NOT NULL CHECK (duration_minutes BETWEEN 15 AND 480),
    ends_at           TIMESTAMPTZ NOT NULL,   -- scheduled_at + duration, set by trigger (see 004)
    interview_type    VARCHAR(12) NOT NULL DEFAULT 'VIDEO'
                      CHECK (interview_type IN ('PHONE', 'VIDEO', 'ONSITE', 'TECHNICAL', 'HR')),
    meeting_link      TEXT CHECK (meeting_link IS NULL OR meeting_link ~* '^https://'),
    notes             TEXT CHECK (notes IS NULL OR length(notes) <= 4000),
    status            VARCHAR(12) NOT NULL DEFAULT 'SCHEDULED'
                      CHECK (status IN ('SCHEDULED', 'COMPLETED', 'CANCELLED', 'RESCHEDULED')),
    sequence          INTEGER NOT NULL DEFAULT 0,   -- iCalendar SEQUENCE; bumped on every reschedule
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT interview_ends_after_start CHECK (ends_at > scheduled_at),
    -- An interviewer cannot hold two overlapping scheduled interviews...
    CONSTRAINT no_interviewer_double_booking
        EXCLUDE USING gist (interviewer_id WITH =, tstzrange(scheduled_at, ends_at, '[)') WITH &&)
        WHERE (status = 'SCHEDULED'),
    -- ...and a candidate cannot be booked into two overlapping interviews for the same application.
    CONSTRAINT no_candidate_double_booking
        EXCLUDE USING gist (application_id WITH =, tstzrange(scheduled_at, ends_at, '[)') WITH &&)
        WHERE (status = 'SCHEDULED')
);

CREATE TABLE interview_feedback (
    feedback_id            BIGSERIAL PRIMARY KEY,
    interview_id           BIGINT NOT NULL REFERENCES interviews(interview_id) ON DELETE CASCADE,
    interviewer_id         BIGINT NOT NULL REFERENCES recruiters(recruiter_id),
    technical_score        SMALLINT NOT NULL CHECK (technical_score BETWEEN 1 AND 10),
    communication_score    SMALLINT NOT NULL CHECK (communication_score BETWEEN 1 AND 10),
    problem_solving_score  SMALLINT NOT NULL CHECK (problem_solving_score BETWEEN 1 AND 10),
    overall_score          SMALLINT NOT NULL CHECK (overall_score BETWEEN 1 AND 10),
    recommendation         VARCHAR(12) NOT NULL CHECK (recommendation IN ('STRONG_HIRE', 'HIRE', 'NO_HIRE', 'STRONG_NO_HIRE')),
    comments               TEXT CHECK (comments IS NULL OR length(comments) <= 4000),
    submitted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (interview_id, interviewer_id)
);

CREATE TABLE offers (
    offer_id       BIGSERIAL PRIMARY KEY,
    application_id BIGINT NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    salary         NUMERIC(12,2) NOT NULL CHECK (salary > 0),
    currency       CHAR(3) NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$'),
    joining_date   DATE NOT NULL,
    offer_status   VARCHAR(10) NOT NULL DEFAULT 'PENDING'
                   CHECK (offer_status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'EXPIRED')),
    notes          TEXT CHECK (notes IS NULL OR length(notes) <= 4000),
    issued_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at     TIMESTAMPTZ NOT NULL,
    responded_at   TIMESTAMPTZ,
    CONSTRAINT offer_expiry_after_issue CHECK (expires_at > issued_at)
);
-- Only one live (pending or accepted) offer per application.
CREATE UNIQUE INDEX uq_offers_one_live ON offers (application_id) WHERE offer_status IN ('PENDING', 'ACCEPTED');

-- "Why not me": persisted explanation for every rejected application.
CREATE TABLE candidate_feedback (
    feedback_id          BIGSERIAL PRIMARY KEY,
    application_id       BIGINT NOT NULL UNIQUE REFERENCES applications(application_id) ON DELETE CASCADE,
    missing_skills       JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(missing_skills) = 'array'),
    match_score          NUMERIC(5,2),
    match_summary        TEXT,
    suggested_resources  JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(suggested_resources) = 'array'),
    suggestions_status   VARCHAR(8) NOT NULL DEFAULT 'PENDING' CHECK (suggestions_status IN ('PENDING', 'READY')),
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Notifications, outbox, audit
-- ---------------------------------------------------------------------------------------------
CREATE TABLE notifications (
    notification_id  BIGSERIAL PRIMARY KEY,
    user_id          UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    type             VARCHAR(40) NOT NULL,
    title            VARCHAR(200) NOT NULL,
    body             TEXT,
    link             VARCHAR(300) CHECK (link IS NULL OR link ~ '^#/'),   -- in-app routes only
    is_read          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE outbox_event AS ENUM (
    'APPLICATION_STATUS_CHANGED', 'INTERVIEW_SCHEDULED', 'INTERVIEW_RESCHEDULED',
    'INTERVIEW_CANCELLED', 'OFFER_ISSUED', 'FEEDBACK_READY'
);

-- Transactional outbox: rows are written by triggers in the same transaction as the change that
-- caused them; a background worker delivers them through AWS SES.
CREATE TABLE notifications_outbox (
    outbox_id        BIGSERIAL PRIMARY KEY,
    event_type       outbox_event NOT NULL,
    application_id   BIGINT REFERENCES applications(application_id) ON DELETE SET NULL,
    interview_id     BIGINT REFERENCES interviews(interview_id) ON DELETE SET NULL,
    recipient_email  VARCHAR(255) NOT NULL,
    payload          JSONB NOT NULL,
    status           VARCHAR(8) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at          TIMESTAMPTZ
);

CREATE TABLE audit_logs (
    log_id       BIGSERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    action       VARCHAR(50) NOT NULL,
    entity_type  VARCHAR(50) NOT NULL,
    entity_id    BIGINT,
    metadata     JSONB NOT NULL DEFAULT '{}',
    ip_address   INET,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------------------------
CREATE INDEX idx_job_postings_status          ON job_postings (status) WHERE deleted_at IS NULL;
CREATE INDEX idx_job_postings_company         ON job_postings (company_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_job_postings_search          ON job_postings USING gin (search_vector);
CREATE INDEX idx_job_skills_skill             ON job_skills (skill_id);
CREATE INDEX idx_candidate_skills_skill       ON candidate_skills (skill_id);
CREATE INDEX idx_resumes_applicant            ON resumes (applicant_id);
CREATE INDEX idx_resumes_search               ON resumes USING gin (search_vector);
CREATE INDEX idx_resumes_pending              ON resumes (uploaded_at) WHERE processing_status IN ('UPLOADED', 'PROCESSING');
CREATE INDEX idx_education_applicant          ON education (applicant_id);
CREATE INDEX idx_experience_applicant         ON experience (applicant_id);
CREATE INDEX idx_projects_applicant           ON projects (applicant_id);
CREATE INDEX idx_certifications_applicant     ON certifications (applicant_id);
CREATE INDEX idx_applications_job_status      ON applications (job_id, status);
CREATE INDEX idx_applications_applicant       ON applications (applicant_id);
CREATE INDEX idx_screening_overall_score      ON screening_results (overall_score DESC);
CREATE INDEX idx_interviews_application       ON interviews (application_id);
CREATE INDEX idx_interviews_interviewer_time  ON interviews (interviewer_id, scheduled_at);
CREATE INDEX idx_offers_pending_expiry        ON offers (expires_at) WHERE offer_status = 'PENDING';
CREATE INDEX idx_notifications_user_unread    ON notifications (user_id, created_at DESC) WHERE NOT is_read;
CREATE INDEX idx_notifications_user           ON notifications (user_id, created_at DESC);
CREATE INDEX idx_outbox_due                   ON notifications_outbox (next_attempt_at) WHERE status = 'PENDING';
CREATE INDEX idx_search_history_recruiter     ON search_history (recruiter_id, created_at DESC);
CREATE INDEX idx_audit_logs_user              ON audit_logs (user_id, created_at DESC);
CREATE INDEX idx_audit_logs_entity            ON audit_logs (entity_type, entity_id);

-- HNSW works well at any table size (IVFFlat needs a populated table before building lists).
CREATE INDEX idx_resume_embeddings_hnsw ON resume_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_job_embeddings_hnsw    ON job_embeddings    USING hnsw (embedding vector_cosine_ops);
