# Durable code-repair AI provider ledger

This feature is separate from the Workers AI features used for translation, media moderation, and operational recommendations. For the **code-repair engine**, Gemini is the only enabled provider today.

## Why this exists

The Gemini repair workflow already has a conservative GitHub Actions trigger budget, but workflow-run counting is not the same as counting actual provider requests and it is not a durable shared provider ledger. This Worker-side ledger records every reserved code-repair request in D1, including requests whose final result is unknown. A reservation consumes budget before a model request is made, and duplicate call IDs are rejected.

## Authenticated endpoints

All endpoints use the existing `Authorization: Bearer <AGENT_API_TOKEN>` protection for `/api/brain/*`.

- `GET /api/brain/ai-provider/status?provider=gemini`: returns today's UTC request count, daily limit, remaining count, cooldown, and whether another request may be reserved.
- `POST /api/brain/ai-provider/reserve`: body keys are `provider`, `role`, `call_id`, and optional `run_id`. Roles are exactly `diagnose`, `propose_patch`, or `review_patch`. The current hard-coded Gemini budget is 5 reservations per UTC day.
- `POST /api/brain/ai-provider/finish`: body keys are `provider`, `call_id`, `outcome`, and optional `error_code`, token counts, and `retry_after_seconds`. Outcomes are `succeeded`, `failed`, and `unknown`. The exact error codes `rate_limited` and `quota_exhausted` set a persisted cooldown; the default is 300 seconds and any explicit value must be between 1 second and 24 hours.

Provider names not explicitly enabled by the Worker are rejected. Do not send prompts, source code, model outputs, credentials, or raw exception messages to these accounting endpoints. The ledger stores only identifiers, role, status, safe error code, timestamps, cooldown, and bounded token counts.

## Fail-closed behavior

The request is not allowed if the D1 ledger cannot reserve it. The daily limit is checked inside the reservation SQL statement, so concurrent reservations cannot all pass using a stale count. Every reservation counts toward the daily limit even if the provider call later times out or the response is unknown. A call can transition out of `reserved` only once. Cooldown writes are tied to that exact finish transition.

The ledger does not itself call Gemini or other providers and does not modify code, open pull requests, deploy the Worker, run migrations, or publish to Rubika.

## Activation steps still required

This PR only prepares code and schema. It is not live until an owner reviews/merges the PR, applies migration `0005_ai_provider_ledger.sql` to the configured D1 database, and deploys the Worker. The GitHub Actions Gemini workflow must then be separately integrated with reserve/finish calls and the existing `AGENT_API_TOKEN` must be configured as a repository secret. No live migration, deployment, or secret operation was performed by this PR.
