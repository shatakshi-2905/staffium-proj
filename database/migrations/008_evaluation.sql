-- 008_evaluation.sql
-- Results of the offline screening-quality and fairness evaluation (eval/run_eval.py).
--
-- Kept relationally so runs can be compared over time with plain SQL, e.g.
--   SELECT r.run_id, r.started_at, m.value
--   FROM eval_runs r JOIN eval_metrics m USING (run_id)
--   WHERE m.job_key = 'backend' AND m.mode = 'applications' AND m.metric = 'ndcg@5'
--   ORDER BY r.started_at;
-- These tables are internal: RLS is on and app_user has no grants, so nothing here is reachable
-- through the API.

SET search_path = public, extensions;

CREATE TABLE eval_runs (
    run_id           BIGSERIAL PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    dataset_version  VARCHAR(40) NOT NULL,
    git_commit       VARCHAR(40),
    config           JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(config) = 'object'),
    summary          JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(summary) = 'object'),
    passed           BOOLEAN,
    CONSTRAINT eval_finished_after_start CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE eval_metrics (
    run_id   BIGINT NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
    job_key  VARCHAR(40) NOT NULL,                         -- a job from the dataset, or 'all' for macro averages
    mode     VARCHAR(20) NOT NULL CHECK (mode IN ('applications', 'talent', 'ablation')),
    variant  VARCHAR(30) NOT NULL DEFAULT 'pipeline',      -- pipeline | vector_only | hybrid | ...
    metric   VARCHAR(30) NOT NULL,
    value    DOUBLE PRECISION,
    PRIMARY KEY (run_id, job_key, mode, variant, metric)
);

CREATE TABLE eval_findings (
    finding_id  BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES eval_runs(run_id) ON DELETE CASCADE,
    kind        VARCHAR(20) NOT NULL CHECK (kind IN ('FALSE_NEGATIVE', 'COUNTERFACTUAL', 'ADVERSE_IMPACT')),
    severity    VARCHAR(8)  NOT NULL CHECK (severity IN ('info', 'warn', 'fail')),
    job_key     VARCHAR(40),
    subject     VARCHAR(120) NOT NULL,                      -- candidate ref or attribute name
    details     JSONB NOT NULL CHECK (jsonb_typeof(details) = 'object')
);
CREATE INDEX idx_eval_findings_run ON eval_findings (run_id, kind);

ALTER TABLE eval_runs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_metrics  ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_findings ENABLE ROW LEVEL SECURITY;

-- Supabase's REST roles never get access to these tables (belt and braces on top of 005).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated';
    END IF;
END $$;
