# Rubika-Agent Progress

## 2026-10-23 - Phase 0 complete (PR #40 merged)

- Starting main at 3db2d150104a802919f16a6425fe070e9fbef140; Phase 0 merged as aaa0ec50a385e3747e3c93f11d18932fc7a16f2b. Prior discovery branch remains at 3aaff9e783e458fe9d30bf5bf4d5c793cffe01f4.
- Backup: backup/pre-phase-0-20261023-2. The unsuffixed date branch already existed, so a fresh backup name was used. Work branch: feature/v2-phase-0.
- Ported current intake validation and fixed the double-escaped visible-text regex. Removed the Mob birth cutoff from TikTok intake; TikTok date limits belong to the Phase 6 per-channel since_utc engine.
- Moved discovery workflow checkout and report/state pushes to main; left the old branch untouched.
- Added intake, branch-guard, and workflow-reference regression tests.
- No live workflow was dispatched and no Rubika post was sent. No production database or Worker changes were made.
- CI run 40010773585 succeeded: 398 Python tests, Worker JS tests, Rubka API surface check, and self-test all passed. Live Worker and Rubika credential checks were skipped. The separate pre-change main baseline was not independently run because shell clone failed on DNS.

## Open PR triage
- #8-#11: superseded repair harnesses; close in Phase 2.
- #16: retired provider path; close in Phase 2.
- #17: keep for discovery hardening review.
- #18: confirmed regex issue, fixed in Phase 0 handover.
- #19: keep for Phase 2 stale-client review.
- #21-#22: overlapping old phone-path work; close in Phase 2 when replacement is verified.
- #23: private assistant experiment, out of scope.
- #37: appears redundant; recheck and close in Phase 2.
- #38: reassess once docs are refreshed.
- #39: port its manual-only E2E safety protection in Phase 2, then close the draft.

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
