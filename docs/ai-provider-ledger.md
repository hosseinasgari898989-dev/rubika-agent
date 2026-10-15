# Multi-provider code-repair request ledger

The durable ledger is used only by the **code-repair workflow**. The intended three-role pipeline is:
1. **Groq — diagnose:** summarize likely causes from sanitized CI logs and read-only test context.
2. **Gemini — propose_patch:** use the existing strict repair prompt and allowlisted unified-diff validator.
3. **OpenRouter free-model router — review_patch:** routes an independent review to an available zero-price model before deterministic checks run.

The roles must use three distinct providers. A reviewer cannot edit code, run tools, authorize deployment, bypass fixed tests, merge a pull request, or publish to Rubika. Cloudflare Workers AI remains separate for its existing unrelated jobs; the code-repair provider module never calls `env.AI.run`.

## Ledger and budget

All requests must reserve a slot through the authenticated Worker API before contacting a provider, then record the final outcome. The server enforces both a provider-specific limit of **3 reservations per provider per UTC day** and a shared total of **9 reservations per UTC day**. This allows at most three full diagnose/propose/review cycles per day. A reservation counts even when the result is unknown. Requests are never automatically retried.

- `GET /api/brain/ai-provider/status?provider=gemini` (also `groq` or `openrouter`): reports provider usage, shared usage, remaining slots, cooldown, and readiness.
- `POST /api/brain/ai-provider/reserve`: accepts `provider`, role (`diagnose`, `propose_patch`, or `review_patch`), a unique `call_id`, and optional `run_id`.
- `POST /api/brain/ai-provider/finish`: records `succeeded`, `failed`, or `unknown`, plus optional safe error code and token counts.

Every endpoint is behind the existing `Authorization: Bearer <AGENT_API_TOKEN>` check. Provider names outside the explicit allowlist are rejected. The reservation SQL checks the provider and shared budgets in one conditional insert, preventing concurrent requests from passing a stale count. Reused call IDs are rejected and each reservation can be finished only once. A rate-limited response records a persistent five-minute cooldown by default (up to 24 hours).

The accounting client sends only identifiers, role, outcome, safe error code, and token counters. It does not send prompts, source code, model output, credentials other than its ledger bearer token, or raw provider exceptions to the accounting endpoints.

## API configuration

The multi-AI workflow reads credentials from GitHub Actions secrets; keys must never be committed to the repository or pasted into chat. Required secrets:
- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `OPENROUTER_API_KEY`
- `AGENT_API_TOKEN` (existing ledger authorization secret)

The selected role/model environment defaults are `GROQ_MODEL=openai/gpt-oss-20b`, `GEMINI_MODEL=gemini-3.8-flash`, and `OPENROUTER_MODEL=openrouter/free`. OpenRouter's `openrouter/free` router selects from zero-price models; the available model can vary between calls, and free capacity and request limits apply. Groq and Gemini also have service-side free-tier limits. The code validates that all three provider credentials exist before reserving or sending any request, so a missing key cannot cause a partial three-role run.

## Fail-closed protections

- No model request occurs if its ledger reservation cannot be confirmed.
- If writing the result to the ledger fails, the result is blocked from progressing.
- Uncertain network outcomes consume their reservation and are not retried automatically.
- Any provider failure, invalid proposal JSON, unsafe diff, or reviewer veto stops promotion.
- Proposed patches remain untrusted; the existing fixed checks run in a disposable runner and a human must review the opened PR.
- Publishing to Rubika remains disabled.

The implementation is introduced in a separate draft PR. Until its review, merge, Worker version rollout and secrets setup are complete, the new three-provider workflow has not made any live model request.

## Model identifier maintenance

The default model IDs in the workflow were checked against provider documentation on 2026-10-15:
- Gemini: `gemini-3.8-flash` — https://ai.google.dev/gemini-api/docs/models
- Groq: `openai/gpt-oss-20b` — https://console.groq.com/docs/models. The previous `llama-3.3-70b-versatile` model was marked deprecated for free/developer tiers with an August 16, 2026 shutdown date: https://console.groq.com/docs/deprecations
- OpenRouter: `openrouter/free` — routes to currently available zero-price models: https://openrouter.ai/openrouter/free/. OpenRouter currently documents a free-model limit of 50 requests per day and 20 per minute; available capacity can vary: https://openrouter.ai/pricing/

These values are configurable, not guaranteed permanent. Re-check provider/model availability before the first live request; never use that check as permission to create or spend on an API account automatically.
