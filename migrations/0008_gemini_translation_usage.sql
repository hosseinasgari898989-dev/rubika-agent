-- Hard cap on Gemini official-news translation calls, separate from Workers AI music telemetry.
-- Each reservation is committed before a provider request and is never refunded on an ambiguous result.
CREATE TABLE IF NOT EXISTS gemini_translation_usage_daily (
  usage_day TEXT PRIMARY KEY CHECK (length(usage_day) = 10),
  request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count BETWEEN 0 AND 10),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
