PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agent_config (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  started_at TEXT,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO agent_config (id, enabled, started_at) VALUES (1, 0, NULL);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'youtube', 'instagram', 'mob_official', 'music')),
  source_key TEXT NOT NULL,
  display_name TEXT,
  enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  last_checked_at TEXT,
  last_seen_published_at TEXT,
  UNIQUE (platform, source_key)
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  source_key TEXT NOT NULL,
  external_id TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title TEXT,
  caption TEXT,
  published_at TEXT,
  discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  status TEXT NOT NULL DEFAULT 'discovered'
    CHECK (status IN ('discovered', 'filtered', 'queued', 'downloading', 'ready', 'sending', 'sent', 'failed', 'uncertain')),
  media_sha256 TEXT,
  media_bytes INTEGER,
  media_mime TEXT,
  rubika_message_id TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT,
  last_error_at TEXT,
  sent_at TEXT,
  UNIQUE (platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_items_status_discovered ON items(status, discovered_at);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(platform, source_key);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  trigger_type TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'failed', 'cancelled')),
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  finished_at TEXT,
  found_count INTEGER NOT NULL DEFAULT 0,
  eligible_count INTEGER NOT NULL DEFAULT 0,
  sent_count INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  summary TEXT
);

CREATE TABLE IF NOT EXISTS delivery_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL REFERENCES items(id),
  attempt_number INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('started', 'sent', 'failed', 'uncertain')),
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  finished_at TEXT,
  rubika_message_id TEXT,
  error_code TEXT,
  UNIQUE (item_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_delivery_attempts_item ON delivery_attempts(item_id, attempt_number);
