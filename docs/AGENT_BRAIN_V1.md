# Rubika-Agent brain — current implementation notes

This file summarizes the active main-branch architecture. Older phone-only discovery notes and provider experiments are historical; the master handoff and live workflow files take precedence.

## Two separate content tracks

### Poppy Playtime edits

- Active discovery/intake is cloud-scheduled through GitHub Actions and Termux Docker, not a daily manual command on the user's phone. The workflow `.github/workflows/termux-docker-discovery.yml` checks its D1 intake gate every 30 minutes and runs the fuller metadata collection only after five hours since the last successful queue intake.
- Metadata candidates are validated and enqueued in the D1 automation queue. The discovery workflow itself does not download video media or publish to Rubika.
- The regular edit lane supports TikTok only. General YouTube/Instagram discovery and delivery are removed. The separate mob_official lane accepts only canonical X status URLs from mobgamesstudios; other source platforms are rejected. The owner-selected TikTok creators and source-specific rules live in that policy file.
- The daily queue processor `.github/workflows/daily-queue-processor.yml` runs at 07:00 UTC and targets at most four successful sends per run. Four is a cap, not a guarantee: each item must pass source, duplicate, media, policy and delivery checks.
- Media output format is source-policy-dependent: selected profiles may use square 1080×1080, others preserve source aspect ratio. Do not assume all creators should be forced into the same aspect ratio.
- Content relevance and edit-signal checks remain mandatory. A trusted creator identity cannot make every upload relevant. AI is not allowed to auto-approve uncertain content.

### Official Mob Entertainment news

- This is a separate legacy content track. Active official-Mob discovery is X-only; inactive non-X source entries are removed from the source registry while existing `mob_official` handlers remain for compatibility.
- The X path checks the authoritative publication timestamp, author identity, canonical URL and actual attached media. For publication, the news item's verified published_at must fall inside the five-hour news-only window; GitHub commit time is not used as a proxy for publication time.
- Captions preserve source text and require a valid Persian translation when the caption is available. Outgoing captions are checked to ensure they have no URL. Actual media must be downloaded and validated before sending.
- Several eligible X posts in one five-hour window can be processed one at a time with deduplication. An ambiguous Rubika delivery stops the run to avoid duplicates.
- Production publication is deliberately blocked until the owner updates the destination secret to the real main-channel GUID and separately approves the Mob-production gate/config. The lifecycle config remains in test mode today.

## AI responsibilities

- Python owns source policy, metadata validation, deduplication, technical media validation, delivery status, and safe error guidance.
- Cloudflare D1 stores the durable metadata, queue, provider call ledger and outcomes.
- Cloudflare Workers AI is reserved exclusively for music detection from video audio using Clef Omni. Ambiguous audio decisions require human review. Workers AI is not used for code repair or official-news translation.
- Groq and OpenRouter are the active external providers for code repair. Groq proposes a constrained patch, both providers diagnose and review independently, all requests reserve a D1 slot first, and uncertain provider calls are not blindly retried.
- Official Mob translation uses Groq first and OpenRouter as a bounded fallback, with the translation role recorded in D1. The active repair/translation path is restricted to Groq and OpenRouter.
- No model may publish to Rubika, merge a PR, or bypass tests. The bounded self-repair workflow can open a review PR only after the changed-file allowlist and deterministic tests pass.

## Delivery and failure handling

- A successful Rubika response is required before a delivery is recorded as sent.
- Only definite structured rejection can be retried within the configured attempt bound.
- A transport exception or ambiguous response is recorded as uncertain and stops automatic resend.
- Temporary media is not removed before delivery status is known. Cleanup policies must fail closed when a staged copy is uncertain or needed for recovery.
- Provider outputs and source captions are untrusted data. Never allow model text or media captions to inject repository instructions, secrets or arbitrary formatting metadata.

## Verified current checks

- Latest Cloud bootstrap CI run recorded in the master handoff: 384 Python tests passed, Worker API tests passed, the Rubka API surface check returned ok, and agent.preflight self-test returned ok=true/publishing=false.
- Live provider smoke confirmed one successful ledgered request each to Groq and OpenRouter. It did not prove every possible repair will succeed, nor did it publish to Rubika.
- Read-only D1 counts last checked were 64 queued, 3 sent, 6 skipped and 1 failed; these counts are time-sensitive and should be queried again before making scheduling decisions.
- Rubika bot authentication passed a read-only get_me check, but the main-channel preflight then blocked because RUBIKA_CHANNEL_GUID still equals the known test-channel GUID. No main-channel send was made.

## What must remain gated

1. Do not change the channel secret in code. The owner must set GitHub Actions secret RUBIKA_CHANNEL_GUID to the real main-channel GUID, then rerun the no-send main-channel preflight.
2. Do not enable the Mob-production variable or move the lifecycle/source config to production until the actual main destination is confirmed and the owner approves that separate step.
3. Do not claim a four-post day is complete until the live queue run confirms successful posts. Do not claim media-caption formatting works on a live post until Rubika has actually accepted a formatted payload; current unit tests use the parser and mocked publisher.
