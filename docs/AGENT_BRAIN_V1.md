# Rubika-Agent brain, version 1

## Two separate content tracks

### Poppy Playtime edits from TikTok, YouTube and Instagram
- Discovery adapters must produce canonical source URLs, stable external IDs and source metadata. Search-result menus, tags and pages are not media candidates.
- The default batch requirement remains five TikTok items and five YouTube items. Instagram is a budget-limited optional source; the phone collector asks Apify for at most two Reels per UTC day.
- Candidate IDs and SHA-256 hashes are checked for duplicates. Source accounts in config/trusted_sources.json are intentionally empty until the owner chooses and verifies them.
- Every file is inspected as the actual media type. Video delivery copies are normalized to vertical 1080x1920 MP4 while preserving the full source frame (smaller sources of at least 480 pixels per dimension can be upscaled and padded). The original is never overwritten; final output is re-validated before staging.
- Metadata keywords and text checks are only weak signals. The six content checks for relevance, edit status, insults against Poppy characters, insults against other games/fandoms, editing effort and music are represented as explicit moderation fields. Without those fields the item is held for review and cannot enter a publish-ready batch.
- Trusted sources can skip weak title/hashtag signal warnings, but not duplicate checks, file validation, resolution, or explicit content safety checks.

## Cloud-hosted Termux discovery

GitHub Actions runs the collector inside the Termux Docker environment on its cloud runner. No daily command, Git authentication, or manual push from the phone is required. The scheduled workflow writes metadata-only output, validates it, pushes only the discovery report to agent-brain-mega-v1, and then imports eligible TikTok metadata into D1. Keep termux/daily_discovery.py because the cloud collector uses it; the phone-specific bootstrap and daily shell wrappers are not part of the supported flow.

### TikTok
- The free cloud-hosted adapter requests a few public hashtag pages directly from the phone network and parses only embedded metadata or canonical video links already present in returned HTML.
- It does not log in, use cookies, bypass a CAPTCHA, use third-party proxies, download media, or pretend a blocked/empty page succeeded.
- A public page can still return a challenge, empty HTML, or no canonical results. This adapter is a new route compared with the old search-engine path, but live results must be checked on the phone before claiming it works reliably.

### YouTube
- The Termux collector uses yt-dlp search with skip_download enabled and keeps eligible results with known durations up to 180 seconds.
- It only submits metadata and Shorts URLs to the report. A GitHub runner download failure is not bypassed; the separate authorized-media relay remains a future download fallback.

### Instagram (free phone-side path)
- Instagram discovery now runs on the phone against public hashtag pages using ordinary unauthenticated HTTPS; it does not use Apify, a paid API, cookies, login sessions, proxies or CAPTCHA/challenge bypasses.
- The collector examines at most three hashtag pages and accepts at most two canonical video/Reel candidates per daily run. It extracts metadata only when the returned HTML exposes it; otherwise it reports zero/blocked rather than inventing candidates.
- Instagram may still return a login wall or challenge to a phone. Parser tests prove the extractor handles fixture HTML, not that live discovery works on every phone/network. The first live run is required before treating this route as reliable.
- No paid Apify adapter is part of the current daily flow. We will only evaluate a paid fallback if the live free-phone test fails and the owner chooses to consider it.

## What happens after the scheduled runner creates a report
The discovery workflow validates the schema and canonical host/ID relationship, executes the collector in cloud-hosted Termux, and imports eligible TikTok rows into the persistent queue. The five-hour state is updated only after a successful queue-intake step; a successful HTTP response alone is not proof of useful discovery. The report is uploaded as an artifact for inspection.

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
- The cloud-hosted discovery route has parser tests, but real TikTok results still need to be monitored in scheduled-run logs.
- Free Instagram hashtag discovery may still return a challenge or no canonical video metadata; monitor the cloud runner's live report.
- Automatic visual/audio judgement needs a real classifier integration. Until then, candidates are held as needs_review.
- YouTube downloads in the cloud runner need a live test and reliable integration into the queue. Metadata collection alone is not download proof.
- A full discovery -> permission check -> download -> inspect -> moderation -> MEGA/D1 -> delivery chain has not yet been validated end-to-end.
- config/trusted_sources.json intentionally remains empty. The staging pipeline will not download or repost third-party material until explicit permission or a suitable license is recorded.
- The production Worker is not deployed with new brain routes. Production publishing remains disabled while these requirements are incomplete.
