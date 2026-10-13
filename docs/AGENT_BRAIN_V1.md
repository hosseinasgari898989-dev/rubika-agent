# Rubika-Agent brain, version 1

## Two separate content tracks

### Poppy Playtime edits from TikTok, YouTube and Instagram
- Discovery adapters must produce canonical source URLs, stable external IDs and source metadata. Search-result menus, tags and pages are not media candidates.
- The default batch requirement remains five TikTok items and five YouTube items. Instagram is a budget-limited optional source; the phone collector asks Apify for at most two Reels per UTC day.
- Candidate IDs and SHA-256 hashes are checked for duplicates. Source accounts in config/trusted_sources.json are intentionally empty until the owner chooses and verifies them.
- Every file is inspected as the actual media type. Video output must be 1080x1080. The normalizer crops a source with at least 1080 pixels on both dimensions; the original is never overwritten. Center-cropping can cut off a subject, so a visual suitability check is still needed before approving a normalized clip.
- Metadata keywords and text checks are only weak signals. The six content checks for relevance, edit status, insults against Poppy characters, insults against other games/fandoms, editing effort and music are represented as explicit moderation fields. Without those fields the item is held for review and cannot enter a publish-ready batch.
- Trusted sources can skip weak title/hashtag signal warnings, but not duplicate checks, file validation, resolution, or explicit content safety checks.

## Phone-side daily discovery

Run the collector from the repository root on Termux:
    python -m termux.daily_discovery --push

It creates artifacts/termux_discovery.json with metadata only (no video downloads and no Rubika posts), then pushes only that report to agent-brain-mega-v1. The script refuses to push from another branch or a different repository origin. Configure Git authentication on the phone once before using --push.

### TikTok
- The free phone-side adapter requests a few public hashtag pages directly from the phone network and parses only embedded metadata or canonical video links already present in returned HTML.
- It does not log in, use cookies, bypass a CAPTCHA, use third-party proxies, download media, or pretend a blocked/empty page succeeded.
- A public page can still return a challenge, empty HTML, or no canonical results. This adapter is a new route compared with the old search-engine path, but live results must be checked on the phone before claiming it works reliably.

### YouTube
- The Termux collector uses yt-dlp search with skip_download enabled and keeps eligible results with known durations up to 180 seconds.
- It only submits metadata and Shorts URLs to the report. A GitHub runner download failure is not bypassed; the separate authorized-media relay remains a future download fallback.

### Instagram (free phone-side path)
- Instagram discovery now runs on the phone against public hashtag pages using ordinary unauthenticated HTTPS; it does not use Apify, a paid API, cookies, login sessions, proxies or CAPTCHA/challenge bypasses.
- The collector examines at most three hashtag pages and accepts at most two canonical video/Reel candidates per daily run. It extracts metadata only when the returned HTML exposes it; otherwise it reports zero/blocked rather than inventing candidates.
- Instagram may still return a login wall or challenge to a phone. Parser tests prove the extractor handles fixture HTML, not that live discovery works on every phone/network. The first live run is required before treating this route as reliable.
- The separate Apify adapter remains in the codebase as an optional future fallback, but the daily command does not call it and no Apify token is required.

## What happens after the phone pushes a report
The discovery workflow validates the schema and canonical host/ID relationship, merges the phone results with the existing identity history, and runs only the fallbacks still needed. The workflow's exit status is red if it cannot find the required five TikTok and five YouTube candidates; an HTTP 200 by itself is not success. The discovery report is uploaded as an artifact for inspection.

## Official Mob Entertainment news
- The official-source track accepts video, image and audio media independently of edit-style filtering.
- A caption can be formatted with the original text and a Persian translation supplied by a translation adapter. No source caption or translation is invented if none is available.
- Multi-image carousels are represented distinctly; they need an album-aware downloader/uploader before being marked ready.

## Delivery safety
- Stage the complete requested batch before publication. A partial batch is never declared ready.
- Retry only a definite structured Rubika rejection, up to three attempts.
- A transport exception or response without a clear status is marked uncertain and stops immediately to avoid duplicate posts.
- Local/MEGA staged copies are deleted only after Rubika returns status OK. If local cleanup fails, preserve the MEGA copy for recovery.
- The Rubka adapter uses the tested constructor and methods. It does not import or publish on module import.

## Remaining gaps before hands-off publishing
- The phone discovery route has parser tests, but real TikTok results still need a live test on the user's phone.
- Free Instagram hashtag discovery still needs a live phone test; the platform may return a login wall/challenge or no canonical video metadata.
- Automatic visual/audio judgement needs a real classifier integration. Until then, candidates are held as needs_review.
- YouTube downloads from Termux need a user-side live test and later integration into the queue. Metadata collection alone is not download proof.
- A full discovery -> permission check -> download -> inspect -> moderation -> MEGA/D1 -> delivery chain has not yet been validated end-to-end.
- config/trusted_sources.json intentionally remains empty. The staging pipeline will not download or repost third-party material until explicit permission or a suitable license is recorded.
- The production Worker is not deployed with new brain routes. Production publishing remains disabled while these requirements are incomplete.
