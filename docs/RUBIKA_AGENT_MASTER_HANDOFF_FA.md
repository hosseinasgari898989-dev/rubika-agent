# Rubika-Agent — checkpoint اصلی و به‌روز

این سند وضعیت جاری است، نه فهرست کارهای قدیمی. قبل از ادامه همیشه SHA فعلی main، Runها، وضعیت Worker/D1 و gateهای انتشار را دوباره از سرویس زنده بخوان. هیچ secret/tokenی را چاپ نکن؛ ارسال باید به مقصدی برسد که صریحاً برای همان مرحله تأیید شده است.

## آخرین checkpoint — 2026-10-24

- PRهای #40، #41 و #42 فازهای ۰ و ۱ را ادغام کردند؛ PR #43 وضعیت مستندات را اصلاح کرد و PR #44 پنج workflow بازنشسته/بی‌عملکرد را حذف کرد. PR #45 مسیر روزانه را روی GitHub Actions + Termux Docker یکپارچه کرد، مسیرهای مخصوص گوشی را حذف کرد و انتشار E2E را دستی و با تأیید صریح قفل کرد. PR #46 اصلاح سازگاری intake را ادغام کرد تا گزارش جدید `github_actions_termux_docker` و گزارش تاریخی `termux_phone` هر دو معتبر باشند. PR #47 اسناد checkpoint را به‌روزرسانی کرد. SHA فعلی `main` در آخرین بررسی: `8730db8d48bcb49064984e0aec761e7fdf682b72`.
- CI مربوط به PRهای #45، #46 و #47 سبز بود؛ post-merge Cloud bootstrap run `40035384915` نیز روی SHA پس از PR #46 با موفقیت تمام شد. Live Worker health و Rubika credential checks در آن اجرا skipped بودند. پس از PR #47، run `40035537961` برای main در وضعیت queued بود؛ نتیجهٔ نهایی آن را پیش از ادعای سلامت کامل دوباره بررسی کن.
- مسیر فعال کشف metadata، GitHub Actions + Termux Docker است؛ اجرای روزانه روی گوشی جزو معماری عادی نیست. راهنمای جاری: [docs/CLOUD_DISCOVERY.md](CLOUD_DISCOVERY.md). `termux/daily_discovery.py` باقی مانده چون collector داخل Docker اجرا می‌شود، اما ورودی‌های مخصوص گوشی و قابلیت push دستی گزارش حذف شده‌اند. گزارش تازه با `source=github_actions_termux_docker` ساخته می‌شود و intake از هر دو این مقدار و مقدار تاریخی `termux_phone` پشتیبانی می‌کند.
- تنظیمات فعلی `config/agent_lifecycle.json`: `mode=production` و `born_at=2026-10-20T00:00:00Z`. این مرز زمانی مخصوص خبر رسمی Mob است و به‌خودی‌خود به معنای فعال شدن کانال اصلی نیست.
- `config/mob_official_sources.json` فقط X رسمی را فعال می‌کند و پیکربندی publication آن روی مقصد ثابت کانال آزمایشی با سقف یک پست است. workflow خبر نیز گیت مستقل `RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014` دارد و GUID ثابت کانال تست را مطالبه می‌کند.
- workflow دسته‌ای E2E باید دستی اجرا شود و هم متغیر gate مخزن و هم تأیید صریح ورودی را لازم دارد؛ workflow آزمایش تک‌ویدئویی نیز دستی و با تأیید پیش‌فرض خاموش است. این workflowها به کانال اصلی هدایت نشده‌اند.
- پردازشگر زمان‌بندی‌شدهٔ ادیت مقصد را از Secret `RUBIKA_CHANNEL_GUID` می‌خواند. به‌دلیل نبود گیت مستقل تولید برای این مسیر، این Secret را صرفاً برای تست تغییر نده. پیش از هر تغییر مقصد، runbook [docs/MAIN_CHANNEL_ACTIVATION_RUNBOOK_FA.md](MAIN_CHANNEL_ACTIVATION_RUNBOOK_FA.md) را بخوان.
- در PRهای #45 و #46 هیچ workflow انتشار dispatch نشد، رسانه‌ای دانلود نشد، پیام Rubika ارسال نشد، Secret یا مقصد کانال تغییر نکرد، و Worker/D1 deploy، migrate یا تغییر داده نشد.


## مشخصات و مرزهای امن

