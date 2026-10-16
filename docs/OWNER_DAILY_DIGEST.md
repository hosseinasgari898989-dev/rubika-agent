# Rubika owner 24-hour digest

## What it does

- Keeps one private announcement message for a rolling 24-hour window.
- Every call to `send_owner_notification` appends a sanitized event and edits that message.
- Starts a fresh window after the previous window expires. The old Rubika message remains as yesterday's record.
- Records code-repair AI provider outcomes in D1. The scheduled refresh workflow updates the message with these events within about 15 minutes; direct Python/Actions notifications update it immediately.
- Shows counts for information, successes, errors, AI successes and AI errors, with a bounded list of recent events.
- Uses the synchronous Rubka `Bot.edit_message_text(chat_id, message_id, text)` API, available in the supplied `rubka-main.zip` source.
- Uses a D1 lease to serialize concurrent edit attempts. If a send result is ambiguous and no message ID is available, it does not send a second message automatically.
- Stores only short, sanitized operational titles/details and safe provider/error codes. It does not store tokens, raw provider responses, URLs or source media.

## Files changed

- `migrations/0005_owner_daily_digest.sql`: persistent 24-hour window, message state, lease and event history.
- `src/index.js`: authenticated event/claim/commit endpoints; cloud code-repair AI outcomes and quota/cooldown skips are recorded without affecting provider results.
- `agent/brain/owner_notify.py`: append, format, send-once, edit-existing, duplicate-avoidance and manual refresh behavior.
- `.github/workflows/owner-daily-digest.yml`: schedules a daily starter announcement.
- `.github/workflows/owner-digest-refresh.yml`: refreshes only if the digest text changed.
- `tests/test_owner_notify.py`, `tests/worker.test.mjs`, and `tests/test_migrations.py`: focused regression coverage.
- `.github/workflows/brain-ci.yml`: enables checks for this feature branch and runs owner-digest tests.

## Activation and rollback

This feature branch does not deploy the Worker or apply a remote D1 migration. It is not live until the code is reviewed, merged, the new D1 migration is applied, and the Worker is deployed. The workflow requires the existing Actions secrets `AGENT_API_TOKEN`, `RUBIKA_TOKEN`, and `RUBIKA_OWNER_CHAT_ID`.

Keep the migration data when rolling back; reverting the feature code/workflows disables the digest implementation without deleting the event history. Do not merge or deploy until CI has succeeded and the owner approves.
