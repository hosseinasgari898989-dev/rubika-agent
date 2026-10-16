# Rubika-Agent — checkpoint اصلی و به‌روز

این سند وضعیت جاری است، نه فهرست کارهای قدیمی. قبل از ادامه همیشه SHA فعلی main، Runها، وضعیت Worker/D1 و gateهای انتشار را دوباره از سرویس زنده بخوان. هیچ secret/tokenی را چاپ نکن و هیچ ارسال روبیکا را صرفاً برای آزمایش فعال نکن.

## مشخصات و مرزهای امن

- GitHub: hosseinasgari898989-dev/rubika-agent
- Worker: https://rubika-agent.h98609294.workers.dev/
- D1: rubika-agent-db، شناسه 70634eb9-70a8-48e8-b254-903a42e93304
- شناسهٔ کانال تست شناخته‌شده: c0EFohK0f3174377fb6fa422d3ccb230
- انتشار واقعی فقط بعد از تأیید مقصد اصلی و عبور از preflight read-only.
- در این دوره هیچ پیام/ویدئویی در کانال اصلی ارسال نشده است. preflight معتبر بودن Bot را تأیید کرد، اما مقصد را به‌درستی مسدود کرد چون secret مربوط به RUBIKA_CHANNEL_GUID هنوز شناسهٔ کانال تست است.
- Worker زنده آخرین بار در ممیزی روی نسخهٔ 4f72c97e-75dd-4e9c-9c73-73e149027478 با ۱۰۰٪ ترافیک بود؛ health و اتصال D1 تأیید شدند. برای وضعیت تازه‌تر، نسخهٔ فعلی را از Cloudflare دوباره بخوان.
- Lifecycle همچنان config/agent_lifecycle.json با mode=test است. آن را برای راه‌اندازی ادیت‌ها به production تغییر نده.

## موتورهای هوش مصنوعی

### Groq و OpenRouter

Gemini، Mistral و GitHub Models از مسیر فعال تعمیر کد/ترجمه خارج شده‌اند. تنظیم فعلی:

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

- GitHub Actions run 38929244036 روی SHA 226bf30ee7ab02d8f0f83fa29f49ddcf476d0a2d موفق شد.
- Run 38928819444 نیز ۳۸۴ تست Python را با موفقیت اجرا کرد؛ تست‌های Worker، بررسی سطح API کتابخانهٔ Rubka و agent.preflight self-test هم موفق شدند.
- Live provider smoke run 38924738525 یک درخواست واقعی و ledger-accounted به Groq و یک درخواست به OpenRouter را موفق ثبت کرد. یک بررسی محدود ترجمهٔ فارسی نیز موفق بود. این شواهد کارکرد همان درخواست‌ها را ثابت می‌کنند، نه دسترس‌پذیری دائمی رایگان یا موفقیت همهٔ تعمیرهای ممکن.
- D1 شامل درخواست‌های موفق Groq/OpenRouter، از جمله diagnose و یک translate_official موفق است؛ خطای قدیمی provider_text_missing نیز با کد واقعی ثبت شده است.
- تست‌های Rubka از قابلیت‌های metadata و کپشن media پشتیبانی می‌کنند؛ live post با قالب‌بندی در کانال اصلی انجام نشده است.

## مسیر ادیت‌ها و صف

- کشف ابری فعال در .github/workflows/termux-docker-discovery.yml است: gate هر ۳۰ دقیقه اجرا می‌شود و فقط پس از گذشت ۵ ساعت از intake موفق D1، Termux Docker را برای کشف metadata به‌کار می‌گیرد. این مسیر روی گوشی فرمان روزانه نمی‌خواهد و خود ویدئو دانلود یا منتشر نمی‌کند.
- گزارش metadata پس از بررسی به صف D1 اضافه می‌شود. پردازش صف در .github/workflows/daily-queue-processor.yml روزانه ساعت 07:00 UTC اجرا می‌شود و سقف آن حداکثر ۴ انتشار موفق است. برای هر آیتم اعتبارسنجی مدیا/منبع و ضدتکرار لازم است؛ وضعیت uncertain نباید خودکار دوباره ارسال شود.
- آخرین SELECT فقط‌خواندنی روی D1: queued=64, sent=3, skipped=6, failed=1. این شمارش ممکن است تغییر کند؛ قبل از هر تصمیم تازه دوباره بخوان.
- چهار انتشار روزانه یک سقف موفقیت است، نه تضمین اینکه هر روز چهار ویدئو واجد شرایط پیدا می‌شود.
- تغییر مقصد اصلی فقط از طریق secret درست RUBIKA_CHANNEL_GUID انجام شود. Workflow صف همان کانال secret را استفاده می‌کند و وقتی مقدار برابر شناسهٔ کانال تست باشد، عمداً متوقف می‌شود.

## خبرهای رسمی Mob و پنجرهٔ پنج‌ساعته

- منابع رسمی: وب‌سایت Mob در اولویت، X حساب @mobgamesstudios با راستی‌آزمایی نویسنده و زمان انتشار، YouTube رسمی و Steam به‌عنوان منابع پشتیبان.
- مسیر در agent/brain/mob_official_trial.py و .github/workflows/mob-x-news-production.yml است. پنجرهٔ پنج‌ساعته بر پایهٔ published_at تأییدشدهٔ خود خبر است، نه زمان ایجاد commit در GitHub. خبرهای واجد شرایط داخل بازه می‌توانند یکی‌یکی پردازش شوند؛ رسانهٔ واقعی و کپشن/ترجمهٔ معتبر لازم است و ارسال نامشخص باعث توقف امن می‌شود.
- اما Workflow انتشار Mob فعلاً با متغیر RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014 gate شده است؛ config/agent_lifecycle.json هنوز test است و config/mob_official_sources.json هنوز test-channel-only است. پس زمان‌بندی تعریف‌شده است ولی انتشار production خبر Mob فعال نیست.
- پیش از فعال کردن این مسیر باید مقصد اصلی واقعی را در secret RUBIKA_CHANNEL_GUID ثبت و preflight read-only را موفق کرد؛ سپس gate و تنظیمات production خبر Mob را جداگانه بازبینی و با رضایت صریح مالک فعال کرد. این کار نباید lifecycle ادیت‌ها را تغییر دهد.

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
