-- Allow the same durable Groq/OpenRouter ledger to account for official-news
-- translation requests. Rebuild only the calls table to widen the checked role enum;
-- preserve every historical row and recreate indexes that belonged to the old table.
CREATE TABLE ai_provider_calls_new (
  call_id TEXT PRIMARY KEY CHECK (length(call_id) BETWEEN 16 AND 100),
  usage_day TEXT NOT NULL CHECK (length(usage_day) = 10),
  provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 40),
  role TEXT NOT NULL CHECK (role IN ('diagnose', 'propose_patch', 'review_patch', 'translate_official')),
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

INSERT INTO ai_provider_calls_new (
  call_id, usage_day, provider, role, run_id, status, error_code,
  prompt_tokens, output_tokens, total_tokens, cooldown_until, finish_marker,
  started_at, finished_at
)
SELECT
  call_id, usage_day, provider, role, run_id, status, error_code,
  prompt_tokens, output_tokens, total_tokens, cooldown_until, finish_marker,
  started_at, finished_at
FROM ai_provider_calls;

DROP TABLE ai_provider_calls;
ALTER TABLE ai_provider_calls_new RENAME TO ai_provider_calls;

CREATE INDEX idx_ai_provider_calls_usage_day_provider
  ON ai_provider_calls(provider, usage_day, started_at);

CREATE INDEX idx_ai_provider_calls_status
  ON ai_provider_calls(provider, status, started_at);
