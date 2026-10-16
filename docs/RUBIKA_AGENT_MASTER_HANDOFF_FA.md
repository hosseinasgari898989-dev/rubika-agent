# Rubika-Agent — checkpoint اصلی

این سند وضعیت اجرایی فعلی و شاخهٔ پیشنهادی را جدا می‌کند. همیشه main/PR/Actions/Worker/D1 را دوباره بخوان. هیچ secret را چاپ نکن و چیزی را موفق اعلام نکن مگر شواهد زنده آن را ثابت کند.

## مخزن و مرزهای امن

- مخزن: hosseinasgari898989-dev/rubika-agent
- Worker: https://rubika-agent.h98609294.workers.dev/
- D1: rubika-agent-db، شناسه 70634eb9-70a8-48e8-b254-903a42e93304
- شناسهٔ کانال تست: c0EFohK0f3174377fb6fa422d3ccb230
- main قبل از PR پیشنهادی فعلی: bb04c461816616f1ebec34c0b82807a10cd580af
- PR پیشنهادی: #30، شاخهٔ fix/gemini-repair-provider-retirement-20261016؛ باز و draft است. هر بار برای SHA دقیق، GitHub را تازه بخوان.
- هیچ Worker deploy، D1 write/migration، تغییر Secret، merge یا ارسال واقعی توسط این PR انجام نشده است.
- config/agent_lifecycle.json باید mode=test بماند. سیستم تولد/birth cutoff برای ادیت‌ها ایجاد یا بازسازی نشود.

## هدف فعلی PR

1. **Gemini-only** برای تشخیص خطا و ترجمهٔ خبر رسمی؛ هر درخواست باید از D1 ledger بگذرد.
2. تشخیص خطا فقط advisory است: شواهد تأییدشده، فرضیه‌ها، موارد نامعلوم و بررسی‌های بعدی read-only. Gemini اجازهٔ تولید/اعمال patch، باز کردن PR تعمیر، merge، deploy یا انتشار ندارد. موتور قدیمی code repair و smoke تست Groq/OpenRouter غیرفعال شده‌اند و نباید درخواست AI بفرستند.
3. Workers AI فقط برای تشخیص موسیقی از صدای واقعی ویدئو با Clef Omni است. هیچ مسیر ترجمه/تشخیص خطا با env.AI.run مجاز نیست.
4. خبرهای Mob هر ۵ ساعت بدون Termux و فقط به کانال تست به‌صورت پیشنهاد زمان‌بندی می‌شوند. پنجرهٔ تازگی فقط بر اساس timestamp تأییدشدهٔ پست X یعنی published_at است، نه زمان commit/push گیت‌هاب. رسانهٔ واقعی، کپشن بدون URL، ترجمهٔ فارسی سالم و ضدتکرار لازم است. یک اجرا حداکثر ۱۲ نامزد را یکی‌یکی بررسی می‌کند و بعد از ارسال نامشخص متوقف می‌شود.
5. زمان‌بندی خبر Mob مستقل از صف ادیت است. مسیر ادیت و فایل config/agent_lifecycle.json نباید برای این پنجره تغییر کنند.

## Gemini و حدود هزینه

Gemini 3.8 Flash در یک آزمایش تاریخی HTTP 503 / UNAVAILABLE برگرداند؛ کد امن provider_http_503 ثبت شد و خروجی تشخیصی قابل استفاده نداشت. هزینهٔ استفاده از Gemini API را نباید خودکار رایگان فرض کرد. به همین علت workflowهای جدید به دو متغیر Actions نیاز دارند:

- RUBIKA_AGENT_AI_FREE_TIER_APPROVED=true
- RUBIKA_AGENT_GEMINI_ONLY_WORKER_READY=true

تا وقتی این gateها صریحاً تنظیم نشده‌اند، هیچ درخواست جدیدی به Gemini انجام نمی‌شود. هر درخواست فقط یک بار مجاز است؛ retry خودکار وجود ندارد. Worker زنده هنوز allowlist قدیمی Groq/OpenRouter دارد تا وقتی تغییر پیشنهادی به‌طور جداگانه بررسی و deploy شود. کد شاخه به‌تنهایی نشان‌دهندهٔ استقرار نیست.

## سابقهٔ خطاها: شواهد و ناشناخته‌ها

