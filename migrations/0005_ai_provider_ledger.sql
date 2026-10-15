-- Durable accounting for explicitly enabled code-repair AI provider requests.
-- The active ensemble uses Groq, OpenRouter, and GitHub Models.
-- Gemini remains enabled for its separately dispatched diagnostic smoke test;
-- Mistral is disabled. Cloudflare Workers AI remains for media/music tasks.
-- Reservations count even if the result is unknown.
CREATE TABLE IF NOT EXISTS ai_provider_calls (
  call_id TEXT PRIMARY KEY CHECK (length(call_id) BETWEEN 16 AND 100),
  usage_day TEXT NOT NULL CHECK (length(usage_day) = 10),
  provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 40),
  role TEXT NOT NULL CHECK (role IN ('diagnose', 'propose_patch', 'review_patch')),
  run_id TEXT,
  status TEXT NOT NULL DEFAULT 'reserved'
    CHECK (status IN ('reserved', 'succeeded', 'failed', 'unknown')),
  error_code TEXT,
  prompt_tokens INTEGER CHECK (prompt_tokens IS NULL OR prompt_tokens BETWEEN 0 AND 10000000),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens BETWEEN 0 AND 10000000),
  total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens BETWEEN 0 AND 10000000),
  cooldown_until TEXT,
  finish_marker TEXT,
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_provider_calls_usage_day_provider
  ON ai_provider_calls(provider, usage_day, started_at);

CREATE INDEX IF NOT EXISTS idx_ai_provider_calls_status
  ON ai_provider_calls(provider, status, started_at);

CREATE TABLE IF NOT EXISTS ai_provider_state (
  provider TEXT PRIMARY KEY CHECK (length(provider) BETWEEN 1 AND 40),
  cooldown_until TEXT NOT NULL,
  last_error_code TEXT NOT NULL CHECK (last_error_code IN ('rate_limited', 'quota_exhausted')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
