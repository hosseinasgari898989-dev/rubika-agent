CREATE TABLE IF NOT EXISTS automation_queue (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'youtube', 'instagram')),
  external_id TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  source_key TEXT NOT NULL,
  title TEXT,
  caption TEXT,
  published_at TEXT,
  media_type TEXT NOT NULL DEFAULT 'video'
    CHECK (media_type IN ('video', 'image', 'audio', 'carousel', 'unknown')),
  freshness_verified INTEGER NOT NULL DEFAULT 0 CHECK (freshness_verified IN (0, 1)),
  method TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'sent', 'failed', 'uncertain', 'skipped')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  last_error_code TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  sent_at TEXT,
  UNIQUE (platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_automation_queue_status_created
  ON automation_queue(status, created_at);

CREATE INDEX IF NOT EXISTS idx_automation_queue_platform_status
  ON automation_queue(platform, status, created_at);