- GitHub: hosseinasgari898989-dev/rubika-agent
- Worker: https://rubika-agent.h98609294.workers.dev/
- D1: rubika-agent-db، شناسه 70634eb9-70a8-48e8-b254-903a42e93304
- شناسهٔ کانال تست شناخته‌شده: c0EFohK0f3174377fb6fa422d3ccb230
- انتشار واقعی فقط بعد از تأیید مقصد اصلی و عبور از preflight read-only.
- در این دوره هیچ پیام/ویدئویی در کانال اصلی ارسال نشده است. preflight معتبر بودن Bot را تأیید کرد، اما مقصد را به‌درستی مسدود کرد چون secret مربوط به RUBIKA_CHANNEL_GUID هنوز شناسهٔ کانال تست است.
- Worker زنده در نسخهٔ 4f72c97e-75dd-4e9c-9c73-73e149027478 با ۱۰۰٪ ترافیک است. در بررسی 2026-10-16، کد زنده با src/index.js در main (blob SHA: 991f15ff06416ea7ca16530a8dad0d46de155ecd) دقیقاً برابر بود؛ بنابراین برای این فایل تغییر جدیدی برای Deploy باقی نمانده. PUBLISHING_ENABLED=false فقط وضعیت قابلیت‌های Worker API را نشان می‌دهد؛ ارسال مدیا از GitHub Actions/Python انجام می‌شود.
- Lifecycle فعلی config/agent_lifecycle.json برابر mode=production و born_at=2026-10-20T00:00:00Z است؛ این مرز فقط برای مسیر خبر رسمی Mob اعمال می‌شود و مقصد انتشار هنوز کانال تست است. آن را به‌عنوان مجوز تغییر مقصد کانال اصلی تفسیر نکن.

## موتورهای هوش مصنوعی

### Groq و OpenRouter

تنظیم فعال تعمیر کد و ترجمه بر پایهٔ Groq و OpenRouter است؛ فقط همین دو Provider در allowlist اجرایی و کنترل بودجهٔ D1 مجازند:

- تشخیص خطا: Groq و OpenRouter تشخیص مستقل ارائه می‌کنند.
- پیشنهاد اصلاح: Groq.
- بازبینی: Groq و OpenRouter به‌صورت مستقل.
- ترجمهٔ خبر Mob: Groq اول، OpenRouter فقط به‌عنوان fallback محدود.
- بودجهٔ D1: ۱۸ رزرو روزانه برای هر Provider و سقف مشترک ۳۶.
- نقش‌های مجاز شامل diagnose، propose_patch، review_patch، translate_official هستند. Migration با نام 0008_ai_provider_translation_role.sql برای نقش ترجمه ساخته شده و schema زنده بررسی شده است.

تمام درخواست‌ها باید از ledger در D1 عبور کنند. رزرو ناموفق یعنی درخواست ارسال نمی‌شود؛ خطاهای مبهم خودکار retry نمی‌شوند. خروجی مدل قابل اعتماد فرض نمی‌شود. اصلاح خودکار کد فقط پس از دو تشخیص، پیشنهاد محدود، دو بازبینی، allowlist و تست‌های ثابت می‌تواند PR بسازد؛ merge خودکار ندارد.

Workflow فعال: .github/workflows/groq-openrouter-code-repair.yml. این مسیر CIهای شکست‌خورده و چند workflow زمان‌بندی‌شده را می‌بیند، اما شکست‌های کلید/دسترسی کانال/سهمیه را به‌عنوان guard تشخیص می‌دهد و از تلاش برای «اصلاح کد» آن‌ها جلوگیری می‌کند. محدودیت فعلی: حداکثر سه تلاش غیر-skipped در روز.

### Cloudflare Workers AI

Workers AI فقط برای تشخیص وجود موسیقی در صدای ویدئو با مدل @cf/cloudflare/clef-omni است. نتیجهٔ نامطمئن نیازمند بازبینی انسانی است. ترجمهٔ خبر یا تعمیر کد از Workers AI استفاده نمی‌کند. مسیر قدیمی /api/brain/translate-official پاسخ 410 translation_route_retired می‌دهد. Worker کد جاری فقط یک env.AI.run(...) دارد و آن برای تشخیص موسیقی است.

### شواهد تست

