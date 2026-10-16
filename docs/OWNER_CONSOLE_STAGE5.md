# Stage 5 — Rubika owner read-only operations console

## Goal

This implements the first operations-console roadmap slice: read-only status/report commands in the owner's private Rubika chat. It remains separate from the multi-AI code-repair coordinator and all content-publishing workflows.

## Commands

- `/help` — available commands and safety boundaries.
- `/status` — Worker/database health, agent config, item counts, latest recorded run.
- `/queue` — queue counts and items sent today, using a new SELECT-only Worker endpoint.
- `/ai-status` — current ledger budget/cooldown/readiness for the four allowlisted providers.
- `/ai-status gemini`, `/ai-status groq`, `/ai-status openrouter`, `/ai-status github_models` — one provider.
- `/report` — combine status, queue, provider ledger and recent GitHub Actions run summaries.

Unsupported text/commands only receive a short help prompt. They are never interpreted as shell commands and are not sent to any AI model.

## Security and audit

- A message is accepted only if it is a private `NewMessage` matching both configured `RUBIKA_OWNER_CHAT_ID` and `RUBIKA_OWNER_USER_ID`. Group/channel, other-user, edited and malformed updates are ignored.
- Worker endpoints are authenticated under `/api/brain/owner-console/`. Inputs are restricted to fixed command names, SHA-256 update/chat identifiers and allowlisted outcomes.
- D1 stores an opaque updates cursor and a minimal audit row (hashed chat identity, normalized command, status and safe result code), not message bodies or raw owner IDs.
- Duplicate updates are claimed once; uncertain message-delivery results are not automatically retried. The Worker limits claimed commands to 80 per UTC day.
- Queue reporting uses a new SELECT-only route, not the existing queue GET handler that also changes stale `processing` items.
- No shell execution, model inference, queue mutation, code edit, PR creation/merge, deployment, database mutation of project rows, or channel posting is available from chat.

## Scheduling and required configuration

The GitHub Actions workflow polls every five minutes (actual start may be delayed by GitHub). It triggers only on schedule or manual dispatch, not on pushes or pull requests. It requires the following Actions secrets: `RUBIKA_TOKEN`, `RUBIKA_OWNER_CHAT_ID`, `RUBIKA_OWNER_USER_ID`, and `AGENT_API_TOKEN`. Missing secrets stop the workflow and only the missing secret name is logged.

## Migrations and activation

- `0005_ai_provider_ledger.sql` is existing schema.
- PR #24 was corrected to use `0006_owner_daily_digest.sql`.
- Stage 5 adds `0007_owner_console.sql`.
- Tests use local SQLite and fake Worker/Rubika clients only.
- No remote D1 migration, Worker deployment, live Rubika request, private message, model-provider request or publishing action has been performed.
- The Stage 5 branch is stacked on the PR #24 branch. If approved later, review/merge PR #24 first and Stage 5 second; apply the ordered pending migrations and deploy only through separately approved steps.

## Current checkpoint

Branch: `feature/rubika-readonly-ops-console-stage5`

Implemented in source, pending CI/runtime verification: allowlisted read-only commands, private sender gate, durable cursor, idempotent claims/audit, read-only queue summary, sanitized provider and GitHub Actions status summaries.

Not live/verified: actual bot IDs/secrets, live Worker routes, remote migration 0007, end-to-end private replies.