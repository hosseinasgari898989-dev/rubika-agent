ALTER TABLE items ADD COLUMN media_type TEXT NOT NULL DEFAULT 'video'
  CHECK (media_type IN ('video', 'image', 'audio', 'carousel', 'unknown'));
ALTER TABLE items ADD COLUMN source_tier TEXT NOT NULL DEFAULT 'unknown'
  CHECK (source_tier IN ('unknown', 'trusted', 'official'));
ALTER TABLE items ADD COLUMN storage_key TEXT;
ALTER TABLE items ADD COLUMN validation_json TEXT;
ALTER TABLE items ADD COLUMN moderation_json TEXT;
ALTER TABLE items ADD COLUMN review_state TEXT NOT NULL DEFAULT 'pending'
  CHECK (review_state IN ('pending', 'approved', 'rejected', 'needs_review'));
ALTER TABLE items ADD COLUMN original_caption TEXT;
ALTER TABLE items ADD COLUMN persian_translation TEXT;

CREATE INDEX IF NOT EXISTS idx_items_review_state ON items(review_state, status);
CREATE INDEX IF NOT EXISTS idx_items_media_sha256 ON items(media_sha256);

CREATE TABLE IF NOT EXISTS brain_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  item_id TEXT,
  level TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('debug', 'info', 'warning', 'error')),
  code TEXT NOT NULL,
  details_json TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_brain_events_run ON brain_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_brain_events_item ON brain_events(item_id, created_at);
