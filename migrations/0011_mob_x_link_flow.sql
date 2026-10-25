-- Mob X owner-confirmation flow. Additive panel_links columns and a compatible console_jobs rebuild.
-- This file is intentionally not executed by this change; owner approval is required before D1 migration.

ALTER TABLE panel_links ADD COLUMN external_id TEXT;
ALTER TABLE panel_links ADD COLUMN confirmed_at TEXT;
ALTER TABLE panel_links ADD COLUMN updated_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_panel_links_external_id_unique
  ON panel_links(external_id) WHERE external_id IS NOT NULL;

-- Expand the existing job status CHECK while preserving every stored job field and timestamp.
ALTER TABLE console_jobs RENAME TO console_jobs_before_mob_x_flow;

CREATE TABLE console_jobs (
  id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 8 AND 100),
  type TEXT NOT NULL CHECK (type IN ('queue_intake_dry_run', 'ai_health_check', 'log_zip', 'd1_backup', 'mob_x_link', 'requeue_stuck')),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('pending', 'confirmed', 'queued', 'running', 'processing', 'ready_for_review', 'sent', 'failed', 'rejected', 'uncertain', 'done')),
  progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  params_json TEXT,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  panel_link_id INTEGER
);

INSERT INTO console_jobs
  (id, type, status, progress, params_json, result_json, error, created_at, updated_at, panel_link_id)
SELECT id, type, status, progress, params_json, result_json, error, created_at, updated_at, NULL
FROM console_jobs_before_mob_x_flow;

DROP TABLE console_jobs_before_mob_x_flow;

CREATE INDEX IF NOT EXISTS idx_console_jobs_created
  ON console_jobs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_console_jobs_status
  ON console_jobs(status, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_console_jobs_mob_link_unique
  ON console_jobs(panel_link_id)
  WHERE type = 'mob_x_link' AND panel_link_id IS NOT NULL;
