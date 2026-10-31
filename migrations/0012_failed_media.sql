-- Originals that could not be sent to Rubika (size or any other error), kept untouched in Mega failed/.
-- Idempotent: the table was already created on D1 by the owner; IF NOT EXISTS keeps re-apply a no-op.
CREATE TABLE IF NOT EXISTS failed_media (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  job_id TEXT,
  item_id TEXT,
  media_type TEXT NOT NULL DEFAULT 'video',
  storage_key TEXT NOT NULL,
  share_url TEXT,
  caption TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL,
  size_bytes INTEGER,
  status TEXT NOT NULL DEFAULT 'stored',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_failed_media_storage_key ON failed_media(storage_key);
CREATE INDEX IF NOT EXISTS idx_failed_media_status_created ON failed_media(status, created_at);