- آزمایش Gemini با run id 38910142359 به HTTP 503 / UNAVAILABLE رسید؛ دلیل موقت در دسترس نبودن مدل محتمل است، اما برای تمام خطاهای قبلی اثبات نشده.
- اجرای قدیمی 38893646505 و 38886008170 در GitHub API صفر job/log قابل دسترسی نشان دادند؛ علت داخل job نامعلوم است.
- بعضی رکوردهای قدیمی D1 فقط provider_result_unknown داشتند، بنابراین جزئیات واقعی خطا از آن رکوردها به‌تنهایی قابل بازیابی نیست.
- CI اصلی قبل از تغییرات این PR، run 38930281967 موفق بود و ۳۸۴ تست Python، تست‌های Worker، بررسی سطح API Rubka و self-test موفق گزارش شده بودند.
- یک Rubika read-only preflight احراز هویت Bot را تأیید کرد اما مقصد اصلی را مسدود کرد چون secret RUBIKA_CHANNEL_GUID هنوز برابر شناسهٔ کانال تست بود. پیام به کانال اصلی ارسال نشد.

## مسیرهای اصلی

- .github/workflows/termux-docker-discovery.yml: gate سبک هر ۳۰ دقیقه؛ جمع‌آوری metadata با Termux Docker تنها پس از ۵ ساعت از intake موفق صف D1؛ بدون دانلود و ارسال ویدئو در این مرحله.
- .github/workflows/daily-queue-processor.yml: صف ادیت، سقف چهار ارسال موفق در هر اجرا؛ وضعیت uncertain را خودکار تکرار نکن.
- .github/workflows/mob-x-news-production.yml: production خبر Mob همچنان به gate مستقل وابسته است و نباید فعال شود درحالی‌که lifecycle هنوز test است.
- .github/workflows/mob-x-news-test-autopilot.yml: PR فعلی، cron پنج‌ساعته؛ به RUBIKA_AGENT_MOB_X_TEST_AUTOPILOT_APPROVED=true نیز نیاز دارد و کانال را به GUID تست hard-lock می‌کند.
- .github/workflows/gemini-error-diagnosis.yml: PR فعلی؛ فقط در صورت تأیید هزینه و آماده بودن Worker، D1 status را می‌سنجد و حداکثر دو workflow attempt در روز را می‌پذیرد. گزارش آن patch تولید یا اعمال نمی‌کند.
- agent/brain/error_supervisor.py: قواعد محلی determinisitic + مسیر تشخیص advisory Gemini؛ خودکار چیزی را تغییر نمی‌دهد.
- agent/brain/ai_provider_clients.py و agent/brain/gemini_usage_client.py: مسیر Gemini و D1 ledger در PR.
- src/index.js: allowlist پیشنهادی Gemini با سقف ۱۲ رزرو در روز؛ هنوز مستقر نشده.
- agent/brain/rubika_publisher.py: برای کپشن قالب‌بندی‌شده، متن parserشده و metadata Rubika را با یک send می‌فرستد؛ در صورت ابهام ارسال تکرار نمی‌کند.

## کانال تست و خبر Mob

Workflow خودکار Mob تنها وقتی می‌تواند اجرا شود که هر سه متغیر زیر true باشند: RUBIKA_AGENT_MOB_X_TEST_AUTOPILOT_APPROVED، RUBIKA_AGENT_AI_FREE_TIER_APPROVED، RUBIKA_AGENT_GEMINI_ONLY_WORKER_READY. قبل از آن باید Worker با تغییرات provider allowlist مستقر و status?provider=gemini آماده بودنش را ثابت کرده باشد. هیچ تغییری به secrets یا کانال اصلی از داخل PR انجام نمی‌شود.

Production Mob همچنان به gate مستقل RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014 و تنظیمات production جداگانه نیاز دارد. این تغییرات بخشی از PR نیستند و نباید بدون تصمیم جداگانهٔ مالک انجام شوند.

## ادامهٔ امن

1. SHA فعلی main و head PR را تازه بخوان.
2. نتایج CI آخرین head PR را بررسی کن؛ هر خطا را با log واقعی رفع کن.
3. مطمئن شو provider callها همیشه قبل از invoke رزرو D1 دارند و کدهای واقعی مثل provider_http_503 پاک نمی‌شوند.
4. فایل‌های قدیمی/تاریخی D1 را برای حذف provider names دستکاری نکن؛ فقط مسیر جدید را Gemini-only کن.
5. پس از مرور و merge، فقط با تأیید جداگانهٔ مالک Worker را deploy کن. بعد از deploy، gate Worker-ready را تنظیم کن. هزینه/رایگان‌بودن Gemini باید جداگانه مشخص باشد.
6. تا وقتی gate خبر Mob فعال نشده، هیچ ادعایی دربارهٔ انتشار خودکار زنده یا ارسال چند خبر واقعی نکن.
