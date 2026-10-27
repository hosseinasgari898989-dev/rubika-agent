# Web console v2 vs src/console_api.js (2026-10-27)

Status: OK = complete, PART = partial, NONE = no real API (UI must be disabled + labelled).

| Feature | API | v2 UI | Status | Action |
|---|---|---|---|---|
| Dashboard (status, queue summary, AI quota) | /api/status, /api/brain/* (index.js) | yes | OK | - |
| Items list/filter/edit | GET /items, PATCH /items/:id | yes | OK | approved is never set from console |
| Queue list + requeue/skip/delete | d1/query + POST /queue/:id/action | yes | OK | retry_now = 501 (not shown) |
| History (runs/events/brain/delivery/audit) | 5 GET endpoints | yes | OK | - |
| AI calls | GET /ai/calls | yes | OK | - |
| Sources CRUD | /sources | yes | OK | only TikTok and X can be registered; X maps to mob_official |
| X links add/clear | /links, /links/clear | yes | PART | confirm/cancel missing -> ext: Instant page |
| Owners CRUD + invite | /owners, /owners/invite | yes | OK | - |
| Rules (cap, edit count, quiet hours) | GET/PUT /rules | yes | PART | no UTC+Tehran view; save dropped quiet_hours.enabled -> ext |
| Channels | /channels | yes | OK | no enable/toggle API |
| Jobs create/list | /jobs | yes | PART | type mob_x_link always 400; no job engine (records only) -> ext |
| D1 explorer | /d1/tables, /d1/query | yes | PART | bug: data-n on chips is overwritten by counter animation -> ext |
| Instant mode (link -> job, test channel only) | /links -> /links/:id/confirm -> job | no | PART | ext: Instant page (Mob X links only, 2-step) |
| Mob X job detail/preview state | GET /jobs/:id | no | PART | ext: read-only detail |
| Owner approval of Mob X job | none in console API (PV only) | no | NONE | label: approve from PV |
| Final publish | /mob-x/publish-claim (runner only) | no | NONE | runner-only; test channel only |
| Video preview in web | none (no media stream endpoint) | no | NONE | PV preview only |
| Daily-count scheduling across days (4 today -> 7, rest later) | only daily_success_cap | no | NONE | cap only |
| Mega management / schedules / repo editor / feature requests | none | 'soon' page | NONE | already labelled |
| Main channel readiness / unlock | GET /rules forces publishing_enabled=false | no | NONE | locked; needs explicit owner approval |

Locks enforced by the Worker (not changeable from the console): publishing_enabled=false, test_channel_only=true, production channels always enabled=0.
Quiet hours are evaluated in UTC (00:30-07:00 UTC = 04:00-10:30 Tehran, fixed UTC+3:30).