- GitHub Actions run 38929770969 روی SHA 5ec9f0ab7876f0531567adcce6c1f7040f57ace7 موفق شد.
- همان Run ۳۸۴ تست Python را با موفقیت اجرا کرد؛ تست‌های Worker، بررسی سطح API کتابخانهٔ Rubka نسخهٔ 8.1.7 و agent.preflight self-test هم موفق شدند.
- Live provider smoke run 38924738525 یک درخواست واقعی و ledger-accounted به Groq و یک درخواست به OpenRouter را موفق ثبت کرد. یک بررسی محدود ترجمهٔ فارسی نیز موفق بود. این شواهد کارکرد همان درخواست‌ها را ثابت می‌کنند، نه دسترس‌پذیری دائمی رایگان یا موفقیت همهٔ تعمیرهای ممکن.
- D1 شامل درخواست‌های موفق Groq/OpenRouter، از جمله diagnose و یک translate_official موفق است؛ خطای قدیمی provider_text_missing نیز با کد واقعی ثبت شده است.
- تست‌های Rubka از قابلیت‌های metadata و کپشن media پشتیبانی می‌کنند؛ live post با قالب‌بندی در کانال اصلی انجام نشده است.

## مسیر ادیت‌ها و صف

- کشف ابری فعال در .github/workflows/termux-docker-discovery.yml است: gate هر ۳۰ دقیقه اجرا می‌شود و فقط پس از گذشت ۵ ساعت از intake موفق D1، Termux Docker را برای کشف metadata به‌کار می‌گیرد. این مسیر روی گوشی فرمان روزانه نمی‌خواهد و خود ویدئو دانلود یا منتشر نمی‌کند.
- گزارش metadata پس از بررسی به صف D1 اضافه می‌شود. پردازش صف در .github/workflows/daily-queue-processor.yml روزانه ساعت 07:00 UTC اجرا می‌شود و سقف آن حداکثر ۴ انتشار موفق است. برای هر آیتم اعتبارسنجی مدیا/منبع و ضدتکرار لازم است؛ وضعیت uncertain نباید خودکار دوباره ارسال شود.
- شمارش قبلی SELECT فقط‌خواندنی D1 (۳۴ TikTok و ۳۰ YouTube queued، ۳ TikTok sent، ۶ skipped و ۱ failed) مربوط به snapshot تاریخی 2026-10-16 است، نه شمارش زنده. پیش از تصمیم یا انتشار باید آمار صف و وضعیت processing/uncertain دوباره از D1 خوانده شود. تحویل YouTube در config/tiktok_source_policy.json خاموش است، پس نباید موارد YouTube را آمادهٔ انتشار فرض کرد.
- چهار انتشار روزانه یک سقف موفقیت است، نه تضمین اینکه هر روز چهار ویدئو واجد شرایط پیدا می‌شود.
- تغییر مقصد اصلی فقط از طریق secret درست RUBIKA_CHANNEL_GUID انجام شود. Workflow صف همان کانال secret را استفاده می‌کند و وقتی مقدار برابر شناسهٔ کانال تست باشد، عمداً متوقف می‌شود.

## خبرهای رسمی Mob و پنجرهٔ پنج‌ساعته

- سیاست مالک: اخبار رسمی Mob فقط از حساب تأییدشدهٔ X با نام @mobgamesstudios گرفته و ارسال شوند. config/mob_official_sources.json فقط lane مربوط به X را فعال می‌کند؛ وب‌سایت، YouTube، Steam، TikTok و Instagram غیرفعال‌اند. نسخهٔ Termux در agent-brain-mega-v1 از discover_official_x_posts() استفاده می‌کند و گزارشش source_lanes=[x] دارد.
- مسیر انتشار X در agent/brain/mob_official_trial.py و .github/workflows/mob-x-news-production.yml است. پنجرهٔ پنج‌ساعته براساس published_at تأییدشدهٔ همان پست X است، نه زمان commit در GitHub. انتشار production همچنان پشت gate و lifecycle=test قرار دارد.
- Workflow انتشار Mob با متغیر RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014 gate شده و config/agent_lifecycle.json از born_at تولیدی استفاده می‌کند؛ با این حال پیکربندی و workflow انتشار همچنان به شناسهٔ کانال تست ثابت و سقف یک پست محدودند. انتشار Mob در کانال اصلی فعال نیست.

## Rubka formatting و کتابخانه

