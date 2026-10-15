# Durable Gemini code-repair request ledger

This ledger is exclusively for the **code-repair workflow**. Gemini is currently the only model enabled for code repair. Cloudflare Workers AI's existing jobs elsewhere in Rubika-Agent (such as translation, music moderation, or operational recommendations) remain separate and are not called by this code-repair request flow.

## How it works

Before the production Gemini workflow sends a prompt to Google's Gemini API, `agent/brain/gemini_usage_client.py` reserves one request through the existing authenticated Worker API. If D1 cannot reserve it, the Gemini request is not made. When the model call returns, the client records success, failure, or an uncertain outcome. Token counts are persisted when Gemini provides them. A failure to record the final outcome blocks the proposal from continuing.

## Endpoints

All endpoints use the existing `Authorization: Bearer <AGENT_API_TOKEN>` protection for `/api/brain/*`.

- `GET /api/brain/ai-provider/status?provider=gemini`: returns today's UTC request count, daily limit, remaining count, cooldown, and whether another request may be reserved.
- `POST /api/brain/ai-provider/reserve`: accepts `provider`, `role`, `call_id`, and optional `run_id`. Roles are exactly `diagnose`, `propose_patch`, or `review_patch`. The current hard-coded Gemini budget is 5 reservations per UTC day.
- `POST /api/brain/ai-provider/finish`: accepts `provider`, `call_id`, `outcome`, and optional `error_code`, token counts, and `retry_after_seconds`. Outcomes are `succeeded`, `failed`, and `unknown`. The exact `rate_limited` and `quota_exhausted` error codes set a persisted cooldown; the default is five minutes and the maximum is 24 hours.

Unknown provider names are rejected. The accounting client sends only request identifiers, role, outcome, safe error code, and usage counters. It never sends prompts, source code, model output, credentials other than its bearer token, or raw exception details to accounting endpoints.

## Fail-closed protections

The limit is checked inside the reservation SQL statement, preventing concurrent calls from passing a stale count. Every reservation counts toward the daily limit even when the result is unknown. Reused call IDs are rejected. A reservation can be finished only once. Cooldown changes are tied to that exact finish transition. If the ledger is unavailable, the code-repair engine stops before Gemini is called or prevents the resulting proposal from continuing.

The workflow still has its conservative GitHub Actions run-count guard as a second protective limit. The durable D1 ledger enforces the actual Gemini request-reservation budget.

## Activation

The code and workflow are prepared in draft PR #12. It is not live until an owner reviews/merges the PR, applies migration `0005_ai_provider_ledger.sql` to the configured D1 database, deploys the Worker, and confirms the GitHub Actions secrets `GEMINI_API_KEY` and `AGENT_API_TOKEN` exist. Live migration and deployment are not performed by this PR.

The ledger APIs do not call `env.AI.run` or any Cloudflare AI model, and do not deploy Workers, execute model output, modify repository files, create repair PRs, or publish to Rubika. All patches remain untrusted until the existing fixed checks pass and a human reviews the resulting PR.
