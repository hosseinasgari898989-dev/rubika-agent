# Rubika-Agent session checkpoint — 2026-10-14

## Safe resume point
- Repository: `hosseinasgari898989-dev/rubika-agent`
- Branch: `agent-brain-mega-v1`
- Code baseline before this checkpoint: `27050a36d82af1baf095d6e19ce80cdc055edadf`
- Current checkpoint purpose: preserve verified project status and next actions across a new chat.
- Do not assume this markdown commit changes the application code.

## Non-negotiable publishing safety
- Authorized test channel only: `c0EFohK0f3174377fb6fa422d3ccb230`
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

## Current Mob X discovery limitation
- `config/mob_official_sources.json` uses seeded URLs as fallback. The three recently sent seeds above should be treated as already sent; D1 dedupe is expected to protect them.
- One candidate was skipped because it exposed no actual media; another was skipped because its image was only 190×148.
- Free search from GitHub Actions is unreliable (Bing indexed no useful posts; TwStalker mirrors returned 403; DuckDuckGo returned 202 challenge). Do not claim fully autonomous future X profile crawling is solved.
- Next research task: find a reliable free/public way to discover newer Mob Entertainment X posts without a paid API, verify author/caption/timestamp/original media, and preserve source-lane filtering and D1 dedupe.
- Do not use thumbnails/profile pictures as substitutes for missing original post media.

## Suggested next steps
1. Resume by inspecting the current branch head and the workflows/config before changing anything; this checkpoint was added after the recorded code baseline.
2. Work on the free X-discovery source in a non-publishing discovery/test path first.
3. Keep tests green and avoid a trigger commit to discovery artifacts unless intentionally running the one-post test-channel workflow.
4. Keep all explanations in Persian and concise; user prefers beginner-friendly, step-by-step guidance.
