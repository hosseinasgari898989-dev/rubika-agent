# AI provider ledger — Gemini-only proposal and deployment boundary

This document describes the proposed Gemini-only branch. **The currently deployed Worker has not been updated by this PR.** The live Worker/D1 provider allowlist still needs an approved Worker deployment before the new Gemini workflow can make a model call. The new workflows check this and fail closed.

## Intended active roles after review and deployment

| Role | Proposed behavior |
|---|---|
| Error diagnosis | One Gemini request through the D1 ledger. Return structured evidence, likely causes, unknowns, and read-only next checks. |
| Official Mob news translation | One Gemini request through the D1 ledger; validate that the result is real Persian text and remove URLs from the outgoing caption. |
| Code patching | Disabled. Gemini may not propose or apply patches, write files, open repair PRs, merge, deploy or publish. |
| Music detection | Cloudflare Workers AI using `@cf/cloudflare/clef-omni` on extracted video audio only. Uncertain results require human review. |

Groq, OpenRouter, Mistral and GitHub Models are removed from the proposed active client/workflow path. Historical D1 rows and migration history are retained for audit compatibility; this does not reactivate those providers.

## Ledger and safety design

Every Gemini call must reserve an entry through the authenticated Worker API before contacting the provider, then record the result. A failed or uncertain reservation blocks the request. There are no automatic API retries. Provider HTTP status/reason codes must be retained safely in the ledger, even if the outcome is unknown. Raw provider response bodies, prompts containing sensitive data, credentials and secrets must not be printed.

The proposed Worker limits are:

- Gemini per-provider daily cap: 12 reservations.
- Shared daily cap: 12 reservations across diagnosis and translation.
- Allowed roles: `diagnose` and `translate_official` only.
- The Python client independently enforces the same provider/role allowlist.
- Historical database provider names are preserved; live route allowlisting is enforced by Worker code.

The live Worker currently has the legacy Groq/OpenRouter allowlist. The new workflow checks the status route at `/api/brain/ai-provider/status?provider=gemini` and will not request Gemini unless the deployed Worker confirms the provider is ready.

## Gemini advisory diagnosis

`.github/workflows/gemini-error-diagnosis.yml` is bounded to two workflow attempts per UTC day and requires both repository Actions variables:

- `RUBIKA_AGENT_AI_FREE_TIER_APPROVED=true`
- `RUBIKA_AGENT_GEMINI_ONLY_WORKER_READY=true`

The workflow also verifies secret presence and performs a read-only D1-ledger status check. It supplies sanitized historical facts and, when available, sanitized logs from a failed scheduled/CI workflow. The model output is schema-checked and redacted before it is summarized or stored as an artifact. A Gemini HTTP 503/UNAVAILABLE result remains a failed diagnosis, not a reason to retry or to invent a root cause.

The historical evidence includes one Gemini run with HTTP 503/UNAVAILABLE and safe error code `provider_http_503`; other historical runs returned zero accessible jobs/logs; several old D1 records had the generic `provider_result_unknown` code. Those facts do **not** establish the cause of all earlier failures.

The previous Groq/OpenRouter code-repair workflow and multi-provider live smoke test are now inert manual-only workflows. They read no provider secrets and make no model requests.

## Official Mob translation and five-hour test automation

The proposed Mob translation adapter uses Gemini only and requires a D1-ledgered response that passes Persian-language validation. It does not fall back to Groq, OpenRouter, or an unmetered public translation service.

`.github/workflows/mob-x-news-test-autopilot.yml` runs every five hours, but it is hard-locked to the test Rubika channel and gated by:

- `RUBIKA_AGENT_MOB_X_TEST_AUTOPILOT_APPROVED=true`
- `RUBIKA_AGENT_AI_FREE_TIER_APPROVED=true`
- `RUBIKA_AGENT_GEMINI_ONLY_WORKER_READY=true`

The news freshness window is based only on a verified X post `published_at` timestamp, not GitHub commit/push time. The flow checks source author, actual media, caption/translation validity and duplicate history. It handles at most 12 candidate posts per sweep, sending one at a time and stopping on any uncertain delivery. It uses the existing five-hour news window and does not change edit-queue lifecycle/birth-cutoff behavior.

The production Mob workflow remains separately gated. The repository lifecycle must stay `mode=test` and the source publishing policy must remain test-channel-only until the owner separately approves production.

## Verified evidence and outstanding boundary

- Main CI before this PR passed 384 Python tests, Worker API tests, the Rubka API surface check and `agent.preflight self-test`.
- The prior live Groq/OpenRouter smoke test proved individual requests worked at that time. This branch retires them from future active calls; it does not rewrite history or imply Gemini has already passed a live request in the deployed Worker.
- No Worker deployment, D1 migration/write, secret change, Gemini API request or Rubika send is performed by this branch commit.
- The repository Actions gates default to unset/false in a new environment, so the new diagnosis and Mob test-autopilot jobs do not make requests until approval and Worker readiness are explicitly configured.
- A green CI run only validates the code and tests. It does not prove that the deployed Worker is updated, that Gemini API access is on a free tier, or that any live Rubika post was sent.

Secrets used after explicit approval are GitHub Actions secrets, never source code. Do not bypass the gates or use an unmetered model request to test around a stale Worker.