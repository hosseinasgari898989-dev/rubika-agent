# Rubika-Agent brain, version 1

## Two separate content tracks

### Poppy Playtime edits from TikTok, YouTube and Instagram
- Discovery adapters must produce canonical source URLs, stable external IDs and source metadata. Search-result menus, tags and pages are not media candidates.
- هدف نهایی کشف پنج TikTok و پنج YouTube در هر چرخه است؛ اما گردش‌کار زمان‌بندی‌شدهٔ فعلی فقط metadata مربوط به TikTok را به صف عادی وارد می‌کند (حداکثر چهار URL جدید در هر چرخه) و نامزدهای رسمی Mob را در مسیر جدا نگه می‌دارد. YouTube و Instagram هنوز در intake معمول پنج‌ساعته فعال نیستند.
- Candidate IDs and SHA-256 hashes are checked for duplicates. The current config/trusted_sources.json contains broad wildcard permission assertions; those are configuration values, not independent proof of rights. Before production publishing, source permissions must be narrowed to verifiable, approved sources.
- Every file is inspected as the actual media type. Video delivery copies are normalized to vertical 1080x1920 MP4 while preserving the full source frame (smaller sources of at least 480 pixels per dimension can be upscaled and padded). The original is never overwritten; final output is re-validated before staging.
- Metadata keywords and text checks are only weak signals. The six content checks for relevance, edit status, insults against Poppy characters, insults against other games/fandoms, editing effort and music are represented as explicit moderation fields. Without those fields the item is held for review and cannot enter a publish-ready batch.
- Trusted sources can skip weak title/hashtag signal warnings, but not duplicate checks, file validation, resolution, or explicit content safety checks.

## Cloud-hosted Termux discovery

GitHub Actions runs the collector inside the Termux Docker environment on its cloud runner. No daily command, Git authentication, or manual push from the phone is required. The scheduled workflow writes metadata-only output, validates it, pushes only the discovery report to agent-brain-mega-v1, and then imports eligible TikTok metadata into D1. Keep termux/daily_discovery.py because the cloud collector uses it; the phone-specific bootstrap and daily shell wrappers are not part of the supported flow.

### TikTok
- The free cloud-hosted adapter requests public hashtag pages from the cloud runner and parses only embedded metadata or canonical video links already present in returned HTML.
- It does not log in, use cookies, bypass a CAPTCHA, use third-party proxies, download media, or pretend a blocked/empty page succeeded.
- صفحهٔ عمومی ممکن است challenge، HTML خالی یا نتیجهٔ canonical نداشته باشد. موفقیت زنده باید از گزارش گردش‌کار ابری ارزیابی شود؛ تست parser به‌تنهایی کارکرد واقعی منبع را ثابت نمی‌کند.

### YouTube
- آداپترهای discovery مربوط به YouTube در مخزن وجود دارند، اما گردش‌کار عادی پنج‌ساعتهٔ فعلی YouTube را به صف معمول وارد نمی‌کند.
- دریافت رسانهٔ YouTube در runner ابری هنوز به آزمایش زنده و اتصال کامل به صف نیاز دارد؛ Relay اختیاری مجوزدار هنوز Deploy نشده است.

### Instagram (not in the active scheduled intake)
- کدهای آزمایشی Instagram در مخزن باقی مانده‌اند، اما مسیر زمان‌بندی‌شدهٔ فعلی از آن‌ها برای پرکردن صف انتشار استفاده نمی‌کند.
- صفحات عمومی ممکن است login wall یا challenge برگردانند؛ وجود تست parser به معنی اثبات کشف زنده نیست.
- فعلاً از Apify یا سرویس پولی استفاده نمی‌شود؛ افزودن Instagram به صف عادی به اتصال و آزمایش مستقل نیاز دارد.

## What happens after the scheduled cloud runner creates a report
گردش‌کار discovery ساختار گزارش و رابطهٔ host/ID canonical را بررسی می‌کند، collector را در Termux ابری اجرا می‌کند و metadata معتبر TikTok را به صف پایدار وارد می‌کند. وضعیت پنج‌ساعته فقط پس از موفقیت queue-intake به‌روزرسانی می‌شود؛ پاسخ HTTP به‌تنهایی اثبات موفقیت نیست. گزارش برای بررسی به‌صورت artifact نگهداری می‌شود.

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
- مسیر کشف ابری تست parser دارد، اما نتیجه‌های زندهٔ TikTok باید در لاگ اجرای زمان‌بندی‌شده پایش شوند.
- Instagram is not part of the active five-hour intake. Any future activation must be validated against live cloud-runner results before it is treated as reliable.
- Automatic visual/audio judgement needs a real classifier integration. Until then, candidates are held as needs_review.
- دریافت رسانهٔ YouTube در runner ابری به آزمایش زنده و ادغام مطمئن در صف نیاز دارد. Metadata collection alone is not download proof.
- A full discovery -> permission check -> download -> inspect -> moderation -> MEGA/D1 -> delivery chain has not yet been validated end-to-end.
- config/trusted_sources.json currently contains wildcard permission assertions and must be audited before production. A config flag alone is not independent proof that every third-party video is authorized for reposting.
- The production Worker is not deployed with new brain routes. Production publishing remains disabled while these requirements are incomplete.
