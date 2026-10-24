-- Owner console phase 2. Additive only; do not apply to production D1 without owner approval.
CREATE TABLE IF NOT EXISTS console_owners (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT NOT NULL UNIQUE CHECK (length(chat_id) BETWEEN 1 AND 128),
  label TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('owner', 'viewer')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  claimed_at TEXT,
  last_seen_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Preserve the existing singleton owner as the initial console owner.
INSERT OR IGNORE INTO console_owners (id, chat_id, label, role, enabled, claimed_at)
SELECT id, chat_id, 'مالک اصلی', 'owner', 1, claimed_at FROM panel_owner;

CREATE INDEX IF NOT EXISTS idx_console_owners_enabled_role
  ON console_owners(enabled, role, id);

CREATE TABLE IF NOT EXISTS console_owner_invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  expires_at TEXT NOT NULL,
  used_at TEXT,
  used_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_console_owner_invites_expiry
  ON console_owner_invites(expires_at, used_at);

CREATE TABLE IF NOT EXISTS console_rules (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  rules_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO console_rules (id, version, rules_json)
VALUES (1, 1, '{"publishing_enabled":false,"test_channel_only":true,"daily_success_cap":4,"daily_edit_count":6,"mob_posts_per_run":1,"mob_since_cutoff":"","freshness_hours":24,"ai_provider_order":["groq","openrouter"],"ai_daily_limit_groq":200,"ai_daily_limit_openrouter":50,"translate_enabled":true,"quiet_hours":{"start":"00:30","end":"07:00"},"caption_max_chars":900,"digest_window_utc":"07:05"}');

CREATE TABLE IF NOT EXISTS console_channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  rubika_channel_id TEXT NOT NULL UNIQUE CHECK (length(rubika_channel_id) BETWEEN 3 AND 40),
  role TEXT NOT NULL CHECK (role IN ('test', 'production')),
  enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS console_jobs (
  id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 8 AND 100),
  type TEXT NOT NULL CHECK (type IN ('queue_intake_dry_run', 'ai_health_check', 'log_zip', 'd1_backup', 'mob_x_link', 'requeue_stuck')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
  progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  params_json TEXT,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_console_jobs_created
  ON console_jobs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_console_jobs_status
  ON console_jobs(status, created_at DESC);
