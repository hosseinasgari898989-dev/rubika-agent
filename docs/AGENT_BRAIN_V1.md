# Rubika-Agent brain, version 1

## Two separate content tracks

### Poppy Playtime edits from TikTok, YouTube and Instagram
- Discovery adapters must produce canonical source URLs, stable external IDs and source metadata. Search-result menus, tags and pages are not media candidates.
- The default batch requirement remains five TikTok items and five YouTube items. Instagram can be included through an explicit per-source batch count.
- Candidate IDs and SHA-256 hashes are checked for duplicates. Source accounts in config/trusted_sources.json are intentionally empty until the owner chooses and verifies them.
- Every file is inspected as the actual media type. Video output must be 1080x1080. The normalizer crops a source with at least 1080 pixels on both dimensions; the original is never overwritten. Center-cropping can cut off a subject, so a visual suitability check is still needed before approving a normalized clip.
- Metadata keywords and text checks are only weak signals. The six content checks for relevance, edit status, insults against Poppy characters, insults against other games/fandoms, editing effort and music are represented as explicit moderation fields. Without those fields the item is held for review and cannot enter a publish-ready batch.
- Trusted sources can skip weak title/hashtag signal warnings, but not duplicate checks, file validation, resolution, or explicit content safety checks.

### Official Mob Entertainment news
- The official-source track accepts video, image and audio media independently of edit-style filtering.
- A caption can be formatted with the original text and a Persian translation supplied by a translation adapter. No source caption or translation is invented if none is available.
- Multi-image carousels are represented distinctly; they need an album-aware downloader/uploader before being marked ready.

## Delivery safety
- Stage the complete requested batch before publication. A partial batch is never declared ready.
- Retry only a definite structured Rubika rejection, up to three attempts.
- A transport exception or response without a clear status is marked uncertain and stops immediately to avoid duplicate posts.
- Local/MEGA staged copies are deleted only after Rubika returns status OK. If local cleanup fails, preserve the MEGA copy for recovery.
- The Rubka adapter uses the tested constructor and methods. It does not import or publish on module import.

## What is still an adapter, not solved automatically
- Public indexed TikTok/Instagram search is best-effort: the last live diagnostic returned no canonical TikTok or Instagram links, even where search pages loaded. A successful workflow is not proof of discovery success; candidate counts and canonical-link evidence are the real result.
- Instagram now has an optional official Graph API hashtag-search adapter. It requires an eligible Professional Instagram account, a linked Facebook Page, the needed Meta app permissions/review, and the `INSTAGRAM_GRAPH_ACCESS_TOKEN` plus `INSTAGRAM_GRAPH_USER_ID` GitHub Actions secrets. The API version defaults to `v26.0`; no credentials are stored in source or logged. If those prerequisites are unavailable, the adapter reports `not_configured` and does not send network requests.
- TikTok's public Research API is restricted to approved qualifying researchers and research use; it is not treated as a general free search API for a content channel. Current public fallback endpoints returned access denials, challenge pages, or network/redirect failures. No CAPTCHA/login bypass is implemented.
- Automatic visual/audio judgement needs a real classifier integration. Until then, candidates are fail-closed as needs_review.
- The YouTube Termux downloader that previously succeeded must be identified and reused separately; a GitHub Actions download failure does not invalidate the phone-side method.
- A durable state-write API to Cloudflare D1 and a full discovery-to-delivery workflow are separate integration steps. The current Worker production API remains read-only until those are deployed and authenticated.
