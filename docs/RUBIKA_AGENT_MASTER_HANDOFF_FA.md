# Rubika-Agent — checkpoint اصلی و به‌روز

این سند راهنمای معماری و ایمنی پروژه است؛ بخش‌های تاریخی آن لزوماً وضعیت زنده را نشان نمی‌دهند. آخرین snapshot ممیزی در [گزارش ممیزی 2026-10-16](RUBIKA_AGENT_AUDIT_2026-10-16.md) ثبت می‌شود. قبل از هر اقدام، SHA فعلی main، Runها، وضعیت Worker/D1 و gateهای انتشار را دوباره بخوان. هیچ secret/tokenی را چاپ نکن؛ ارسال باید به مقصدی برسد که صریحاً برای همان مرحله تأیید شده است.

## مشخصات و مرزهای امن

- GitHub: hosseinasgari898989-dev/rubika-agent
- Worker: https://rubika-agent.h98609294.workers.dev/
- D1: rubika-agent-db، شناسه 70634eb9-70a8-48e8-b254-903a42e93304
- شناسهٔ کانال تست شناخته‌شده: c0EFohK0f3174377fb6fa422d3ccb230
- انتشار واقعی فقط بعد از تأیید مقصد اصلی و عبور از preflight read-only.
- در این دوره هیچ پیام/ویدئویی در کانال اصلی ارسال نشده است. preflight معتبر بودن Bot را تأیید کرد، اما مقصد را به‌درستی مسدود کرد چون secret مربوط به RUBIKA_CHANNEL_GUID هنوز شناسهٔ کانال تست است.
- Worker زنده در نسخهٔ 4f72c97e-75dd-4e9c-9c73-73e149027478 با ۱۰۰٪ ترافیک است. در بررسی 2026-10-16، کد زنده با src/index.js در main (blob SHA: 991f15ff06416ea7ca16530a8dad0d46de155ecd) دقیقاً برابر بود؛ بنابراین برای این فایل تغییر جدیدی برای Deploy باقی نمانده. PUBLISHING_ENABLED=false فقط وضعیت قابلیت‌های Worker API را نشان می‌دهد؛ ارسال مدیا از GitHub Actions/Python انجام می‌شود.
- Lifecycle همچنان config/agent_lifecycle.json با mode=test است. آن را برای راه‌اندازی ادیت‌ها به production تغییر نده.

## موتورهای هوش مصنوعی

### Groq و OpenRouter

Gemini و Mistral از مسیر فعال تعمیر کد/ترجمه خارج شده‌اند. GitHub Models نیز از 2026-07-30 کاملاً بازنشسته شده و inference API آن در دسترس نیست؛ به همین دلیل نمی‌توان آن را به‌عنوان Provider فعال انتخاب کرد. تنظیم جاری و آزمایش‌شده همچنان Groq/OpenRouter است:

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
- SELECT فقط‌خواندنی D1 در بررسی فعلی: 34 TikTok و 30 YouTube در وضعیت queued؛ 3 TikTok sent، 6 skipped و 1 failed. مورد processing یا uncertain وجود ندارد. تحویل YouTube در config/tiktok_source_policy.json خاموش است، پس 30 مورد YouTube آمادهٔ انتشار نیستند. شمارش قبل از هر اجرا دوباره خوانده شود.
- چهار انتشار روزانه یک سقف موفقیت است، نه تضمین اینکه هر روز چهار ویدئو واجد شرایط پیدا می‌شود.
- تغییر مقصد اصلی فقط از طریق secret درست RUBIKA_CHANNEL_GUID انجام شود. Workflow صف همان کانال secret را استفاده می‌کند و وقتی مقدار برابر شناسهٔ کانال تست باشد، عمداً متوقف می‌شود.

## خبرهای رسمی Mob و پنجرهٔ پنج‌ساعته

- سیاست مالک: اخبار رسمی Mob فقط از حساب تأییدشدهٔ X با نام @mobgamesstudios گرفته و ارسال شوند. config/mob_official_sources.json فقط lane مربوط به X را فعال می‌کند؛ وب‌سایت، YouTube، Steam، TikTok و Instagram غیرفعال‌اند. نسخهٔ Termux در agent-brain-mega-v1 از discover_official_x_posts() استفاده می‌کند و گزارشش source_lanes=[x] دارد.
- مسیر انتشار X در agent/brain/mob_official_trial.py و .github/workflows/mob-x-news-production.yml است. پنجرهٔ پنج‌ساعته براساس published_at تأییدشدهٔ همان پست X است، نه زمان commit در GitHub. انتشار production همچنان پشت gate و lifecycle=test قرار دارد.
- Workflow انتشار Mob همچنان با متغیر RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014 gate شده و config/agent_lifecycle.json روی test است؛ config برای آزمون یک‌پستۀ test-channel-only است. انتشار Mob در کانال اصلی هنوز فعال نیست.

## Rubka formatting و کتابخانه

