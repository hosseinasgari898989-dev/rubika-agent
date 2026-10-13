CREATE TABLE IF NOT EXISTS ai_usage_daily (
  usage_day TEXT PRIMARY KEY CHECK (length(usage_day) = 10),
  request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_daily_updated ON ai_usage_daily(updated_at);
