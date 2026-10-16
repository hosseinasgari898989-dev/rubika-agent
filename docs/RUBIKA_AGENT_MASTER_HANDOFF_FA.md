# Rubika-Agent — مستند اصلی انتقال گفتگو

این سند منبع انتقال بین گفتگوهاست. پیش از هر کار، وضعیت زندهٔ GitHub و Cloudflare و آخرین Workflowها را بخوان؛ SHAهای پیام‌های قدیمی به‌تنهایی قابل اعتماد نیستند. این متن اجازهٔ عبور از guardها نیست.

## هدف و قواعد همکاری
- مخزن: hosseinasgari898989-dev/rubika-agent
- Worker: https://rubika-agent.h98609294.workers.dev/
- D1: rubika-agent-db؛ شناسه 70634eb9-70a8-48e8-b254-903a42e93304
- کانال آزمایشی شناخته‌شده: c0EFohK0f3174377fb6fa422d3ccb230
- هرگز secret یا token را در خروجی چاپ نکن.
- هیچ کاری را موفق اعلام نکن مگر اینکه خروجی تازهٔ GitHub/Actions/D1/Worker آن را تأیید کند.
- ترجیح کاربر: فارسی، کوتاه و قابل فهم، بدون بازسازی معماری یا پرسش‌های تأیید تکراری. مسیرهای موجود را هماهنگ کن.
- انتشار واقعی باید از کانال درست عبور کند، تکراری‌ها نباید دوباره ارسال شوند و وضعیت ارسال نامشخص باید باعث توقف امن شود.

## تصمیم‌های محصول
1. اطلاع‌رسانی خصوصی سازنده باید در یک پیام روزانهٔ قابل ویرایش با edit_message_text جمع شود. مسیر agent/brain/owner_notify.py در main شناسهٔ پیام را از Worker/D1 می‌گیرد و همان پیام را ویرایش می‌کند. Workflow صف باید همین کد main را اجرا کند؛ نسخهٔ قدیمی شاخهٔ agent-brain-mega-v1 فقط send_message داشت.
2. صف ادیت TikTok: هدف چهار انتشار موفق در روز است. خطاهای قطعی نباید کل صف را متوقف کنند؛ وضعیت uncertain نباید خودکار تکرار شود. بررسی منبع، مرتبط بودن محتوا، اعتبارسنجی مدیا و ضدتکرار باقی بمانند.
3. مرز تولد/birth cutoff فقط برای خبر رسمی Mob است. برای ادیت‌ها نباید agent_lifecycle باعث رد شدن ویدئو شود. فایل‌ها config/agent_lifecycle.json و agent/lifecycle.py باید در مسیر خبر Mob اعمال شوند، نه مسیر ادیت.
4. انتشار خودکار خبر Mob مستقل از ادیت‌هاست. X از حساب رسمی @mobgamesstudios راستی‌آزمایی شود؛ وب‌سایت Mob منبع اصلی و YouTube رسمی و Steam منابع پشتیبان‌اند. خبر قابل انتشار باید زمان واقعی و تأییدشده در پنجرهٔ پنج‌ساعته داشته باشد، رسانهٔ اصلی واقعی و معتبر داشته باشد، کپشن بدون URL و ترجمهٔ فارسی سالم داشته باشد، ضدتکرار شود و تمام خبرهای واجد شرایط در پنجره پردازش شوند. پس از ارسال نامشخص توقف امن. رویداد Mob نیز باید در پیام واحد سازنده جمع شود.
5. Workers AI فقط برای تشخیص وجود موسیقی در صدای ویدئو است. ترجمهٔ خبر باید از مسیر Gemini یا مسیر مجاز کنترل‌شده باشد؛ تغییر کد به‌تنهایی اثبات Deploy یا Migration نیست.
6. Gemini باید با یک درخواست کنترل‌شده خطاهای تاریخی را تحلیل کند. خطاهای provider در ledger باید کد واقعی و امن داشته باشند؛ وضعیت unknown از ستون outcome معلوم است و نباید error_code عمومی اطلاعات تشخیصی را پاک کند. تحلیل AI مجوز patch خودکار یا ادغام نیست.
7. Groq و OpenRouter را بدون آزمایش مستقل Gemini حذف نکن. در ترکیب قبلی برای diagnose/review هستند؛ خطای rate-limit یا خروجی نامشخص‌شان می‌تواند کل تعمیر را متوقف کند. با شواهد واقعی و وضعیت هزینه/quota تصمیم بگیر.

