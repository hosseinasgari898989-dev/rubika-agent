CREATE TABLE IF NOT EXISTS owner_digest_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  window_id TEXT NOT NULL,
  window_started_at TEXT NOT NULL,
  window_ends_at TEXT NOT NULL,
  window_started_epoch INTEGER NOT NULL,
  window_ends_epoch INTEGER NOT NULL,
  message_id TEXT,
  message_body TEXT,
  message_creation_uncertain INTEGER NOT NULL DEFAULT 0
    CHECK (message_creation_uncertain IN (0, 1)),
  edit_lock_token TEXT,
  edit_lock_until_epoch INTEGER,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS owner_digest_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  window_id TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('info', 'success', 'error', 'ai_success', 'ai_error')),
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT,
  occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_owner_digest_events_window_id
  ON owner_digest_events(window_id, id);
CREATE INDEX IF NOT EXISTS idx_owner_digest_events_occurred
  ON owner_digest_events(occurred_at);