نسخهٔ CI برابر rubka==8.1.7 است. Parser داخل rubka/metadata.py قالب‌های Bold، Italic، Underline، Strike، Spoiler، Quote، Inline code، Code block و Link را به metadata تبدیل می‌کند. متدهای media سطح‌بالا metadata را عبور نمی‌دادند؛ agent/brain/rubika_publisher.py اکنون برای کپشن قالب‌بندی‌شده فایل را یک بار upload می‌کند و با sendFile متن parseشده و metadata را می‌فرستد. کپشن‌های ساده همچنان از متدهای عادی media استفاده می‌کنند؛ ارسال قالب‌بندی‌شده فقط یک بار تلاش می‌شود تا ابهام شبکه موجب duplicate نشود.
- تیتر/نام کانال کپشن ادیت با markup بولد شده است.
- خبر Mob تیتر بولد، نقل‌قول منبع و برچسب ترجمهٔ بولد دارد؛ متن منبع از تزریق markup محافظت می‌شود.
- unit testهای mock برای payloadهای Bold/Quote/Spoiler وجود دارد، ولی هنوز تأیید رسانه‌ای زنده در کانال اصلی نداریم.

## کاری که مانع انتشار اصلی است

1. در GitHub: Settings → Secrets and variables → Actions → RUBIKA_CHANNEL_GUID را به GUID واقعی کانال اصلی تغییر بده. توکن/مقدار secret را در چت نفرست. مقدار فعلی همان کانال تست است و به همین علت مسیر عمداً متوقف می‌شود.
2. اجرای workflow .github/workflows/rubika-main-channel-preflight.yml با workflow_dispatch باید تأیید کند Bot با get_me() احراز هویت می‌شود و get_chat() برای GUID اصلی موفق است؛ این preflight پیام نمی‌فرستد.
3. سپس تصمیم جداگانه دربارهٔ فعال‌سازی production برای خبرهای Mob لازم است. پیش از آن، هیچ‌کدام از guardها را با تغییر کد دور نزن.

## قواعد ادامه

- اول وضعیت تازهٔ main, Actions, Worker health/deployment, D1 counts و gateها را بخوان.
- فقط ادعاهایی را موفق اعلام کن که خروجی تازه ثابت کرده است.
- هیچ secret، raw provider response یا شناسهٔ خصوصی را در گزارش چاپ نکن.
- Worker/D1 را دستی deploy/migrate نکن؛ migration 0008 در این checkpoint قبلاً روی D1 بررسی شده است.
- هیچ PR قدیمی Gemini/Mistral/GitHub Models را صرفاً برای مرتب‌سازی merge نکن؛ تغییرات فعلی main جایگزین مسیرهای قدیمی شده‌اند.


## بررسی زندهٔ به‌روز — 2026-10-16 20:09 UTC

این بخش snapshot ممیزی همان زمان است؛ قبل از هر تغییر عملیاتی، Run و تنظیمات را دوباره بررسی کنید.

- SHA مشاهده‌شدهٔ `main`: `a55e856d6d529f618b73efa060cacd4c06e8c084`. آخرین CI اصلی ثبت‌شده، Run [38952275986](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38952275986)، موفق شد.
- آخرین اجرای کشف، Run [38959310615](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38959310615)، به‌علت یک تست ناسازگار در شاخهٔ `agent-brain-mega-v1` شکست خورد: تست هنوز فعال‌بودن منابع Mob از سایت، YouTube و Steam را انتظار داشت، درحالی‌که فقط X مجاز است. بررسی سلامت Worker و D1 پیش از تست موفق بود؛ collector، گزارش و intake به D1 اجرا نشدند.
- PR #37 این assertion را برای سیاست X-only اصلاح کرده و CI آن در Run [38960147671](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38960147671) موفق شده است. PR در زمان این snapshot باز و merge‌نشده بود.
- Run [38959343572](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38959343572) برای refresh زمان‌بندی‌شدهٔ owner digest موفق تمام شد؛ اما شواهد لاگ ثابت نمی‌کنند شاخهٔ recovery مخصوص `INVALID_ACCESS` اجرا شده باشد. آن workflow دستی دوباره اجرا نشده، چون ممکن است پیام خصوصی جدید ارسال کند.
- صف در Run [38945789415](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38945789415) سه ویدئوی TikTok را به کانال تست ارسال کرد: `sent_count=3`، `failed_count=0`، `queued_remaining=61`. این نتیجه وضعیت صف در همان اجراست، نه وضعیت امروزی همهٔ آیتم‌ها.
- Preflight کانال اصلی، Run [38945649562](https://github.com/hosseinasgari898989-dev/rubika-agent/actions/runs/38945649562)، مقصد را به‌درستی رد کرد چون شناسهٔ کانال تست تنظیم بود؛ هیچ پیام اصلی ارسال نشد. هیچ secretی نباید در چت چاپ یا درخواست شود.
- PRهای #24، #25، #28، #31، #33، #34، #35 و #36 در `main` ادغام شده‌اند. PR #28 poller خصوصی را حذف کرده؛ بنابراین کد `owner_console.py` باقی است اما polling/پاسخ‌گویی خودکار فعال نیست. PRهای #16 تا #19 در آخرین بررسی هنوز باز بودند؛ PR #20 بسته و merge‌نشده بود.
- lifecycle در حالت test است، انتشار Mob production قفل است، فقط X منبع رسمی فعال است و تحویل YouTube خاموش است.

این ممیزی هیچ deploy، migration یا نوشتن D1، تغییر secret/variable، ارسال زندهٔ جدید، اجرای دستی owner digest یا merge انجام نداده است.
