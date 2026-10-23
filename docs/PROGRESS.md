# Rubika-Agent Progress

## 2026-10-23 - Phase 0 complete (PR #40 merged)

- Starting main at 3db2d150104a802919f16a6425fe070e9fbef140; Phase 0 merged as aaa0ec50a385e3747e3c93f11d18932fc7a16f2b. Prior discovery branch remains at 3aaff9e783e458fe9d30bf5bf4d5c793cffe01f4.
- Backup: backup/pre-phase-0-20261023-2. The unsuffixed date branch already existed, so a fresh backup name was used. Work branch: feature/v2-phase-0.
- Ported current intake validation and fixed the double-escaped visible-text regex. Removed the Mob birth cutoff from TikTok intake; TikTok date limits belong to the Phase 6 per-channel since_utc engine.
- Moved discovery workflow checkout and report/state pushes to main; left the old branch untouched.
- Added intake, branch-guard, and workflow-reference regression tests.
- No live workflow was dispatched and no Rubika post was sent. No production database or Worker changes were made.
- CI run 40010773585 succeeded: 398 Python tests, Worker JS tests, Rubka API surface check, and self-test all passed. Live Worker and Rubika credential checks were skipped. The separate pre-change main baseline was not independently run because shell clone failed on DNS.

## Open PR triage (refreshed 2026-10-24)

- #8-#11, #16, #21, #22 and #37: closed as superseded; none were merged.
- #17: keep for discovery hardening review; do not merge without inspecting the live workflow diff.
- #18: the confirmed metadata-regex defect was fixed in the merged Phase 0/1 work; verify the diff before closing as superseded.
- #19: stale Worker-supervisor client cleanup still requires a diff review.
- #23: private assistant experiment, out of scope.
- #38: contains a historical audit/documentation proposal; compare it with the newly refreshed docs before deciding whether to close.
- #39: draft E2E safety PR; its manual-only publishing protections are being ported to the current main-based workflow in Phase 2. Close only after the replacement passes CI.

## 2026-10-23 - Phase 1 complete (PRs #41 and #42 merged; CI green)

- Deleted the unknown-source TikTok path rather than merely narrowing it: removed the unknown-source policy defaults, public hashtag-feed discovery, legacy TikWM/search-engine TikTok search, ProxiTok RSS discovery, and the associated search-index/ProxiTok modules and tests. TikTok metadata now originates only from the six configured creator profiles.
- Enforced the owner-selected source rule: caption or hashtags must contain a configured `poppy` or `playtime` substring (case-insensitive); no separate edit-marker gate. Basic media integrity, duplicate ID/SHA, minimum quality, and audio-stream checks remain.
- Removed TikTok date/age filtering from discovery/intake; Phase 6's per-channel `since_utc` engine owns that rule. The separate official Mob lifecycle cutoff remains unchanged.
- Canonical TikTok URLs now strip tracking parameters and resolve supported short links once; numeric video IDs remain required.
- Cobalt quality uses configured 1080 maximum. Upscaling is disabled; sources below 1080 short-side remain native. Downloaded source dimensions are included in the existing validation JSON payload without a D1 migration.
- Removed bold-markup markers from edit captions. Confirmed-success delivery removes the local copy but retains the MEGA copy for the scheduled 24-hour cleanup policy.
- Corrected the publisher test's import order and retained YouTube/Instagram metadata behavior without TikTok discovery fallbacks.
- PR #41 merged as `97578088cebcdc58fa6cf520e07fb38c84410b8c`; PR #42 merged as `e3fc45cf190f7b592e9e545379e61395c13592b7`. Relevant GitHub Actions checks passed (PR #41 runs 40019377665/40019377673; PR #42 runs 40019699966/40019700005; main run 40019852168).
- No Worker deploy, D1 write/migration, publishing workflow dispatch, media delivery, MEGA cleanup, or Rubika post was performed. Live Worker/Rubika credential checks were not part of this phase.

## 2026-10-24 - Phase 2 complete (PRs #43–#49)

- Backup before the remaining Phase 2 work: `backup/pre-phase-2c-20261024`, created from original main `fa192e01bd9f2364a64c0df8800646dda7e959a2`.
- PR #43 (`e9290cadee941a04542de5869f25884e28b9462c` corrected stale Phase 1 status documentation. PR #44 (`d91139f255c01ec8b8416f872655fbed736e2b35` removed five retired/no-op workflow placeholders; runs `40032158121` and `40032158184` passed.
- PR #45 (`c855905cec91522f4182beccbd9aab7975729007` made GitHub Actions + Termux Docker the single daily discovery path, removed the old phone entrypoints/manual report push, and added manual-only default-false E2E publication confirmations. Checks `40035119306` and `40035119308` passed; read-only post-merge Worker health run `40035199767` passed.
- PR #46 (`92dd54b710204c97374810718bfdf6792dd1685a` fixed the report-source contract between cloud discovery and D1 queue intake, accepting the new cloud label and historical reports. Checks `40035282944` and `40035282947` passed; post-merge Cloud bootstrap run `40035384915` passed.
- PR #47 (`8730db8d48bcb49064984e0aec761e7fdf682b72` and PR #48 (`fa192e01bd9f2364a64c0df8800646dda7e959a2` refreshed the Phase 2 checkpoint and handoff. Checks `40035474771` and `40035590691` passed; post-merge Cloud bootstrap run `40035653646` passed.
- PR #49 merged as `087a880317a8140afd790c2742e6cea696123439` and completed the remaining provider cleanup. It removes the Gemini-only client and tests after extracting provider-neutral code-repair guardrails and the generic AI usage-ledger client into `agent/brain/code_repair_guardrails.py` and `agent/brain/ai_usage_ledger.py`; it preserves active Groq/OpenRouter code and `.github/workflows/groq-openrouter-code-repair.yml`. Final PR-head checks passed: Cloud bootstrap run `40038338630` and Agent brain run `40038338665`; post-merge main CI also passed in run `40038415580`.
- Retired provider references were removed from active Python modules, tests, current docs and workflow configuration. Historical IDs in migration `0005` comments were left untouched as immutable migration-history context; no migration was run. No `ai-health-check.yml` exists in the audited workflow tree, so current live Groq/OpenRouter availability remains unverified.
- The live smoke workflow for Groq/OpenRouter remains available but is now manual-only because it makes real provider requests and writes usage records to D1. It was not dispatched. The bounded repair workflow remains enabled: it triggers from completed upstream runs but starts only for eligible failures on main; successful upstream CI explains its recent skipped runs and does not prove the repair path is dead.
- PR #38 was closed after its unique dated audit snapshot was ported to `docs/RUBIKA_AGENT_AUDIT_2026-10-16.md`; its remaining stale documentation is superseded by the refreshed README, cloud-discovery guide, owner-console notes and master handoff.
- PR #17 remains open for targeted discovery hardening review (official Termux mirror fallback and TikTok-only cycle guard are potentially useful); its current diff is stale/dirty and must not be merged as-is. PR #19 remains open because the stale `CloudState.supervise_error` client and tests still exist in main; rebase and run CI before considering its focused removal.
- No branch was deleted. Candidate-only old non-backup branches are listed in the handoff/report, not removed.
- No publishing workflow was dispatched; no provider API request, media download, Rubika post, D1 write/migration, secret change or Worker deployment was performed during Phase 2c.

Provider-cleanup implementation commit (PR #49): `087a880317a8140afd790c2742e6cea696123439`; final documentation checkpoint PR #50 merged as `01167e6837af9098ccde11fa450d259b66058227`.
