-- Production-oriented PostgreSQL draft. The pilot runs on SQLite; this file is
-- the migration target for RLS, composite tenant foreign keys and JSONB.
-- It is not executed by the test suite.

CREATE TABLE tenant (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT 'cn',
  data_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE school (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  name TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, id)
);

CREATE TABLE class (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  school_id TEXT NOT NULL,
  code TEXT NOT NULL,
  grade TEXT NOT NULL,
  term TEXT NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, school_id) REFERENCES school(tenant_id, id)
);

CREATE TABLE app_user (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  email TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('teacher','rubric_owner','admin','dpo','student')),
  password_hash TEXT NOT NULL,
  disabled BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, email)
);

CREATE TABLE session (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  csrf TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE student (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  class_id TEXT NOT NULL,
  pseudonym TEXT NOT NULL,
  birth_year INT,
  under_14 BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, class_id) REFERENCES class(tenant_id, id)
);

CREATE TABLE guardian_consent (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  student_id TEXT NOT NULL,
  scope JSONB NOT NULL,
  notice_version TEXT NOT NULL DEFAULT 'v1',
  purpose_version TEXT NOT NULL DEFAULT 'v1',
  granted_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  evidence_ref TEXT,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, student_id) REFERENCES student(tenant_id, id)
);

CREATE TABLE task_config (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  task_id TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','shadow','active','retired')),
  config_json JSONB NOT NULL,
  config_hash TEXT NOT NULL,
  rubric_id TEXT NOT NULL,
  rubric_version TEXT NOT NULL,
  gate_report_id TEXT,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  activated_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, task_id, version)
);

CREATE TABLE gate_report (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  task_config_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pass','fail','demo')),
  dataset_hash TEXT NOT NULL,
  metrics JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, task_config_id) REFERENCES task_config(tenant_id, id)
);

CREATE TABLE quota_source (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  unit TEXT NOT NULL CHECK (unit IN ('token','call','money')),
  total_units NUMERIC NOT NULL,
  used_units NUMERIC NOT NULL DEFAULT 0,
  reserved_units NUMERIC NOT NULL DEFAULT 0,
  window_seconds INT NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  reset_at TIMESTAMPTZ NOT NULL,
  concurrency INT NOT NULL DEFAULT 1,
  rate_per_minute INT NOT NULL DEFAULT 60,
  terms_ok BOOLEAN NOT NULL DEFAULT false,
  expires_at TIMESTAMPTZ,
  fallback_order INT NOT NULL DEFAULT 100,
  pay_as_you_go BOOLEAN NOT NULL DEFAULT false,
  monthly_budget NUMERIC,
  monthly_spent NUMERIC NOT NULL DEFAULT 0,
  price_per_1k NUMERIC NOT NULL DEFAULT 0,
  enabled BOOLEAN NOT NULL DEFAULT true,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, name)
);

CREATE TABLE quota_usage (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  run_id TEXT,
  stage TEXT NOT NULL,
  source_id TEXT NOT NULL,
  model TEXT NOT NULL,
  units NUMERIC NOT NULL DEFAULT 0,
  reserved_units NUMERIC NOT NULL DEFAULT 0,
  cost NUMERIC NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('reserved','settled','released')),
  provider_request_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  settled_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, source_id) REFERENCES quota_source(tenant_id, id)
);

CREATE TABLE assignment (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  class_id TEXT NOT NULL,
  task_config_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  due_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, class_id) REFERENCES class(tenant_id, id),
  FOREIGN KEY (tenant_id, task_config_id) REFERENCES task_config(tenant_id, id)
);

CREATE TABLE submission (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  assignment_id TEXT,
  student_id TEXT,
  task_config_id TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'upload',
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, assignment_id) REFERENCES assignment(tenant_id, id),
  FOREIGN KEY (tenant_id, student_id) REFERENCES student(tenant_id, id),
  FOREIGN KEY (tenant_id, task_config_id) REFERENCES task_config(tenant_id, id)
);

CREATE TABLE submission_image (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  content_type TEXT NOT NULL,
  bytes INT NOT NULL,
  redacted_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES submission(tenant_id, id)
);

CREATE TABLE transcript (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  version INT NOT NULL,
  title TEXT,
  lines JSONB NOT NULL,
  illegible JSONB NOT NULL DEFAULT '[]'::jsonb,
  redactions INT NOT NULL DEFAULT 0,
  confirmed_by TEXT,
  confirmed_at TIMESTAMPTZ,
  model TEXT,
  prompt_version TEXT,
  raw_cer NUMERIC,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (submission_id, version),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES submission(tenant_id, id)
);

CREATE TABLE run (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  error TEXT,
  needs_manual BOOLEAN NOT NULL DEFAULT false,
  ai_total NUMERIC,
  reviewed_total NUMERIC,
  final_total NUMERIC,
  comments JSONB NOT NULL DEFAULT '{}'::jsonb,
  model_fingerprints JSONB NOT NULL DEFAULT '{}'::jsonb,
  sentence_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
  coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES submission(tenant_id, id)
);

