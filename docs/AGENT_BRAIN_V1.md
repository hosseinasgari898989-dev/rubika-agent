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

### Instagram and Apify
- Instagram public-index discovery was repeatedly empty on the GitHub runner. The Termux collector does not depend on that old route: it can optionally call the Apify apify/instagram-hashtag-scraper Actor for metadata only.
- Put the Apify token on the phone at ~/.config/rubika-agent/apify_token with file permissions readable only by your Termux user, or provide APIFY_API_TOKEN in the environment. Never put it into Git, a report, or a message.
- The adapter reads Apify's monthly usage endpoint first. If usage cannot be verified, it does not start the Actor. It refuses new runs once account usage reaches the configurable default safety ceiling of $3.50, preserving at least $1.50 of the advertised $5 monthly Free-plan credit as a buffer.
- Maximum: one attempted Actor run per UTC day per local state file, one hashtag, at most two Reels, and no media download. A failed or timed-out attempt still consumes the day's local allowance so rerunning cannot accidentally create a second paid run. The daily state lives under ~/.local/state/rubika-agent/, outside the repository.
- Apify Free-plan credits reset with the account's billing cycle, not necessarily on the first day of a calendar month. Unused credits expire and do not roll over. This code is a conservative budget guard, not a guarantee that the Actor's charge can never change. Check the Apify billing page before enabling the integration; the guard fails closed.
- Without a token, Instagram reports not_configured and makes no Apify requests.

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
- Instagram Apify requires the owner to create an account and store an API token privately on the phone. The adapter intentionally uses only two requested results daily and checks monthly usage before each run.
- Automatic visual/audio judgement needs a real classifier integration. Until then, candidates are held as needs_review.
- YouTube downloads from Termux need a user-side live test and later integration into the queue. Metadata collection alone is not download proof.
- A full discovery -> permission check -> download -> inspect -> moderation -> MEGA/D1 -> delivery chain has not yet been validated end-to-end.
- config/trusted_sources.json intentionally remains empty. The staging pipeline will not download or repost third-party material until explicit permission or a suitable license is recorded.
- The production Worker is not deployed with new brain routes. Production publishing remains disabled while these requirements are incomplete.
