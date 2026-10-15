# Rubika-Agent resilient four-item intake and bounded AI supervisor plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Collect metadata every scheduled window and enqueue up to four genuinely new Poppy Playtime edit URLs fairly across creators, while a low-cost AI helper provides diagnostic recommendations without executing actions.

**Architecture:** Keep the existing Termux Docker collector metadata-only. Validate timestamps/relevance and inspect the persistent D1 queue and delivery-blocking index before selecting candidates; rotate creators round-robin and enqueue one item at a time until four new rows are confirmed or the candidate pool is exhausted. Add a guarded Cloudflare Workers AI endpoint that accepts sanitized error enums/counters only, atomically reserves no more than four supervisor calls per UTC day in D1, records its decision in brain_events, and can recommend only from a fixed allowlist. AI has no ability to publish, download, delete, change configuration, or execute commands.

**Tech Stack:** GitHub Actions, Python 3.12, real Termux Docker, Cloudflare Worker JavaScript, D1, Workers AI.

**Spec:** User request in the 2026-10-15 Rubika-Agent conversation.

## Global Constraints
- Each online intake adds at most 4 genuinely new URLs; repeats and known delivery-blocking IDs do not consume this cap.
- Candidate selection rotates through creator groups; it is round-robin, not random.
- Collector and intake never download media or publish to Rubika.
- The official Mob news lane stays separate and production-gated until a birth timestamp and explicit launch approval exist.
- AI is advisory only, daily-limited to 4 requests, and receives no tokens, full logs, cookies, arbitrary URLs, or media.
- Unknown/uncertain publishing state remains protected; AI cannot retry or send.

## Review Focus
- All candidates are already in D1 or previously sent/uncertain.
- A race produces a duplicate after initial queue inspection.
- Candidate metadata is stale, undated, future-dated, off-topic, or malformed.
- D1/AI binding is unavailable, daily AI quota is reached, or the model returns invalid JSON.
- Error hints recommend no destructive or publication action, even for uncertain delivery.

## Tasks
- [x] Verify and fix fair intake tests on the active branch.
- [x] Extend the scheduled job summary with only newly enqueued URLs and rerun the real Termux workflow.
- [x] Add Worker tests for safe error input, atomic daily cap, allowlist validation, fail-closed fallback, and fenced model JSON.
- [x] Implement and deploy the bounded AI Worker route and D1 event logging without adding publishing/download/delete capability.
- [x] Add the Python client/CLI and invoke it only when a workflow stage has failed.
- [x] Run the complete Python test suite, Worker API tests, the live scheduled metadata-only workflow, and verify deployed Worker route/bindings.
- [ ] Run one post-fix live Workers AI inference after the UTC daily four-call reservation limit resets; earlier CI failures consumed all four slots before the tolerant JSON parser was deployed.
