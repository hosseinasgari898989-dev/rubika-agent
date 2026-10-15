# Multi-provider code-repair request ledger

The durable ledger is used only by the **code-repair workflow**. The default four-provider ensemble is:
1. **Groq + Mistral + OpenRouter Free — diagnose in parallel:** collect independent views of sanitized CI logs and read-only test context.
2. **Gemini — propose:** produce one constrained patch using the existing strict prompt and allowlisted unified-diff validator.
3. **Mistral + OpenRouter Free — review independently:** both reviewers must approve; a veto, malformed response, or reviewer outage stops promotion.

This is a cooperative multi-model pipeline, not a single giant model. Each response is untrusted evidence; no model can execute commands or tools. A reviewer cannot authorize deployment, bypass fixed tests, merge a pull request, or publish to Rubika. Cloudflare Workers AI remains separate for its existing unrelated jobs; the code-repair provider module never calls `env.AI.run`.

## Ledger and budget

All requests must reserve a slot through the authenticated Worker API before contacting a provider, then record the final outcome. The server enforces both a provider-specific limit of **6 reservations per provider per UTC day** and a shared total of **18 reservations per UTC day**. A complete ensemble normally uses six calls (three parallel diagnoses, one proposal, and two reviews), leaving a conservative maximum of three complete repair cycles per day. A reservation counts even when the result is unknown. Requests are never automatically retried.

- `GET /api/brain/ai-provider/status?provider=gemini` (also `groq`, `mistral`, or `openrouter`): reports provider usage, shared usage, remaining slots, cooldown, and readiness.
- `POST /api/brain/ai-provider/reserve`: accepts `provider`, role (`diagnose`, `propose_patch`, or `review_patch`), a unique `call_id`, and optional `run_id`.
- `POST /api/brain/ai-provider/finish`: records `succeeded`, `failed`, or `unknown`, plus optional safe error code and token counts.

Every endpoint is behind the existing `Authorization: Bearer <AGENT_API_TOKEN>` check. Provider names outside the explicit allowlist are rejected. The reservation SQL checks the provider and shared budgets in one conditional insert, preventing concurrent requests from passing a stale count. Reused call IDs are rejected and each reservation can be finished only once. A rate-limited response records a persistent five-minute cooldown by default (up to 24 hours).

The accounting client sends only identifiers, role, outcome, safe error code, and token counters. It does not send prompts, source code, model output, credentials other than its ledger bearer token, or raw provider exceptions to the accounting endpoints.

## API configuration

The multi-AI workflow reads credentials from GitHub Actions secrets; keys must never be committed to the repository or pasted into chat. Required secrets:
- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `MISTRAL_API_KEY` (Mistral Studio must remain in its no-card Free mode)
- `OPENROUTER_API_KEY`
- `AGENT_API_TOKEN` (existing ledger authorization secret)

The selected role/model environment defaults are `GROQ_MODEL=openai/gpt-oss-20b`, `GEMINI_MODEL=gemini-3.8-flash`, `MISTRAL_MODEL=mistral-small-latest`, and `OPENROUTER_MODEL=openrouter/free`. OpenRouter's `openrouter/free` router selects from available zero-price models; the model can vary between calls. Groq, Gemini, and Mistral have their own service-side limits. Mistral documents a no-card Free mode with limited usage: https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key. Configure Mistral in that free mode only. The code validates all four provider credentials before any model request, so a missing key cannot cause a partial run.

## Fail-closed protections

- No model request occurs if its ledger reservation cannot be confirmed.
- If writing the result to the ledger fails, the result is blocked from progressing.
- Uncertain network outcomes consume their reservation and are not retried automatically.
- Any provider failure, invalid proposal JSON, unsafe diff, or reviewer veto stops promotion.
- Proposed patches remain untrusted; the existing fixed checks run in a disposable runner and a human must review the opened PR.
- Publishing to Rubika remains disabled.

## Current rollout checkpoint — 2026-10-15

- PR #13 was merged into `main` as commit `042a0ecb2fea0c9ebe4a247388ff2951b7fecb92`.
- Production Worker `rubika-agent` version 62 was deployed at 100% traffic from that commit. The existing `AGENT_API_TOKEN`, `GEMINI_API_KEY`, `AI`, and `DB` bindings were preserved.
- The public `/health` smoke test returned `ok: true`, `database: connected`, and `publishing: false`.
- Deployment and smoke testing did not call any external model API. The repair workflow only proceeds after a failed `Cloud bootstrap checks` run caused by a push to `main`, and only after its freshness, duplicate-PR, and daily-budget checks pass.
- Required GitHub Actions secret names remain `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`, and `AGENT_API_TOKEN`. Secret values are never stored in this repository or exposed in this documentation. This connector cannot independently inspect their registration; missing credentials cause the repair pipeline to stop safely before model calls.
- The first real multi-provider repair attempt has not yet been exercised end-to-end. Worker health is confirmed, but live provider credentials, quotas, and successful external inference remain unverified until an eligible repair run occurs.

## Model identifier maintenance

The default model IDs in the workflow were checked against provider documentation on 2026-10-15:
- Gemini: `gemini-3.8-flash` — https://ai.google.dev/gemini-api/docs/models
- Groq: `openai/gpt-oss-20b` — https://console.groq.com/docs/models. The previous `llama-3.3-70b-versatile` model was marked deprecated for free/developer tiers with an August 16, 2026 shutdown date: https://console.groq.com/docs/deprecations
- Mistral: `mistral-small-latest` via the no-card Free mode, with limited usage: https://docs.mistral.ai/api/endpoint/chat and https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key
- OpenRouter: `openrouter/free` — routes to currently available zero-price models: https://openrouter.ai/openrouter/free/. Free-model limits depend on account eligibility and current limits: https://openrouter.ai/pricing/

These values are configurable, not guaranteed permanent. Re-check provider/model availability before the first live request; never use that check as permission to create or spend on an API account automatically.
