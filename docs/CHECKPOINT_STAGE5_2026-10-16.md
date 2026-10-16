# Rubika-Agent checkpoint — Stage 5 — 2026-10-16

## Scope completed in the draft branch

Stage 5 implements the first read-only slice of the owner management console: the private Rubika bot can accept a short allowlist of commands and reply with status information sourced from the Worker, D1 summaries, provider usage ledger and GitHub Actions metadata.

Commands: `/help`, `/status`, `/queue`, `/ai-status`, `/ai-status <provider>`, and `/report`.

The console is intentionally command-based at this stage; it does not use an LLM, execute arbitrary instructions, edit code, repair bugs, mutate the publishing queue, merge PRs, deploy, migrate remote D1 or publish channel posts.

## Current GitHub state

- PR #24: [Draft owner digest](https://github.com/hosseinasgari898989-dev/rubika-agent/pull/24), open and unmerged. Migration ordering was fixed: existing `0005_ai_provider_ledger.sql` is preserved, digest schema is `0006_owner_daily_digest.sql`. Latest checked runs `38848004714` (Agent brain checks #445) and `38848004840` (Cloud bootstrap checks #240) succeeded.
- PR #25: [Stage 5 read-only Rubika operations console](https://github.com/hosseinasgari898989-dev/rubika-agent/pull/25), open, draft, unmerged and stacked on PR #24's branch. If later approved, review order is PR #24 first, PR #25 second.
- Stage 5 branch: `feature/rubika-readonly-ops-console-stage5`.
- Code is not live. No remote D1 migration, Worker deployment, live Rubika poll/reply, provider request, channel publish, or merge was performed.

## Latest verified automated checks

- Agent brain checks #455, run [38849356778](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38849356778): success. Log confirms owner-console tests (20), migration tests (5), workflow-safety tests (3), other brain/storage suites and Worker API tests passed.
- Stage 5 isolated offline checks #11, run [38849353459](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38849353459): success.
- Stage 5 isolated offline checks #12, run [38849356795](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38849356795): success.
- Local offline pipeline installs project requirements, compiles Python modules, tests fake Worker/Rubika clients, applies SQLite migration chain 0001–0007, runs workflow safety checks, Worker tests against the in-memory D1 fake and the console self-test (`publishing=false`, `queue_mutation=false`).
- Earlier CI failures were investigated: test precedence expected the wrong validation error for an allowlisted route with an unapproved query; the test and per-route method allowlist were corrected. A separate workflow initially lacked a dependency imported by the existing `agent.brain` package; the workflow now installs project requirements. Latest relevant CI runs succeeded.

## Safety architecture

- Both exact configured owner chat ID and sender/user ID must match.
- Only three fixed Rubika API methods are used: `getMe`, `getUpdates`, `sendMessage`.
- Unsupported requests are not passed to an AI model and are never interpreted as shell commands.
- Worker control routes use authenticated access and strict route/method/query allowlists.
- D1 stores an opaque updates cursor and minimal idempotency/audit rows with hashed identities, normalized commands and safe outcome codes; raw incoming text and raw owner identifiers are not stored.
- Uncertain message delivery is not automatically retried to avoid duplicate replies.
- Queue summary is implemented via new SELECT-only queries so it does not call the existing summary path that can repair stale processing rows.

## Remaining before live activation

1. Review PR #24 and PR #25 in dependency order.
2. Owner explicitly approves merging, remote migration and deployment as separate actions.
3. Configure `RUBIKA_TOKEN`, `RUBIKA_OWNER_CHAT_ID`, `RUBIKA_OWNER_USER_ID`, and `AGENT_API_TOKEN` as repository Actions secrets without sharing them in chat.
4. Apply pending D1 migrations in order and deploy only after explicit authorization.
5. Run one carefully controlled private-chat test, then verify a read-only status and queue report.

Until these steps are explicitly approved and completed, PR #25 remains a draft prototype, not a working/live Rubika bot integration.