CREATE TABLE stage_output (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  attempt INT NOT NULL,
  schema_version TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  output_sha TEXT,
  parent_id TEXT,
  output JSONB,
  selected BOOLEAN NOT NULL DEFAULT false,
  status TEXT NOT NULL,
  latency_ms INT NOT NULL DEFAULT 0,
  tokens_in INT NOT NULL DEFAULT 0,
  tokens_out INT NOT NULL DEFAULT 0,
  model TEXT,
  source_id TEXT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (run_id, stage, attempt),
  FOREIGN KEY (tenant_id, run_id) REFERENCES run(tenant_id, id)
);

CREATE TABLE error_item (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  sid INT NOT NULL,
  original TEXT NOT NULL,
  corrected TEXT NOT NULL,
  span_start INT NOT NULL,
  span_end INT NOT NULL,
  type TEXT NOT NULL,
  error_code TEXT NOT NULL DEFAULT '',
  pattern TEXT NOT NULL,
  certainty TEXT NOT NULL,
  rule TEXT NOT NULL,
  minimal_edit BOOLEAN NOT NULL DEFAULT true,
  guard_status TEXT NOT NULL DEFAULT 'pass',
  verifier_status TEXT NOT NULL DEFAULT 'pending',
  terminal_status TEXT NOT NULL DEFAULT 'candidate',
  group_id TEXT,
  is_primary BOOLEAN NOT NULL DEFAULT true,
  review_verdict TEXT,
  review_reason TEXT,
  teacher_action TEXT NOT NULL DEFAULT 'pending',
  effective BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES run(tenant_id, id)
);

CREATE TABLE checklist_item (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value JSONB NOT NULL,
  evidence JSONB,
  valid BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (run_id, key),
  FOREIGN KEY (tenant_id, run_id) REFERENCES run(tenant_id, id)
);

CREATE TABLE score (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('ai','reviewed','final')),
  seq INT NOT NULL,
  language NUMERIC NOT NULL,
  content NUMERIC NOT NULL,
  structure NUMERIC NOT NULL,
  total NUMERIC NOT NULL,
  scoring_revision INT NOT NULL DEFAULT 1,
  draft BOOLEAN NOT NULL DEFAULT false,
  trace JSONB NOT NULL,
  title_deducted NUMERIC NOT NULL DEFAULT 0,
  cap_applied NUMERIC,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (run_id, kind, seq),
  FOREIGN KEY (tenant_id, run_id) REFERENCES run(tenant_id, id)
);

CREATE TABLE teacher_review (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  reviewer_id TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  note TEXT,
  before_json JSONB NOT NULL,
  after_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES run(tenant_id, id)
);

CREATE TABLE calibration_sample (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  selected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  second_reviewer_id TEXT,
  second_score NUMERIC,
  status TEXT NOT NULL DEFAULT 'pending',
  PRIMARY KEY (tenant_id, id),
  UNIQUE (submission_id),
  FOREIGN KEY (tenant_id, submission_id) REFERENCES submission(tenant_id, id)
);

CREATE TABLE profile_pattern (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  student_id TEXT NOT NULL,
  pattern TEXT NOT NULL,
  type TEXT NOT NULL,
  occurrences INT NOT NULL,
  words INT NOT NULL,
  rate NUMERIC NOT NULL,
  status TEXT NOT NULL,
  last_seen TIMESTAMPTZ NOT NULL,
  positive_evidence INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (student_id, pattern),
  FOREIGN KEY (tenant_id, student_id) REFERENCES student(tenant_id, id)
);

CREATE TABLE pattern_evidence (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  student_id TEXT NOT NULL,
  pattern TEXT NOT NULL,
  kind TEXT NOT NULL,
  run_id TEXT,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, student_id) REFERENCES student(tenant_id, id)
);

CREATE TABLE job (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  kind TEXT NOT NULL,
  payload JSONB NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','dead')),
  priority INT NOT NULL DEFAULT 100,
  attempts INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,
  not_before TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_by TEXT,
  locked_at TIMESTAMPTZ,
  lease_until TIMESTAMPTZ,
  idempotency_key TEXT NOT NULL,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE audit_log (
  id TEXT NOT NULL,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  actor_id TEXT,
  action TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT,
  detail JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id)
);

CREATE INDEX ix_submission_tenant_status ON submission(tenant_id, status, created_at);
CREATE INDEX ix_job_claim ON job(status, not_before, priority, created_at);
CREATE INDEX ix_error_run ON error_item(tenant_id, run_id, effective);
CREATE INDEX ix_quota_usage_run ON quota_usage(tenant_id, run_id, stage);

-- Every table with tenant_id gets row-level security. The worker sets
-- app.tenant_id inside the same transaction that claims a job.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename IN (
        'school','class','app_user','student','guardian_consent','task_config','gate_report',
        'quota_source','quota_usage','assignment','submission','submission_image','transcript',
        'run','stage_output','error_item','checklist_item','score','teacher_review',
        'calibration_sample','profile_pattern','pattern_evidence','job','audit_log'
      )
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', r.tablename);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))',
      r.tablename
    );
  END LOOP;
END $$;
