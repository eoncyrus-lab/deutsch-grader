PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tenant (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT 'cn',
  data_policy TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS school (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS class (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  school_id TEXT NOT NULL REFERENCES school(id),
  code TEXT NOT NULL,
  grade TEXT NOT NULL,
  term TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_user (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  email TEXT NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('teacher','rubric_owner','admin','dpo','student')),
  password_hash TEXT NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS session (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES app_user(id),
  csrf TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempt (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  ip TEXT NOT NULL,
  ok INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS student (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  class_id TEXT NOT NULL REFERENCES class(id),
  pseudonym TEXT NOT NULL,
  birth_year INTEGER,
  under_14 INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS guardian_consent (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  student_id TEXT NOT NULL REFERENCES student(id),
  scope TEXT NOT NULL,
  notice_version TEXT NOT NULL DEFAULT 'v1',
  purpose_version TEXT NOT NULL DEFAULT 'v1',
  granted_at TEXT,
  expires_at TEXT,
  revoked_at TEXT,
  evidence_ref TEXT
);

CREATE TABLE IF NOT EXISTS task_config (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  task_id TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','shadow','active','retired')),
  config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  rubric_id TEXT NOT NULL,
  rubric_version TEXT NOT NULL,
  created_by TEXT,
  created_at TEXT NOT NULL,
  activated_at TEXT,
  gate_report_id TEXT,
  UNIQUE (tenant_id, task_id, version)
);

CREATE TABLE IF NOT EXISTS gate_report (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  task_config_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pass','fail','demo')),
  dataset_hash TEXT NOT NULL,
  metrics TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_route (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  name TEXT NOT NULL,
  stage TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  family TEXT NOT NULL,
  fingerprint TEXT,
  params TEXT NOT NULL DEFAULT '{}',
  UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS quota_source (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  unit TEXT NOT NULL CHECK (unit IN ('token','call','money')),
  total_units REAL NOT NULL,
  used_units REAL NOT NULL DEFAULT 0,
  reserved_units REAL NOT NULL DEFAULT 0,
  window_seconds INTEGER NOT NULL DEFAULT 86400,
  window_start TEXT NOT NULL,
  reset_at TEXT NOT NULL,
  concurrency INTEGER NOT NULL DEFAULT 2,
  rate_per_minute INTEGER NOT NULL DEFAULT 60,
  terms_ok INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT,
  fallback_order INTEGER NOT NULL DEFAULT 100,
  pay_as_you_go INTEGER NOT NULL DEFAULT 0,
  monthly_budget REAL,
  monthly_spent REAL NOT NULL DEFAULT 0,
  price_per_1k REAL NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS quota_usage (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  run_id TEXT,
  stage TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES quota_source(id),
  model TEXT NOT NULL,
  units REAL NOT NULL DEFAULT 0,
  reserved_units REAL NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('reserved','settled','released')),
  created_at TEXT NOT NULL,
  settled_at TEXT
);

CREATE TABLE IF NOT EXISTS assignment (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  class_id TEXT NOT NULL REFERENCES class(id),
  task_config_id TEXT NOT NULL REFERENCES task_config(id),
  topic TEXT NOT NULL,
  due_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submission (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  assignment_id TEXT REFERENCES assignment(id),
  student_id TEXT REFERENCES student(id),
  task_config_id TEXT NOT NULL REFERENCES task_config(id),
  status TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'upload',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submission_image (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  submission_id TEXT NOT NULL REFERENCES submission(id),
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  content_type TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  redacted_at TEXT,
  deleted_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  submission_id TEXT NOT NULL REFERENCES submission(id),
  version INTEGER NOT NULL,
  title TEXT,
  lines TEXT NOT NULL,
  illegible TEXT NOT NULL DEFAULT '[]',
  redactions INTEGER NOT NULL DEFAULT 0,
  confirmed_by TEXT,
  confirmed_at TEXT,
  model TEXT,
  prompt_version TEXT,
  raw_cer REAL,
  created_at TEXT NOT NULL,
  UNIQUE (submission_id, version)
);

CREATE TABLE IF NOT EXISTS run (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  submission_id TEXT NOT NULL REFERENCES submission(id),
  pipeline_version TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  error TEXT,
  needs_manual INTEGER NOT NULL DEFAULT 0,
  ai_total REAL,
  reviewed_total REAL,
  final_total REAL,
  comments TEXT NOT NULL DEFAULT '{}',
  model_fingerprints TEXT NOT NULL DEFAULT '{}',
  sentence_flags TEXT NOT NULL DEFAULT '[]',
  coverage TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS stage_output (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  run_id TEXT NOT NULL REFERENCES run(id),
  stage TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  schema_version TEXT NOT NULL DEFAULT 'v1',
  input_hash TEXT NOT NULL,
  output_sha TEXT,
  parent_id TEXT,
  output TEXT,
  selected INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  latency_ms INTEGER DEFAULT 0,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  model TEXT,
  source_id TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (run_id, stage, attempt)
);

CREATE TABLE IF NOT EXISTS error_item (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  run_id TEXT NOT NULL REFERENCES run(id),
  sid INTEGER NOT NULL,
  original TEXT NOT NULL,
  corrected TEXT NOT NULL,
  span_start INTEGER NOT NULL,
  span_end INTEGER NOT NULL,
  type TEXT NOT NULL,
  error_code TEXT NOT NULL DEFAULT '',
  pattern TEXT NOT NULL,
  certainty TEXT NOT NULL,
  rule TEXT NOT NULL,
  minimal_edit INTEGER NOT NULL DEFAULT 1,
  guard_status TEXT NOT NULL DEFAULT 'pass',
  verifier_status TEXT NOT NULL DEFAULT 'pending',
  terminal_status TEXT NOT NULL DEFAULT 'candidate',
  group_id TEXT,
  is_primary INTEGER NOT NULL DEFAULT 1,
  review_verdict TEXT,
  review_reason TEXT,
  teacher_action TEXT NOT NULL DEFAULT 'pending',
  effective INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checklist_item (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  run_id TEXT NOT NULL REFERENCES run(id),
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  evidence TEXT,
  valid INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  UNIQUE (run_id, key)
);

CREATE TABLE IF NOT EXISTS score (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  run_id TEXT NOT NULL REFERENCES run(id),
  kind TEXT NOT NULL CHECK (kind IN ('ai','reviewed','final')),
  seq INTEGER NOT NULL,
  language REAL NOT NULL,
  content REAL NOT NULL,
  structure REAL NOT NULL,
  total REAL NOT NULL,
  scoring_revision INTEGER NOT NULL DEFAULT 1,
  draft INTEGER NOT NULL DEFAULT 0,
  trace TEXT NOT NULL,
  title_deducted REAL NOT NULL DEFAULT 0,
  cap_applied REAL,
  computed_at TEXT NOT NULL,
  UNIQUE (run_id, kind, seq)
);

CREATE TABLE IF NOT EXISTS teacher_review (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  submission_id TEXT NOT NULL REFERENCES submission(id),
  run_id TEXT NOT NULL REFERENCES run(id),
  reviewer_id TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  note TEXT,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_sample (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  submission_id TEXT NOT NULL REFERENCES submission(id),
  reason TEXT NOT NULL,
  selected_at TEXT NOT NULL,
  second_reviewer_id TEXT,
  second_score REAL,
  status TEXT NOT NULL DEFAULT 'pending',
  UNIQUE (submission_id)
);

CREATE TABLE IF NOT EXISTS profile_pattern (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  student_id TEXT NOT NULL REFERENCES student(id),
  pattern TEXT NOT NULL,
  type TEXT NOT NULL,
  occurrences INTEGER NOT NULL,
  words INTEGER NOT NULL,
  rate REAL NOT NULL,
  status TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  positive_evidence INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE (student_id, pattern)
);

CREATE TABLE IF NOT EXISTS pattern_evidence (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  student_id TEXT NOT NULL REFERENCES student(id),
  pattern TEXT NOT NULL,
  kind TEXT NOT NULL,
  run_id TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','dead')),
  priority INTEGER NOT NULL DEFAULT 100,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  not_before TEXT NOT NULL,
  locked_by TEXT,
  locked_at TEXT,
  lease_until TEXT,
  idempotency_key TEXT NOT NULL,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenant(id),
  actor_id TEXT,
  action TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT,
  detail TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_submission_tenant_id ON submission(tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_run_tenant_id ON run(tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_transcript_tenant_id ON transcript(tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_stage_output_tenant_id ON stage_output(tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_error_item_tenant_id ON error_item(tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_checklist_item_tenant_id ON checklist_item(tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_score_tenant_id ON score(tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_teacher_review_tenant_id ON teacher_review(tenant_id, id);
CREATE INDEX IF NOT EXISTS ix_submission_tenant_status ON submission(tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_error_run ON error_item(tenant_id, run_id, effective);
CREATE INDEX IF NOT EXISTS ix_job_claim ON job(status, not_before, priority, created_at);
