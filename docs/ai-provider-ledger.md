# AI provider ledger — active configuration

This document describes the active Python repair/translation path and its accounting rules.

## Active providers and roles

The active provider allowlist contains **Groq** and **OpenRouter Free** only.

| Role | Active behavior |
|---|---|
| Diagnose CI failures | Groq and OpenRouter provide independent diagnoses from sanitized logs and read-only test context. |
| Propose a code patch | Groq proposes one constrained patch. |
| Review a patch | Groq and OpenRouter review independently. |
| Translate official Mob news | Groq is tried first; OpenRouter is the bounded fallback if needed. |
| Detect music in video audio | Cloudflare Workers AI, using `@cf/cloudflare/clef-omni`, handles this separate task. |

Workers AI is not used for code repair, error diagnosis or news translation. The existing Worker translation route returns `410 translation_route_retired`; the private-chat Worker AI and music moderation logic are outside this provider cleanup.

## Metering and safety

Every Python repair or official-news translation request reserves a D1 usage slot before contacting a provider and records its outcome afterward. If reservation cannot be confirmed, the model request is not sent. Ambiguous requests are not automatically retried. Raw credentials, provider response bodies and secrets must not be logged.

The current Worker policy is:

- Daily cap: **18 reservations per provider**, with a **36-reservation combined cap**.
- Allowed roles: `diagnose`, `propose_patch`, `review_patch` and `translate_official`.
- The role extension is represented by migration `0008_ai_provider_translation_role.sql`; this cleanup does not run migrations or write to D1.
- Historical provider identifiers in older schema files are retained for database compatibility. The active Python client accepts only the two providers listed above.

## Bounded repair workflow

The workflow is `.github/workflows/groq-openrouter-code-repair.yml`. It listens for completed runs from the main CI, scheduled queue processor, cloud discovery, and live-provider smoke workflows.

It starts its repair job only when an eligible upstream run failed on `main`. A successful upstream run results in a skipped repair job; that is expected behavior, not proof that the repair engine is dead.

Before any model call, the workflow checks that the failed SHA is still current `main`, verifies the required credentials, checks both providers' live D1 budget/cooldown status, and blocks known credential, destination, quota and ledger failures. If those guards pass, it requests two diagnoses, one Groq proposal, and independent reviews from both providers. The patch is untrusted and must pass the changed-file allowlist, compile checks, Python tests, Worker tests and repository self-test before the workflow can open a review PR. It does not auto-merge, deploy the Worker, migrate D1 or publish to Rubika.

A daily cap limits the workflow to three non-skipped repair attempts. The workflow requires GitHub Actions secrets `AGENT_API_TOKEN`, `GROQ_API_KEY` and `OPENROUTER_API_KEY`; it uses GitHub's built-in `GITHUB_TOKEN` permission for creating a proposed branch/PR after validation.

## Live-provider evidence and limits

A historical live smoke run, [38924738525](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38924738525), recorded one successful ledgered call to each active provider. That evidence proves those calls worked at that time only; it is not a current availability check or a promise of free-tier capacity.

The workflow `.github/workflows/multi-ai-live-smoke.yml` remains available by manual dispatch only. It sends real provider requests and records usage in D1; this health check is separate from that workflow and does not dispatch it.

## Manual, read-only AI health check

Use GitHub Actions → **Manual AI provider health check** → **Run workflow**. The workflow is `.github/workflows/ai-health-check.yml` and has only a `workflow_dispatch` trigger—no push or schedule trigger.

It sends one short request each to Groq and OpenRouter through the existing `provider_config` and `invoke_provider` clients, initially with `max_output_tokens=10`. Providers are checked independently. The same compact table is printed to the job log and Job Summary: result category, latency, HTTP status, exception class, sanitized provider error type/code, whether assistant content was non-empty, and `finish_reason`. Categories are `ok`, `auth error`, `rate limit`, `empty response`, or `other error`. Only an HTTP-200 empty response triggers one bounded follow-up request with `max_output_tokens=64`. Credentials, request bodies, provider response text, and error message text are never printed.

This check deliberately bypasses `call_provider_with_ledger`: it performs no D1 ledger reads/writes and does not invoke Rubika, create repair PRs, or deploy anything. It does consume one request against each provider's own API quota; run it manually when you need a fresh check. A successful result proves only that the small request worked at that time.

## Credentials

The repair workflow uses `AGENT_API_TOKEN`, `GROQ_API_KEY` and `OPENROUTER_API_KEY`, plus GitHub's built-in token permission to open a review PR. Never commit or print secret values. Do not alter secrets or bypass destination gates to make a check pass.

## Important limits

Successful past calls do not prove that every failure can be repaired. A provider can be rate-limited, models can return incorrect output, and current quotas can change. Deterministic tests, constrained file paths, a human-reviewed PR and separate deployment/publication authorization remain mandatory.
