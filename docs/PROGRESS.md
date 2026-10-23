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
