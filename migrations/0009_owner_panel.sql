-- Owner panel (Cloudflare Worker, stage 1). Additive only; no existing table is touched.
CREATE TABLE IF NOT EXISTS panel_owner (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  chat_id TEXT NOT NULL,
  claimed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS panel_events (
  event_key TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS panel_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS panel_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'received',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