## مسیرهای مهم
- .github/workflows/daily-queue-processor.yml — اجرای صف ادیت؛ سقف روزانه و گزارش خصوصی.
- .github/workflows/owner-daily-digest.yml — شروع پیام گزارش ۲۴ ساعته.
- .github/workflows/owner-digest-refresh.yml — تازه‌سازی پیام واحد از رویدادها.
- agent/brain/owner_notify.py — ثبت رخداد، ساخت پیام واحد و ویرایش پیام قبلی.
- agent/brain/queue_worker.py — پردازش صف و انتشار ادیت؛ birth cutoff مربوط به Mob نباید اجرا شود.
- agent/brain/queue_intake.py — اعتبارسنجی metadata و افزودن به D1.
- termux/daily_discovery.py و .github/workflows/termux-docker-discovery.yml — کشف metadata؛ Workflow هر حدود پنج ساعت گردآوری کامل می‌کند و چیزی منتشر نمی‌کند.
- agent/brain/mob_official_trial.py — کشف، ترجمه، اعتبارسنجی مدیا و انتشار خبر رسمی Mob.
- agent/discovery/termux_mob_x.py — اعتبارسنجی حساب رسمی، زمان واقعی X و مدیای اصلی.
- agent/discovery/mob_official_complete.py — ترکیب سایت رسمی، X، YouTube و Steam با حذف موارد تکراری.
- config/mob_official_sources.json — منابع رسمی و وضعیت انتشار Mob.
- config/agent_lifecycle.json و agent/lifecycle.py — مرز شروع که باید فقط برای خبر رسمی Mob اعمال شود.
- agent/brain/ai_provider_clients.py و agent/brain/gemini_usage_client.py — فراخوانی provider و ثبت بودجه/نتیجه در D1.
- .github/workflows/gemini-only-live-smoke.yml — آزمایش محدود Gemini و تحلیل ساختاریافته.
- agent/brain/error_supervisor.py — قبلاً فقط راهنمای محلی deterministic بود؛ Gemini فعال نیست مگر کد مسیر واقعی آن را نشان دهد.
- src/index.js — کد Worker؛ از Cloudflare نسخهٔ زنده/health را بررسی کن.

## شواهد واقعی آخرین ممیزی
- main قبل از این اصلاح روی SHA a9a707effe4ea0d2313e5bc133138e5f091009ed بود؛ بعد از commitها SHA فعلی را از API بخوان.
- Worker نسخهٔ ۶۴، health موفق و D1 متصل داشت؛ API status مقدار publishing=false نشان می‌داد.
- Run انتشار ادیت 38898409972 در 2026-10-16 موفق شد و ویدئوی https://www.tiktok.com/@menezxl9/video/7696229912578985237 را فرستاد. گزارش فایل خروجی MP4 را حدود ۹٫۷ MB ثبت کرد؛ افت کیفیت هنوز با bitrate/رزولوشن منبع و خروجی مقایسه نشده است.
- همان اجرا یک انتشار موفق داشت، workflow با daily-limit 1 اجرا شد و صف ۶۴ مورد انتظار نشان داد.
- D1 در 2026-10-15: Gemini diagnose چهار call با status unknown و error_code عمومی provider_result_unknown؛ Gemini propose_patch یک موفق و یک unknown؛ Groq diagnose دو موفق؛ OpenRouter یک موفق و یک unknown؛ Mistral دو rate_limited. علت دقیق خطای تاریخی Gemini از این داده‌ها به‌تنهایی قابل بازیابی نیست.
- GitHub Runs 38893646505 و 38886008170 با نام فایل workflow و failure برگشتند، اما API برایشان zero jobs/بدون logs نشان داد؛ این احتمال خطای پیکربندی Workflow را مطرح می‌کند اما HTTP error واقعی Gemini را ثابت نمی‌کند.
- PR #29 (Workers AI فقط موسیقی/Gemini translation) و #30 (code repair/GitHub Models) در ممیزی قبلی draft بودند؛ وضعیت را دوباره بخوان، تغییر تکراری نساز.
- rubika-main.zip در کتابخانه هنگام بررسی پیدا نشد. اگر لازم است از API کتابخانه استفاده شود، ابتدا فایل را واقعاً پیدا و باز کن.

## چک‌لیست ادامه
1. بازخوانی Main head، PRها و runهای جدید.
2. بررسی نتیجهٔ CI/test واقعی؛ شکست‌ها را رفع کن.
3. اجرای Gemini با حداکثر یک درخواست، ledger و error code دقیق؛ نتیجهٔ واقعی و تشخیص را بخوان.
4. پس از تأیید Gemini، ترجمهٔ Mob را از Workers AI جدا کن و هر Migration/Deploy لازم را واقعاً کنترل کن.
5. سیستم Mob: timestamp تأییدشدهٔ پنج ساعت اخیر، رسانهٔ اصلی، کپشن/ترجمه، ضدتکرار، چند خبر در همان پنجره و پیام واحد مالک.
6. در پایان فقط ادعاهایی را بیان کن که تست، Run یا Deployment واقعی ثابت کرده است.

