# Rubika-Agent session checkpoint — 2026-10-14

## Safe resume point
- Repository: `hosseinasgari898989-dev/rubika-agent`
- Branch: `agent-brain-mega-v1`
- Code baseline before this checkpoint: `27050a36d82af1baf095d6e19ce80cdc055edadf`
- Current checkpoint purpose: preserve verified project status and next actions across a new chat.
- Do not assume this markdown commit changes the application code.

## Non-negotiable publishing safety
- Authorized test channel only: the existing secret; its SHA-256 must match the registered test-channel pin.
- Production/main-channel publishing remains disabled.
- One post maximum per run.
- Never claim delivery without a successful run report plus a D1 `status=sent` row with Rubika message ID.
- Avoid unnecessary updates to `artifacts/termux_discovery.json`: those pushes trigger discovery/queue workflows and may cause multiple workflows to run.

## Completed code changes
1. `de7650db0deb51705e7c3b5b7390a8c2976b407c` — known owner-selected TikTok creators accept flexible/compound Poppy/Playtime/edit tags; unknown creators remain strict. Tests passed: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38619967320
2. `4b6e8b9c44a48899c25655765c23409af3fc54bc` — official news candidate filtering by source lane (`x/website/youtube/steam)); X-only mode does not fall back to other platforms. Tests passed: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38620384815
3. `644d8921aad126eecba12b8e5f537c300c93fa8c` — official trial workflow uses live official-source discovery, filters to X, and publishes at most one item to the test channel. Run: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38620503765
4. `d3b65bdad5940e665ca83cbd79273f96a5b2bb06` — improved Mob X media seeds; excludes a low-resolution image. Relevant workflows passed: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38620852049, https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38620852097, https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38620852121
5. `27050a36d82af1baf095d6e19ce80cdc055edadf` — triggered one more safe X test-channel run for the next unsent seed. Run: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38621132428

## Verified publishing receipts
All four X items below have D1 status `sent` and a Rubika message ID:
- `x-2107878755079213476` — message ID `1939805047112836875`; older Oct 7 image.
- `x-2110370731628298519` — message ID `1939851072320836875`; Oct 14 “keep watching” video, H.264/AAC, 530×530, 18.048s. Run: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38620503765
- `x-2109690712006533267` — message ID `1939860074015836875`; Oct 12 Huggy image, 1200×1200 JPEG. Run: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38620852097
- `x-2101713521020912076` — message ID `1939861075065836875`; Sep 20 Escape From Playtime video, 1280×720, 67.45s. Run: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38621132428

TikTok end-to-end test also verified:
- Source: `https://www.tiktok.com/@smyxae/video/7696217798850186516`
- D1 key: `tiktok:7696217798850186516`
- Rubika message ID: `1939822056274836875`
- Output was 1080×1080, H.264, music check passed.
- Run: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38616544013

## Ambiguous TikTok attempt — do not retry automatically
- D1 key: `tiktok:7694421166508838165`
- Current status: `uncertain`; error: `batch_run_cancelled_after_attempt_start`.
- The batch workflow was cancelled after the attempt started; there is no confirmed receipt or Rubika message ID. Delivery could have happened or not.
- It was deliberately marked `uncertain` under a guarded D1 update to prevent duplicate publishing. Reconcile manually before any retry.

## Previous Mob X discovery limitation (before follow-up below; superseded)
- `config/mob_official_sources.json` uses seeded URLs as fallback. The three recently sent seeds above should be treated as already sent; D1 dedupe is expected to protect them.
- One candidate was skipped because it exposed no actual media; another was skipped because its image was only 190×148.
- Free search from GitHub Actions is unreliable (Bing indexed no useful posts; TwStalker mirrors returned 403; DuckDuckGo returned 202 challenge). Do not claim fully autonomous future X profile crawling is solved.
- Next research task: find a reliable free/public way to discover newer Mob Entertainment X posts without a paid API, verify author/caption/timestamp/original media, and preserve source-lane filtering and D1 dedupe.
- Do not use thumbnails/profile pictures as substitutes for missing original post media.

## Original suggested next steps (superseded by follow-up below)
1. Resume by inspecting the current branch head and the workflows/config before changing anything; this checkpoint was added after the recorded code baseline.
2. Work on the free X-discovery source in a non-publishing discovery/test path first.
3. Keep tests green and avoid a trigger commit to discovery artifacts unless intentionally running the one-post test-channel workflow.
4. Keep all explanations in Persian and concise; user prefers beginner-friendly, step-by-step guidance.


## Follow-up checkpoint — 2026-10-14, after public X profile integration

### Latest code and checks
- Branch head at code milestone: `41c200698428fbd2a7e751354523e2855c84c734` — `Discover recent Mob X posts from public profile metadata`.
- Files changed atomically: `agent/discovery/termux_mob_x.py` and `tests/test_termux_mob_x.py`.
- New source: unauthenticated read-only request to `https://unifapi.com/tools/x/profile?handle=mobgamesstudios`. It is only an ID-discovery source. Candidates still pass `cdn.syndication.twimg.com/tweet-result` checks for the exact Mob handle, canonical post ID, real caption, timestamp, and supported original media. Third-party caption/author/timestamp/media values are not trusted.
- New unit test ensures unrelated profile text/author values are ignored and an off-handle X URL is rejected.
- Brain CI passed: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38624376266
- Live read-only discovery passed: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38624376177
- One-post test-channel trial passed: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38624376039
- The live-check showed 10 public profile rows; the first three IDs were confirmed via X syndication. The broader discovery run found 6 X posts with usable official metadata. This improves discovery today but is not a guarantee the third-party endpoint will remain available; retain the existing fallback sources.
- Do not alter `artifacts/termux_discovery.json` to test this path. It was not changed.

### Newly verified publishing receipt
- Workflow: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38624376039 — completed `success`.
- Artifact report: `status=published`, `publishing=true`, `rubika_message_id_recorded=true`, `error=null`; one item maximum, approved test channel only.
- Published X item: `x-2108957995074011326` — “Lily Lovebraids spotted at New York Comic Con!! Lovely cosplay by Coco!”
- Media validated: JPEG, 768×1024, 199427 bytes; original caption was available and Persian translation succeeded.
- Direct read-only query of D1 `rubika-agent-db` verified both `items.status=sent` and `delivery_attempts.status=sent`, with matching Rubika message ID `1939893090988836875`; `sent_at=2026-10-14T19:32:45.301Z`. This is a confirmed receipt.
- The candidate `x-2110158933356847501` was not published in that run because Persian translation was unavailable. Do not mark it sent based on discovery alone.
- Other source lanes were filtered out for publishing despite being read by the merge; actual trial was explicitly restricted to X.

### Safety state unchanged
- Only authorized destination: the existing secret, accepted only when its SHA-256 matches the registered test-channel pin.
- Production/main-channel publishing remains disabled; limit stays at one post per trial run.
- TikTok `tiktok:7694421166508838165` remains `uncertain`; do not automatically retry.
- Next step: monitor ongoing free-source reliability and improve fallback resilience if the profile endpoint is unavailable. Any code change to `agent/discovery/termux_mob_x.py` triggers Brain CI, the read-only live check, and the one-post test-channel trial; prepare tests before a single atomic push.


### Follow-up: controlled UnifAPI rate-limit header probe — 2026-10-14
- One-shot GitHub Actions run: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38628728472 — one GET to `https://unifapi.com/tools/x/profile?handle=mobgamesstudios`, HTTP 200, `X-RateLimit-Limit=20`, `X-RateLimit-Remaining=19`, zero downloads and zero Rubika posts.
- Controlled same-job comparison: https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38628872233 — two sequential GETs, both HTTP 200, both returned `X-RateLimit-Remaining=19` and the same reset timestamp. The counter did **not visibly decrease** between these two requests.
- Interpretation: this test does not prove that usage is never counted; the endpoint may cache or expose a non-per-request/shared header. Do not claim that these requests definitely consumed one unit each. More importantly, do not infer account-wide usage from the response without a documented quota identity/scope.
- The temporary probe workflow `.github/workflows/unifapi-quota-probe.yml` was deleted after the test. No media was downloaded and no Rubika post was sent by these probe runs.