نسخهٔ CI برابر rubka==8.1.7 است. Parser داخل rubka/metadata.py قالب‌های Bold، Italic، Underline، Strike، Spoiler، Quote، Inline code، Code block و Link را به metadata تبدیل می‌کند. متدهای media سطح‌بالا metadata را عبور نمی‌دادند؛ agent/brain/rubika_publisher.py اکنون برای کپشن قالب‌بندی‌شده فایل را یک بار upload می‌کند و با sendFile متن parseشده و metadata را می‌فرستد. کپشن‌های ساده همچنان از متدهای عادی media استفاده می‌کنند؛ ارسال قالب‌بندی‌شده فقط یک بار تلاش می‌شود تا ابهام شبکه موجب duplicate نشود.
- تیتر/نام کانال کپشن ادیت با markup بولد شده است.
- خبر Mob تیتر بولد، نقل‌قول منبع و برچسب ترجمهٔ بولد دارد؛ متن منبع از تزریق markup محافظت می‌شود.
- unit testهای mock برای payloadهای Bold/Quote/Spoiler وجود دارد، ولی هنوز تأیید رسانه‌ای زنده در کانال اصلی نداریم.

## کاری که مانع انتشار اصلی است

1. پیش از هر تغییر Secret، گیت مستقل برای مسیر زمان‌بندی‌شدهٔ صف ادیت‌ها اضافه و با PR جداگانه بازبینی شود. این مسیر مقصد خود را از `RUBIKA_CHANNEL_GUID` می‌گیرد؛ تغییر Secret می‌تواند انتشار زمان‌بندی‌شده را به مقصد دیگری هدایت کند.
2. پس از دریافت GUID دقیق و تأیید صاحب پروژه، workflow `.github/workflows/rubika-main-channel-preflight.yml` را با `workflow_dispatch` اجرا کن. آن فقط `get_me()` و `get_chat()` را بررسی می‌کند و نباید پیامی بفرستد.
3. فعال‌سازی خبر Mob به گیت خودش، X-only، born_at و سقف یک پست وابسته است. فقط پس از تست و تأیید جداگانهٔ صاحب پروژه، مقصد/گیت را در PR مستقل عوض کن. runbook `docs/MAIN_CHANNEL_ACTIVATION_RUNBOOK_FA.md` را مرجع ترتیب امن قرار بده.

## قواعد ادامه

- اول وضعیت تازهٔ main, Actions, Worker health/deployment, D1 counts و gateها را بخوان.
- فقط ادعاهایی را موفق اعلام کن که خروجی تازه ثابت کرده است.
- هیچ secret، raw provider response یا شناسهٔ خصوصی را در گزارش چاپ نکن.
- Worker/D1 را دستی deploy/migrate نکن؛ migration 0008 در این checkpoint قبلاً روی D1 بررسی شده است.
- مسیرهای قدیمی Provider را صرفاً برای مرتب‌سازی بازسازی نکن؛ مبنای فعال فقط کلاینت‌های Groq/OpenRouter است.


## بررسی زمان‌بندی و مقصد — 2026-10-16
- آخرین Run کشف Termux پیش از وقفه، 38913680694 در 14:10 UTC بود. آن gate موفق اجرا شد و به‌درستی جمع‌آوری را رد کرد: elapsed_seconds=17674 (حدود 4 ساعت و 55 دقیقه)؛ این رد شدن خطای کشف نبود.
- چون بعد از آن Run زمان‌بندی‌شدهٔ جدیدی دیده نمی‌شد، cron گردش‌کار Termux در commit e17d4983017d02af91a06686b93f65ae989e72ef به 7,37 * * * * تغییر و دوباره ثبت شد. برای owner digest نیز cron به 7,22,37,52 * * * * و برای Mob به 7 */5 * * * منتقل شد تا روی مرز ساعت/ربع ساعت نباشند. این‌ها اصلاح پیکربندی‌اند؛ موفقیت زمان‌بندی فقط پس از مشاهدهٔ Run جدید تأیید می‌شود.
- برنامهٔ صف ادیت‌ها هنوز برای 07:00 UTC روزانه تنظیم شده و سقف ۴ انتشار موفق دارد؛ اما workflow فعلی test-channel را رد می‌کند. آخرین preflight احراز هویت ربات را تأیید کرد ولی به‌خاطر مقصد تست هیچ پیامی نفرستاد. هیچ ارسال چهار ویدئویی در این مرحله انجام نشده است.
- PRهای قدیمیِ آزمایشی Provider بسته و ادغام‌نشده باقی می‌مانند؛ هر Provider جدید به طراحی، بررسی دسترسی و تصویب جداگانه نیاز دارد.
- انتشار خبر Mob همچنان gated است؛ اما منبع خبر در کانفیگ و Termux فقط X است. هیچ ارسال production از Mob در این دوره اثبات نشده است.