## دستور شروع در گفتگوی تازه
«این سند را بخوان؛ سپس وضعیت فعلی main، PRها، CI، Worker version/health و جدول‌های D1 را بررسی کن. تغییرات تأییدشده را تکرار نکن، مسیر موازی نساز و secretها را چاپ نکن. کارهای باقی‌مانده را از وضعیت واقعی ادامه بده.»

## به‌روزرسانی زنده پس از ادغام PR #31 — 2026-10-16
- PR #31 ادغام شد؛ SHA اصلی آن پس از ادغام `8730cbd50f48d934134f02a71d73b398a0f58a73` بود. سپس دو اصلاح مستقیم کوچک در main انجام شد: جابه‌جایی پیام شروع صف به بعد از نصب وابستگی‌ها (برای اینکه `rubka` نصب شده باشد) و بازگرداندن gate انتشار Mob؛ SHA فعلی را همیشه از GitHub تازه بگیر.
- هر دو CI پیش از ادغام PR #31 سبز بودند: Agent brain checks run `38909973302` و Cloud bootstrap checks `38909973047`. Bootstrap checks پس از hotfix پیام شروع run `38910430626` نیز موفق شد. برای commit بعدی gate انتشار Mob، run `38910593198` در آخرین بررسی تکمیل و موفق بود.
- آزمایش واقعی Gemini در run `38910142359` و تلاش مجدد همان job هر دو در مرحلهٔ درخواست Gemini خطا دادند: HTTP 503، reason `UNAVAILABLE`، کد امن `provider_http_503` و پیام اینکه مدل فعلاً تقاضای بالایی دارد. این نشان می‌دهد کلید/ledger از مرحلهٔ بررسی تنظیمات عبور کردند، اما **مدل پاسخ تحلیلی نداد**؛ پس علت تاریخی واقعی هنوز از طرف Gemini تأیید نشده است. مدل `gemini-3.8-flash` در مستندات جاری Google مدل پایدار است؛ 503 به‌تنهایی دلیل مدل نامعتبر یا خطای دائمی نیست. فعلاً درخواست‌های بیشتر را پشت‌سرهم نفرست.
- چون Gemini هنوز پاسخ نداده، Groq/OpenRouter را بازنشسته نکن؛ فعلاً مدرک کافی برای جایگزین شدن Gemini در repair/translation وجود ندارد.
- پیام شروع صف اکنون پس از `pip install -r requirements.txt` اجرا می‌شود تا کتابخانهٔ Rubika در دسترس باشد.
- مسیر Mob در main پنجرهٔ ۵ ساعته را با `published_at` تأییدشده برای X اعمال می‌کند و می‌تواند چند خبر را پشت‌سرهم بررسی کند؛ هر اجرای غیرمنتظره/نامشخص متوقف می‌شود. بااین‌حال انتشار فعلاً **غیرفعال می‌ماند**: `RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014` باید صریحاً `approved` شود، `config/agent_lifecycle.json` هنوز `mode=test` است، و `config/mob_official_sources.json` همچنان test-channel-only می‌باشد. همچنین تغییر ترجمه به Gemini در PR #29 هنوز جداگانه بررسی/ادغام و Worker/Migration آن مستقر نشده است. پس هنوز نباید Mob را production-ready معرفی کرد.
- Workflow قدیمی `.github/workflows/gemini-code-assistant.yml` همچنان با نام خود فایل شکست می‌خورد و API آن صفر job برمی‌گرداند؛ علت دقیق خطای راه‌اندازی آن در خروجی قابل دسترسی مشخص نشده. این workflow واقعاً مدل یا patch را اجرا نکرده؛ قبل از بازنویسی بزرگ، تشخیص اعتبار YAML/trigger آن لازم است.

## فرمان ادامه از همین checkpoint
«main فعلی و آخرین Runها را تازه بخوان. اطلاع‌رسانی تک‌پیام و سقف چهار ادیت را حفظ کن. خطای Gemini فعلی 503/UNAVAILABLE است؛ حذف Groq/OpenRouter و فعال‌سازی Mob production را تا پاسخ واقعی Gemini و ترجمهٔ کنترل‌شده انجام نده. PR #29 و وضعیت deploy/migration را بازبینی کن؛ هیچ secret را چاپ نکن.»

