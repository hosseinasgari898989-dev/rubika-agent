CREATE TABLE IF NOT EXISTS owner_console_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  offset_id TEXT CHECK (offset_id IS NULL OR length(offset_id) BETWEEN 1 AND 200),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO owner_console_state (id, offset_id) VALUES (1, NULL);

CREATE TABLE IF NOT EXISTS owner_console_updates (
  update_id TEXT PRIMARY KEY CHECK (length(update_id) = 64),
  chat_hash TEXT NOT NULL CHECK (length(chat_hash) = 64),
  command TEXT NOT NULL CHECK (length(command) BETWEEN 1 AND 80),
  status TEXT NOT NULL CHECK (status IN ('claimed', 'answered', 'failed', 'uncertain', 'ignored')),
  result_code TEXT CHECK (result_code IS NULL OR length(result_code) BETWEEN 1 AND 80),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_owner_console_updates_created
  ON owner_console_updates(created_at);
CREATE INDEX IF NOT EXISTS idx_owner_console_updates_status_created
  ON owner_console_updates(status, created_at);
