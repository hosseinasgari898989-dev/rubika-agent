# Rubika-Agent — CHECKPOINTS

آخرین بستهٔ آماده‌سازی: 2026-10-24. این سند از وضعیت GitHub روی `main` و فایل‌های مخزن ساخته شده است؛ در این مرحله Secretها خوانده یا تغییر داده نشده‌اند، Cloudflare/D1 تغییر نکرده و هیچ workflowی dispatch نشده است.

## 1) مبنای بررسی

- مخزن: `hosseinasgari898989-dev/rubika-agent`
- شاخهٔ پیش‌فرض: `main`
- SHA شاخهٔ `main` هنگام شروع این بسته: `1144146c9f1f65a4fc335e34acd2fed1a1eb88e2` (ادغام PR #57). در هر چت یا اقدام تازه، SHA زنده را دوباره از GitHub بخوان؛ این مقدار فقط نقطهٔ شروع بسته است.
- وضعیت فازها طبق `docs/PROGRESS.md`: فازهای ۰، ۱ و ۲ تکمیل‌اند. پیگیری کنسول مالک بعد از این فازها هنوز در حال بررسی است و تکمیل‌شده محسوب نمی‌شود.
- بکاپ پیش از PR مستنداتی این بسته: `backup/pre-checkpoint-package-20261024`، ساخته‌شده از `main` پیش از تغییر فایل‌ها.
- شاخهٔ مستندات این بسته: `docs/checkpoint-resume-package-20261024`. این PR فقط مستندات را تغییر می‌دهد؛ پس از ادغامش، SHA ادغام همین PR را در این جدول ثبت کن. SHA ادغام را حدس نزن.

## 2) فازها و PRهای ادغام‌شده

SHAهای زیر SHA ادغام ثبت‌شده توسط GitHub هستند.

| فاز/بخش | PR | SHA ادغام | نتیجه |
|---|---:|---|---|
| فاز ۰ | #40 | `aaa0ec50a385e3747e3c93f11d18932fc7a16f2b` | یکپارچه‌سازی مسیر کشف فعال روی main |
| فاز ۱ | #41 | `97578088cebcdc58fa6cf520e07fb38c84410b8c` | حذف مسیرهای کشف TikTok با منبع ناشناس و پایدارسازی گیت‌های کیفیت |
| فاز ۱ | #42 | `e3fc45cf190f7b592e9e545379e61395c13592b7` | تنظیم دقیق فیلترهای انتخاب‌شدهٔ TikTok |
| اصلاح مستندات فاز ۱ | #43 | `e9290cadee941a04542de5869f25884e28b9462c` | اصلاح وضعیت و شرح فاز ۱ |
| فاز ۲ | #44 | `d91139f255c01ec8b8416f872655fbed736e2b35` | حذف workflowهای قدیمی و بی‌اثر |
| فاز ۲ | #45 | `c855905cec91522f4182beccbd9aab7975729007` | انتقال مسیر کشف روزانه به GitHub Actions + Termux Docker و سخت‌سازی گیت‌های E2E |
| فاز ۲ | #46 | `92dd54b710204c97374810718bfdf6792dd1685a` | اصلاح قرارداد گزارش کشف ابری و ورود صف D1 |
| فاز ۲ | #47 | `8730db8d48bcb49064984e0aec761e7fdf682b72` | تکمیل راهنمای تحویل و checkpoint |
| فاز ۲ | #48 | `fa192e01bd9f2364a64c0df8800646dda7e959a2` | ثبت نتیجهٔ CI پس از ادغام فاز ۲ |
| فاز ۲c | #49 | `087a880317a8140afd790c2742e6cea696123439` | حذف Providerهای قدیمی؛ حفظ مسیر Groq/OpenRouter و ledger |
| checkpoint فاز ۲ | #50 | `01167e6837af9098ccde11fa450d259b66058227` | نهایی‌سازی checkpoint پاک‌سازی Provider |
| اصلاح سند فاز ۲ | #51 | `e622ea41aa6dd73ca705be403b54d8935b10ad40` | تفکیک SHA پیاده‌سازی از commitهای مستنداتی |
| پیگیری پس از فاز ۲ | #52 | `d4dd76ad1611ea04f97649b63d1ccdc5ba08ffab` | افزودن health check دستی و فقط‌خواندنی Groq/OpenRouter |
| پیگیری پس از فاز ۲ | #53 | `37f2eb0bfc536fad16c5b452f82569fd5877edeb` | اصلاح نصب وابستگی‌های health check |
| پیگیری Mob X | #54 | `a9c2fd2d713d8a9849ef316dafafddcfe26e2230` | اصلاح صف مبتنی بر `since_utc` و probeهای فقط‌خواندنی |
| کنسول مالک | #55 | `064f1c292d35996f561460be14b3b049c90ec535` | افزودن کیپد کنسول و حالت دریافت لینک X |
| کنسول مالک | #56 | `a8548576dcd164cc1140c5bc2bddc15437849e79` | تلاش برای استفاده از chat ID به‌عنوان sender ID؛ فرض نادرست در دادهٔ واقعی |
| کنسول مالک | #57 | `1144146c9f1f65a4fc335e34acd2fed1a1eb88e2` | افزودن تشخیص‌های بی‌خطر برای رویدادهای نادیده‌گرفته‌شده |

## 3) وضعیت جاری و قدم بعدی

### کنسول خصوصی مالک

- فایل اصلی: `agent/brain/owner_console.py`; workflow زمان‌بندی‌شده: `.github/workflows/owner-console-poll.yml` (هر ۵ دقیقه و امکان اجرای دستی).
- PR #55 منو/کیپد و انتظار لینک X را اضافه کرد؛ PR #56 اشتباهاً فرض کرد chat ID و sender ID یکی هستند؛ PR #57 تشخیص‌های امن را افزود، اما خودِ مشکل را حل نکرد.
- PR #59 اکنون باز است و اصلاح را در شاخهٔ `fix/owner-auth-private-chat-20261024` نگه می‌دارد. بکاپ پیش از تغییر: `backup/pre-owner-auth-20261024`. منطق پیشنهادی فقط از `RUBIKA_OWNER_CHAT_ID` استفاده می‌کند، چت خصوصی و chat ID دقیق را الزام می‌کند، sender_id را مقایسه نمی‌کند، و گروه/کانال/پیام Bot/رویدادهای ناقص یا ویرایش‌شده را رد می‌کند.
- لاگ رویداد در PR #59 فقط suffix چهارکاراکتری نوع رویداد و chat ID را می‌نویسد؛ هیچ متن پیام، sender ID یا شناسهٔ کامل ثبت نمی‌شود.
- روی head نهایی PR #59 با SHA `a12a12ee3b04f2e2e85a632620e68f29d9740b9a`، CI موفق است: Cloud bootstrap run `40062519824` و Agent brain run `40062519794`. PR هنوز ادغام نشده و این نتایج اثبات‌کنندهٔ رفتار زندهٔ Rubika نیستند.
- پس از بازبینی/تصمیم حسین دربارهٔ PR #59، health check دستی Groq/OpenRouter همچنان کار مستقلی است؛ نتیجهٔ آخر ثبت‌شده «other error» بود و ترجمه تأیید نشده. هیچ workflow انتشاری را برای آزمودن کنسول اجرا نکن.

### هوش مصنوعی و خبر رسمی Mob

- `.github/workflows/ai-health-check.yml` دستی است. طبق گزارش فعلی مالک، آخرین نتیجهٔ Groq و OpenRouter «other error» بوده و ترجمه هنوز تأیید نشده است. این health check در این بسته دوباره اجرا نشده؛ وضعیت زنده را موفق فرض نکن.
- پس از اصلاح کنسول، health check دستی را جداگانه و بدون workflow انتشار اجرا و نتیجه را ثبت کن.
- بعد از آن، PR جداگانهٔ مسیر «لینک X → تأیید لینک → ترجمه → دانلود رسانه → پیش‌نمایش → انتشار در کانال آزمایشی» لازم است. هر مرحله تست و گیت مستقل داشته باشد. تا تأیید صریح حسین، هیچ workflow انتشار را dispatch نکن و چیزی به کانال اصلی نفرست.

### مرزهای ایمنی

- مقصد آزمایش همچنان کانال تست است. هرگز GUID کامل چت یا کانال را در docs، logs، issue یا PR قرار نده.
- `daily-queue-processor.yml` زمان‌بندی فعال دارد و مسیر زمان‌بندی مقصدش را از Secret `RUBIKA_CHANNEL_GUID` می‌خواند؛ مسیر دستی آن گیت جداگانه و قفل تست دارد. این بسته مقصد یا منطق انتشار را تغییر نداده است. برای آزمون‌های بعدی مطمئن شو مقصد Secret روی تست باقی مانده، اما مقدارش را نمایش نده.
- اجرای metadata discovery با دانلود رسانه یا انتشار یکی نیست. `termux-docker-discovery.yml` زمان‌بندی دارد و می‌تواند metadata را وارد صف D1 کند؛ از این موضوع به‌عنوان اثبات انتشار موفق استفاده نکن.
- در کار آماده‌سازی حاضر، هیچ workflowی dispatch نشده، هیچ رسانه‌ای دانلود نشده، هیچ پیام Rubika ارسال نشده، هیچ Secret تغییر نکرده، هیچ Worker deploy و هیچ D1 write/migration انجام نشده است. این جمله دربارهٔ همین کار مستنداتی است، نه تضمین اینکه workflowهای برنامه‌ریزی‌شدهٔ مستقل هرگز اجرا نمی‌شوند.

## 4) کتابخانهٔ Rubka و API surface check

- فایل پیوست همین گفتگو: `rubka-main (1).zip`؛ ریشهٔ داخل آرشیو: `rubka-main/`; مسیر آن در محیط این گفتگو: `/mnt/data/rubka-main (1).zip`.
- `rubka-main/pyproject.toml` در آرشیو پیوست، نسخهٔ **8.1.6** را اعلام می‌کند. این فایل روی دیسک همین گفتگو موجود است؛ مسیر `/mnt/data` برای چت بعدی پایدار فرض نشود.
- مخزن نسخهٔ **8.1.7** را در `requirements.txt` پین کرده است. بنابراین آرشیو پیوست با نسخهٔ نصب‌شده در CI یکسان نیست.
- آرشیو را در مخزن کپی نکن؛ اکنون کد SDK داخل مخزن vendored نشده و وابستگی از طریق requirements نصب می‌شود. برای نگه‌داری مستقل از گفتگو، نسخهٔ دقیق 8.1.7 را از منبع معتبر به‌عنوان artifact جداگانه نگه‌دار یا در چت آینده دوباره پیوست کن. پیش از تغییر dependency یا vendoring، تفاوت API نسخه‌ها باید بررسی و تغییر در PR مستقل انجام شود.
- بخش‌های وابسته به SDK:
  - `agent/brain/owner_notify.py`: ساخت `rubka.Bot` و ارسال پیام خصوصی برای digest/گزارش مالک.
  - `agent/brain/rubika_publisher.py`: ارسال رسانه با `Bot`، از جمله مسیر ویدئو/تصویر/فایل، و پردازش کپشن با `rubka.metadata.Track_parsed`.
  - `agent/rubika_preflight.py` و `daily-queue-processor.yml`: شناسایی Bot و `get_chat` برای preflight بدون ارسال پیام.
  - تست‌های `tests/test_rubika_publisher.py`, `tests/test_rubika_preflight.py`, `tests/test_owner_notify.py` و تست‌های workflow، رفتارها را با mock بررسی می‌کنند.
- **Rubka API surface check** در `.github/workflows/ci.yml` بعد از نصب requirements اجرا می‌شود و نسخهٔ نصب‌شده و موجود بودن سطح API مورد انتظار را گزارش می‌کند. مرجع آن نسخهٔ 8.1.7 در requirements است، نه فایل ZIP نسخهٔ 8.1.6.
- `agent/brain/owner_console.py` خودش SDK را برای polling به‌کار نمی‌برد؛ یک REST adapter محدود به میزبان ثابت Bot API v3 دارد. allowlist فعلی آن: `getMe`, `getUpdates`, `sendMessage`, `editMessageText`, `editMessageKeypad`. payloadها باید تست شوند، اما منطق هویت فرستنده جدا از سطح API است.

## 5) GitHub Actions secrets و variables — نام‌ها فقط

این فهرست از ارجاع‌های workflowهای روی main ساخته شده، نه از خواندن مقادیر تنظیمات حساب. ممکن است Secretی در تنظیمات وجود داشته باشد ولی در main فعلی ارجاع داده نشود.

**GitHub Actions Secrets در workflowهای موجود:**

`AGENT_API_TOKEN`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `RUBIKA_TOKEN`, `RUBIKA_CHANNEL_GUID`, `RUBIKA_OWNER_CHAT_ID`, `MEGA_EMAIL`, `MEGA_PASSWORD`, `RELAY_BASE_URL`, `RELAY_API_TOKEN`.

**GitHub-provided token:** `GITHUB_TOKEN` (توکن داخلی Actions است؛ مقدارش نباید چاپ شود).

**GitHub Actions Variables ارجاع‌شده:**

`RUBIKA_AGENT_E2E_PUBLISH_GATE_20261014`, `RUBIKA_AGENT_MOB_X_PUBLISH_GATE_20261014`.

**برای PR اصلاح هویت مالک:** main پس از PR #56 از `RUBIKA_OWNER_USER_ID` در workflow کنسول استفاده نمی‌کند؛ اگر sender ID با chat ID متفاوت است، باید Secret/config مستقل را در PR اصلاحی برگرداند و فقط نام آن را در مستندات آورد. نیازی نیست مقدار این Secret در چت فرستاده شود.

## 6) Cloudflare Worker / D1 / Workers AI — نام‌ها فقط

- Worker: `rubika-agent`; کد: `src/index.js`.
- Secret مورد استفاده در Worker: `AGENT_API_TOKEN`.
- Bindingهای `wrangler.jsonc`: `DB` (D1 با نام منطقی `rubika-agent-db`) و `AI` (Workers AI).
- در `wrangler.jsonc` بلوک `vars` وجود ندارد. `PUBLISHING_ENABLED = false` یک ثابت در کد Worker است، نه Secret یا Variable حساب Cloudflare.
- workflowهای فعلی ارجاعی به Secret استقرار Cloudflare ندارند؛ این بسته هیچ deploy انجام نداده است. نام یا مقدار شناسهٔ حساب Cloudflare و شناسهٔ کامل D1 در این checkpoint تکرار نمی‌شود.

## 7) فهرست workflowهای موجود روی main

فهرست بر اساس ۲۷ فایل فعلی زیر `.github/workflows/` است. «دستی» یعنی فقط اجرای صریح از UI/dispatch؛ در صورت وجود schedule یا push، آن محرک نیز درج شده است. این جدول فهرست فایل‌ها و محرک‌های تعریف‌شده است، نه تأیید سلامت زندهٔ هر سرویس.

| فایل | محرک | وظیفه و نکتهٔ ایمنی |
|---|---|---|
| `ai-health-check.yml` | دستی | درخواست کوچک health برای Groq و OpenRouter؛ انتشار Rubika ندارد؛ نتیجهٔ زنده هنوز تأیید نشده است. |
| `brain-ci.yml` | push، PR، دستی | تست‌ها و بررسی‌های Agent Brain. |
| `brain-e2e-dispatch.yml` | دستی | آزمون انتهابه‌انتها برای discovery/صف/MEGA/D1 و مسیر انتشار batch؛ گیت صریح انتشار دارد؛ dispatch نکن مگر با تأیید جداگانه. |
| `ci.yml` | push، PR، دستی | بررسی‌های bootstrap، syntax، تست‌های Python/Worker، Rubka API surface check؛ بخش‌های live opt-in هستند. |
| `daily-queue-processor.yml` | روزانه 07:00 UTC، دستی | پردازش صف رسانه؛ حداکثر چهار انتشار موفق در اجرا. مقصد زمان‌بندی از Secret می‌آید؛ مقصد Secret باید در دورهٔ آزمایش روی کانال تست باشد. |
| `groq-openrouter-code-repair.yml` | پایان workflowهای upstream | تشخیص و پیشنهاد اصلاح محدود و ساخت PR در شکست واجد شرایط؛ merge خودکار ندارد. |
| `media-download-smoke.yml` | push، دستی | تست دانلود/اعتبارسنجی رسانه؛ انتشار به کانال جزو هدف این workflow نیست. |
| `mega-ledger-smoke.yml` | push، دستی | آزمون یکپارچگی ledger با MEGA؛ عملیات ذخیره‌سازی واقعی ممکن است انجام شود. |
| `mega-storage-smoke.yml` | push | smoke تست آپلود/دانلود MEGA؛ قبل از تغییر محرک یا اجرای عمدی، اثر ذخیره‌سازی را در diff مرور کن. |
| `mob-official-trial.yml` | دستی | آزمون تک‌پستی خبر رسمی Mob در کانال تست؛ قابل انتشار است، dispatch نکن. |
| `mob-x-discovery-probe.yml` | دستی | probe فقط‌خواندنی برای کشف X؛ دانلود/ارسال/انتشار ندارد. |
| `mob-x-free-live-check.yml` | push، دستی | بررسی زندهٔ کشف عمومی X؛ مسیر metadata است، نه تأیید انتشار. |
| `mob-x-news-production.yml` | هر ۵ ساعت، دستی | مسیر خبر رسمی X با گیت Variable و مقصد پیکربندی‌شده؛ توان انتشار دارد، dispatch نکن. |
| `multi-ai-live-smoke.yml` | دستی | درخواست زنده از Providerها؛ ممکن است ledger D1 را بنویسد؛ انتشار Rubika ندارد. |
| `owner-console-local-checks.yml` | push، PR | تست‌های آفلاین کنسول مالک. |
| `owner-console-poll.yml` | هر ۵ دقیقه، دستی | دریافت پیام/دکمهٔ کنسول خصوصی مالک؛ PR #59 اصلاح گیت چت خصوصی را پیشنهاد کرده و CI آن سبز است، ولی تا ادغام/آزمون مجاز زنده حل‌شده فرض نشود؛ انتشار کانالی ندارد. |
| `owner-daily-digest.yml` | روزانه 07:05 UTC، دستی | شروع یا ایجاد digest خصوصی مالک. |
| `owner-digest-refresh.yml` | هر ۱۵ دقیقه، دستی | به‌روزرسانی پیام digest خصوصی مالک. |
| `owner-notification-smoke.yml` | دستی | ارسال یک پیام آزمایشی خصوصی به مالک؛ فقط با اجازهٔ جداگانه اجرا شود. |
| `queue-intake.yml` | دستی | افزودن metadata کشف به صف پایدار؛ به معنی دانلود یا انتشار نیست. |
| `rubika-main-channel-preflight.yml` | push، دستی | بررسی هویت Bot و دسترس‌پذیری مقصد بدون ارسال پیام؛ مسیر preflight را با انتشار اشتباه نگیر. |
| `rubika-publish-smoke.yml` | push، دستی | آزمون انتشار رسانه در مسیر smoke؛ به‌عنوان workflow انتشار حساس در نظر بگیر و dispatch نکن. |
| `supervisor-live-smoke.yml` | push، دستی | بررسی سلامت زندهٔ Worker. |
| `termux-docker-discovery.yml` | هر نیم‌ساعت، دستی | گیت زمان‌بندی کشف ابری؛ اجرای کامل فقط پس از گذشت پنجرهٔ پنج‌ساعته، metadata و queue intake. |
| `termux-docker-smoke.yml` | push، دستی | تست سازگاری runtime Termux Docker/کشف؛ انتشار کانالی هدف آن نیست. |
| `test-uncertain-media.yml` | دستی | ارسال دوبارهٔ یک مورد نامطمئن به کانال تست؛ قابل ارسال است، dispatch نکن. |
| `video-e2e-trial.yml` | دستی | آزمون کنترل‌شدهٔ تک‌ویدئو در کانال تست؛ قابل انتشار است، dispatch نکن. |

## 8) شاخه‌های backup/* موجود

هیچ شاخه‌ای حذف نشده است. برای چند شاخهٔ قدیمی، وقتی سند دقیقِ مبدأ در main نبود، دلیل زیر از نام شاخه استنباط شده و این موضوع صریحاً مشخص شده است.

| شاخه | دلیل/محدوده |
|---|---|
| `backup/before-guard-removal-20261020` | بکاپ پیش از حذف guard زمان‌بندی کانال تست. |
| `backup/main-before-agent-brain-2026-10-15` | snapshot از main پیش از انتقال/یکپارچه‌سازی Agent Brain. |
| `backup/main-before-mob-x-production-test-20261020` | snapshot پیش از آزمون تنظیم production برای خبر Mob در مقصد تست. |
| `backup/main-before-remove-scheduled-test-channel-guard-20261020` | snapshot پیش از تغییر guard مقصد انتشار زمان‌بندی‌شده. |
| `backup/pre-cloud-cleanup-2026-10-12` | بکاپ پیش از پاک‌سازی cloud؛ دلیل دقیق از نام شاخه استنباط شده است. |
| `backup/pre-console-a-20261024` | بکاپ پیش از برش اولیهٔ کنسول مالک/PR #55. |
| `backup/pre-mob-since-20261024` | بکاپ پیش از تغییرات `since_utc` و probeهای Mob X/PR #54. |
| `backup/pre-owner-console-receive-diagnostics-20261024` | بکاپ پیش از PR #57 و افزودن تشخیص‌های امن کنسول. |
| `backup/pre-owner-id-fallback-20261024` | بکاپ پیش از PR #56. |
| `backup/pre-phase-0-20261023-2` | backup جایگزین فاز ۰؛ نام بدون پسوند از قبل وجود داشت. |
| `backup/pre-phase-0-20261023` | بکاپ پیش از پیاده‌سازی فاز ۰. |
| `backup/pre-phase-1-20261023-unknown-source` | بکاپ پیش از حذف discovery منبع ناشناس TikTok. |
| `backup/pre-phase-1-20261023` | بکاپ پیش از فاز ۱. |
| `backup/pre-phase-1-addendum-fix-20261023` | بکاپ پیش از اصلاح addendum فاز ۱. |
| `backup/pre-phase-2-20261023` | بکاپ پیش از فاز ۲. |
| `backup/pre-phase-2-20261024` | snapshot اضافی مربوط به کار فاز ۲ در ۲۴ اکتبر. |
| `backup/pre-phase-2c-20261024` | بکاپ از main پیش از تکمیل cleanup فاز ۲c/Provider. |
| `backup/pre-phase-3a-20261024` | بکاپ پیش از بستهٔ تغییرات مرحلهٔ بعد؛ رابطهٔ دقیق PR از سند فعلی به‌طور مستقل قابل اثبات نیست. |
| `backup/pre-checkpoint-package-20261024` | **بکاپی که برای بستهٔ مستندات ساخته شد**؛ snapshot از main پیش از PR مستنداتی حاضر. |
| `backup/pre-owner-auth-20261024` | بکاپ از main پیش از PR #59 برای اصلاح احراز هویت کنسول مالک. |

## 9) گام آغازین در هر ادامه

1. SHA زندهٔ `main`، وضعیت PRها و آخرین CI را از GitHub بخوان؛ این سند را وضعیت زنده فرض نکن.
2. پیش از هر PR جدید، شاخهٔ `backup/pre-<task>-<YYYYMMDD>` از main بساز؛ اگر نام تکراری است، پسوند یکتا بزن.
3. اصلاحات محدود را روی شاخهٔ feature جداگانه انجام بده، تست‌ها را اجرا کن و PR بساز؛ merge خودکار ممنوع.
4. پس از هر merge، شمارهٔ PR و SHA ادغام واقعی را به همین سند و `docs/PROGRESS.md` اضافه کن.
5. هرگز Secret/token یا شناسهٔ کامل چت/کانال را در log، تستِ خروجی‌دار، issue یا PR چاپ نکن. هیچ workflow انتشار را بدون تأیید صریح dispatch نکن.
