# AI provider ledger — current main configuration

This document describes the current deployed arrangement. Older multi-provider rollout notes and Gemini/Mistral/GitHub Models experiments are historical only.

## Active providers and roles

The active provider allowlist is **Groq** and **OpenRouter Free**. Gemini, Mistral, and GitHub Models are not active code-repair or translation providers.

| Role | Active behavior |
|---|---|
| Diagnose CI failures | Groq and OpenRouter provide separate diagnoses from sanitized logs/read-only test context. |
| Propose a code patch | Groq proposes one constrained patch. |
| Review the patch | Groq and OpenRouter review independently. |
| Translate official Mob news | Groq is tried first; OpenRouter is the bounded fallback if Groq returns an unusable result. |
| Detect music in video audio | Cloudflare Workers AI, using `@cf/cloudflare/clef-omni`, handles this separately. |

Workers AI must **not** be used for code repair, error diagnosis, or news translation. The old Worker translation route returns `410 translation_route_retired`. The live Worker code has a single `env.AI.run(...)` call, for audio-based music moderation. Music decisions that are uncertain require human review; they are not auto-approved for publication.

## Metering and safety

Every Groq/OpenRouter repair or Mob translation request must reserve a D1 usage slot before it contacts the provider, then finish the ledger record with the result. If a reservation cannot be confirmed, the request is not sent. Ambiguous requests are not automatically retried. Safe provider error codes are retained in the ledger; raw credentials, provider response bodies and secrets must not be logged.

The current Worker policy is:

- Per-provider daily cap: **18 reservations** for Groq and **18** for OpenRouter.
- Shared daily cap: **36 reservations** across both providers.
- Allowed roles: `diagnose`, `propose_patch`, `review_patch`, and `translate_official`.
- The ledger schema role change is migration `0008_ai_provider_translation_role.sql`; the live D1 schema was read back and confirms `translate_official` is permitted.
- The repository still keeps historical providers in its older migration history for data compatibility, but the live Worker allowlist and Python clients reject retired providers.

## Automatic repair workflow

`.github/workflows/groq-openrouter-code-repair.yml` watches failures from the CI, scheduled edit-queue, cloud discovery, and live-provider-smoke workflows on `main`. It checks that the failed SHA is still the current `main`, verifies credential presence and the live D1 budget/cooldown, and applies a further safety guard for missing credentials, Rubika destination/auth failures, and provider quota or ledger errors.

If allowed to proceed, it needs two diagnoses, one Groq proposal, and independent Groq/OpenRouter review. The proposed diff is untrusted. It must pass the changed-file allowlist, compile checks, all Python unit tests, Worker tests, and the repository self-test in a disposable runner. Only then may the workflow open a review PR. It never auto-merges, deploys the Worker, changes secrets, changes D1 schema, or publishes to Rubika. It is fail-closed when it has no safe file context.

The workflow has a daily cap of three non-skipped attempts. Normal successful CI runs cause the repair workflow to be skipped; this is expected and does not mean a repair failed.

## Live verification evidence — 2026-10-16

- The live Groq/OpenRouter provider smoke workflow completed successfully in GitHub Actions run [38924738525](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38924738525): one ledgered request to Groq and one to OpenRouter returned usable responses. A metered Groq-first Persian-translation check also returned Persian text without a source URL. This proves these bounded calls worked at that time; it is not a guarantee of future free-tier availability.
- The latest repository CI after the recent provider, caption, and documentation changes is [run 38929244036](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38929244036), conclusion **success**, at commit `226bf30ee7ab02d8f0f83fa29f49ddcf476d0a2d`. Earlier run `38928819444` also passed **384 tests**, Worker API tests, the installed Rubka API surface check and `agent.preflight self-test`.
- D1 read-only queries show successful Groq/OpenRouter diagnosis calls and a successful `translate_official` call. A previous OpenRouter result was recorded as `provider_text_missing`; the adapter preserves that reason and does not blindly retry the same call.
- Production Rubika sending is **not yet cleared**. A read-only bot-authentication preflight succeeded, but the next guard correctly stopped because the repository secret `RUBIKA_CHANNEL_GUID` still equals the known test-channel GUID. No main-channel message was sent by that preflight.
- The Mob production publisher is still gated by the explicit Actions variable `RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014`, `config/agent_lifecycle.json` is still in test mode, and `config/mob_official_sources.json` remains test-channel-only. Therefore the five-hour Mob publisher is configured and guarded, but is not production-enabled.

## Credentials

The workflow uses GitHub Actions secrets `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and `AGENT_API_TOKEN`. The official Mob publisher also needs `RUBIKA_TOKEN`, `RUBIKA_CHANNEL_GUID`, and its other configured credentials. Never commit or print secret values. Do not change secrets or bypass a destination gate in code to make a test green.

## Important limits

Live provider smoke tests prove that individual model calls can work; they do not prove that every possible failure can be repaired automatically. A model can return incorrect reasoning or an unsafe proposal, a provider can be rate-limited, and test/live source conditions can vary. Deterministic tests, code-path restrictions, a human-reviewed PR and a separate main-channel preflight remain mandatory.